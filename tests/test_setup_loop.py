import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers

ECHO = [sys.executable, "-c", "import sys; print(sys.stdin.read())"]
TRUE = [sys.executable, "-c", "pass"]


def closer(unit, status, files=()):
    """A fake CLI: writes each (path, text) of `files`, then last-unit.json with `status`."""
    code = "import pathlib\n"
    for path, text in files:
        code += (f"q = pathlib.Path({str(path)!r}); q.parent.mkdir(parents=True, exist_ok=True); "
                 f"q.write_text({text!r})\n")
    body = json.dumps({"status": status, "unit": "u", "summary": "s"})
    code += f"p = pathlib.Path({str(unit)!r}); p.parent.mkdir(parents=True, exist_ok=True); p.write_text({body!r})\n"
    return [sys.executable, "-c", code]


class SetupLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        helpers.isolate_git(self, self.tmp)
        self.ap = helpers.load(self.tmp)
        import repo_setup as setup
        self.setup = setup
        self.projects = [{"name": n, "path": str(helpers.git_repo(self.tmp / n)), "weekly_cap": 90}
                         for n in ("alpha", "beta")]
        self.by_name = {p["name"]: p for p in self.projects}
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5, "safety_factor": 1.5,
                                 "primary_cap": 100, "blocked_cooldown_hours": 6},
                    "notify": {"topic": "T"}, "project": self.projects}
        self.state = self.ap.load_state(self.cfg)
        self.real_process_decisions = self.ap.process_decisions
        self.ap.dispatch_decisions = lambda cfg, notify: None  # never ntfy over the network
        self.ap.process_decisions = lambda cfg, state: None
        self.ap.prepare_docker = lambda cfg, project: "ok"
        self.limits = {"primary": {"used_percent": 10, "resets_at": time.time() + 3600},
                       "secondary": {"used_percent": 40, "resets_at": time.time() + 86400}}

    def request(self, name, status="requested", **fields):
        self.setup.update(self.tmp, name, lambda cur: {**self.setup.new_request("virar um SaaS"), "status": status, **fields})

    def phase(self, name):
        return self.setup.phase(self.tmp, name)

    def root(self, name):
        return Path(self.by_name[name]["path"])

    def unit_file(self, name):
        return self.root(name) / ".superpowers" / "autopilot" / "last-unit.json"

    def pick(self):
        engine = helpers.FakeEngine(ECHO)
        project, lock, wait = self.ap.pick(self.cfg, engine, self.state, self.limits)
        if lock:
            self.ap.coord.unlock(lock)
        return project["name"] if project else None

    def run_and_close(self, name, cmd, resumable=None):
        engine = helpers.FakeEngine(cmd)
        engine.label = "Codex"
        if resumable is not None:
            engine.resumable = lambda s: resumable
        project = self.by_name[name]
        out, err, code, unit_file = self.ap.run_unit(self.cfg, engine, self.state, project)
        self.ap.after_unit(self.cfg, engine, self.state, project, out, err, code, unit_file)
        return Path(out).read_text()

    def titles(self):
        return [t for t, _ in self.ap.sent]

    # pick

    def test_review_is_skipped(self):
        self.request("alpha", "review")
        self.assertEqual(self.pick(), "beta")

    def test_requested_and_approved_ignore_idle_and_waiting(self):
        self.state["alpha"]["status"] = "idle"
        self.assertEqual(self.pick(), "beta")
        self.request("alpha")
        self.assertEqual(self.pick(), "alpha")
        self.state["alpha"]["status"] = "waiting"
        self.request("alpha", "approved")
        self.assertEqual(self.pick(), "alpha")

    def test_blocked_cooldown_still_holds_a_setup_unit(self):
        self.request("alpha")
        self.state["alpha"].update(status="blocked", blocked_until=time.time() + 3600)
        self.assertEqual(self.pick(), "beta")

    def test_exclude_is_written_once_and_before_the_clean_tree_check(self):
        for name in ("alpha", "beta"):
            (self.root(name) / ".superpowers" / "sdd").mkdir(parents=True)
            (self.root(name) / ".superpowers" / "sdd" / "ledger.md").write_text("x")
        self.request("alpha")
        self.assertEqual(self.pick(), "alpha")  # dirty before the exclude line, clean after it
        self.assertEqual(self.pick(), "alpha")
        exclude = (self.root("alpha") / ".git" / "info" / "exclude").read_text().splitlines()
        self.assertEqual(exclude.count(".superpowers/"), 1)
        self.state["alpha"]["status"] = "idle"
        self.setup.delete(self.tmp, "alpha")
        self.assertIsNone(self.pick())  # beta has no request: its tree stays dirty and untouched
        beta_exclude = self.root("beta") / ".git" / "info" / "exclude"
        self.assertNotIn(".superpowers/", beta_exclude.read_text() if beta_exclude.exists() else "")

    # prompts

    def test_draft_prompt_has_the_setup_protocol_and_section(self):
        self.request("alpha", feedback="use pnpm")
        (self.root("alpha") / self.setup.DRAFT).mkdir(parents=True)
        (self.root("alpha") / self.setup.DRAFT / "autopilot.md").write_text("x")
        prompt = self.run_and_close("alpha", ECHO)
        self.assertIn("Autopilot setup unit — project alpha", prompt)
        self.assertIn("## Draft unit", prompt)
        self.assertIn("Setup unit — mode: draft.", prompt)
        self.assertIn("virar um SaaS", prompt)
        self.assertIn("use pnpm", prompt)
        self.assertIn("Draft files already in .superpowers/autopilot/setup/: autopilot.md", prompt)
        self.assertNotIn("Do exactly ONE unit", prompt)  # not the normal protocol

    def test_apply_prompt_has_no_feedback_and_lists_the_files(self):
        self.request("alpha", "approved", feedback="FEEDBACK-GONE")
        prompt = self.run_and_close("alpha", ECHO)
        self.assertIn("Setup unit — mode: apply.", prompt)
        self.assertIn("Draft files already in .superpowers/autopilot/setup/: none", prompt)
        self.assertNotIn("FEEDBACK-GONE", prompt)

    def test_project_without_a_request_gets_the_normal_prompt(self):
        prompt = self.run_and_close("beta", ECHO)
        self.assertIn("Do exactly ONE unit", prompt)
        self.assertNotIn("Setup unit", prompt)

    def test_resume_keeps_the_setup_section(self):
        self.request("alpha")
        self.state["alpha"]["session"] = "s1"
        prompt = self.run_and_close("alpha", ECHO, resumable=True)
        self.assertIn("resumed after an interruption", prompt)
        self.assertIn("Setup unit — mode: draft.", prompt)

    # closing

    def test_draft_closes_into_review_and_tells_the_owner(self):
        self.request("alpha")
        draft = self.root("alpha") / self.setup.DRAFT / "autopilot.md"
        self.run_and_close("alpha", closer(self.unit_file("alpha"), "waiting", [(draft, "# Autopilot\n")]))
        self.assertEqual(self.phase("alpha"), "review")
        self.assertEqual(self.state["alpha"]["status"], "waiting")
        self.assertFalse(self.state["alpha"]["interrupted"])
        self.assertIsNone(self.state["alpha"]["setup"])
        self.assertIn("Rascunho do alpha pronto para revisar", self.titles())
        self.assertEqual(self.pick(), "beta")

    def test_draft_without_the_file_blocks_and_keeps_the_phase(self):
        for status in ("worked", "idle"):
            self.request("alpha")
            self.state["alpha"]["status"] = "active"
            self.run_and_close("alpha", closer(self.unit_file("alpha"), status))
            self.assertEqual(self.phase("alpha"), "requested", status)
            self.assertEqual(self.state["alpha"]["status"], "blocked", status)
        self.assertIn("alpha: bloqueado", self.titles())

    def test_apply_closes_into_done_and_the_project_is_active(self):
        self.request("alpha", "approved")
        self.state["alpha"]["status"] = "waiting"
        doc = self.root("alpha") / "docs" / "autopilot.md"
        self.run_and_close("alpha", closer(self.unit_file("alpha"), "worked", [(doc, "# Autopilot\n")]))
        self.assertEqual(self.phase("alpha"), "done")
        self.assertIsInstance(self.setup.read(self.tmp, "alpha")["done_at"], float)
        self.assertEqual(self.state["alpha"]["status"], "active")

    def test_apply_blocked_keeps_approved(self):
        self.request("alpha", "approved")
        self.run_and_close("alpha", closer(self.unit_file("alpha"), "blocked"))
        self.assertEqual(self.phase("alpha"), "approved")
        self.assertEqual(self.state["alpha"]["status"], "blocked")

    def test_a_failed_run_keeps_the_phase_and_the_unit_open(self):
        self.request("alpha")
        self.run_and_close("alpha", [sys.executable, "-c", "import sys; sys.exit(1)"])
        self.assertEqual(self.phase("alpha"), "requested")
        self.assertTrue(self.state["alpha"]["interrupted"])
        self.assertEqual(self.state["alpha"]["setup"], "draft")

    def test_request_cancelled_during_the_unit_is_not_recreated(self):
        self.request("alpha")
        draft = self.root("alpha") / self.setup.DRAFT / "autopilot.md"
        engine = helpers.FakeEngine(closer(self.unit_file("alpha"), "waiting", [(draft, "x")]))
        project = self.by_name["alpha"]
        out, err, code, unit_file = self.ap.run_unit(self.cfg, engine, self.state, project)
        self.setup.delete(self.tmp, "alpha")  # the owner cancels while the unit runs
        self.ap.after_unit(self.cfg, engine, self.state, project, out, err, code, unit_file)
        self.assertIsNone(self.phase("alpha"))
        self.assertFalse((self.tmp / "setup" / "alpha.json").exists())
        self.assertNotIn("Rascunho do alpha pronto para revisar", self.titles())
        self.assertNotEqual(self.state["alpha"]["status"], "blocked")

    # the mode of a resumed unit

    def test_a_normal_interrupted_unit_stays_normal_when_a_request_appears(self):
        self.state["beta"].update(interrupted=True, session="s1", setup=None)
        self.request("beta")
        prompt = self.run_and_close("beta", ECHO, resumable=True)
        self.assertIn("resumed after an interruption", prompt)
        self.assertNotIn("Setup unit — mode", prompt)
        self.assertIsNone(self.state["beta"]["setup"])
        self.assertEqual(self.phase("beta"), "requested")

    def test_an_interrupted_draft_stays_a_draft_and_a_cancelled_request_does_not_block(self):
        self.request("alpha")
        self.run_and_close("alpha", [sys.executable, "-c", "import sys; sys.exit(1)"])
        self.assertEqual(self.state["alpha"]["setup"], "draft")
        self.setup.delete(self.tmp, "alpha")  # the owner cancels while the unit is open
        self.state["alpha"]["status"] = "active"
        cmd = closer(self.unit_file("alpha"), "waiting")
        cmd[2] = "import sys; print(sys.stdin.read())\n" + cmd[2]
        prompt = self.run_and_close("alpha", cmd, resumable=True)
        self.assertIn("Setup unit — mode: draft.", prompt)
        self.assertIsNone(self.phase("alpha"))
        self.assertFalse(self.state["alpha"]["interrupted"])
        self.assertNotEqual(self.state["alpha"]["status"], "blocked")
        self.assertNotIn("alpha: bloqueado", self.titles())

    def test_a_tampered_request_does_not_break_the_prompt(self):
        path = self.tmp / "setup" / "alpha.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**self.setup.new_request("g"), "feedback": 5}))
        prompt = self.run_and_close("alpha", ECHO)
        self.assertNotIn("Setup unit — mode", prompt)

    def test_exclude_failure_is_logged_once_per_project(self):
        self.ap.EXCLUDE_LOGGED.clear()
        logged = []
        self.ap.log = logged.append
        real = self.setup.ensure_excluded
        self.setup.ensure_excluded = lambda path, on_error=None: on_error("boom") and False
        self.addCleanup(setattr, self.setup, "ensure_excluded", real)
        for _ in range(3):
            self.ap.exclude_setup("alpha", self.root("alpha"))
        self.assertEqual([m for m in logged if "boom" in m], ["alpha: boom"])

    def test_pick_does_not_touch_the_exclude_of_projects_that_do_not_run(self):
        self.request("beta")
        self.request("alpha")
        (self.root("beta") / ".superpowers" / "sdd").mkdir(parents=True)
        (self.root("beta") / ".superpowers" / "sdd" / "l.md").write_text("x")
        calls = []
        real = self.setup.ensure_excluded
        self.setup.ensure_excluded = lambda path, on_error=None: calls.append(str(path)) or real(path)
        self.addCleanup(setattr, self.setup, "ensure_excluded", real)
        self.assertEqual(self.pick(), "alpha")  # first in the queue, clean: no exclude call at all
        self.assertEqual(calls, [])

    # baton, cancel during docker, empty review, dirty approved

    def echo_and_close(self, status, files=()):
        cmd = closer(self.unit_file("alpha"), status, files)
        cmd[2] = f"import sys, pathlib; pathlib.Path({str(self.tmp / 'prompt.txt')!r}).write_text(sys.stdin.read())\n" + cmd[2]
        return cmd

    def test_a_draft_cut_at_the_window_end_is_resumed_as_a_draft_by_the_other_engine(self):
        self.request("alpha")
        self.state["alpha"].update(interrupted=True, session="s1", setup="draft", unit_id="u1")
        self.ap.coord.claim("alpha", "codex")
        self.ap.pass_baton(helpers.FakeEngine(TRUE, name="codex"), "alpha", self.state["alpha"])
        self.assertEqual(self.ap.coord.read_claim("alpha")["setup"], "draft")
        self.state["alpha"]["setup"] = None  # the other engine's own state knows nothing of it
        engine = helpers.FakeEngine(self.echo_and_close("waiting"), name="claude")
        engine.label = "Claude"
        self.ap.run_project(self.cfg, engine, self.state, self.by_name["alpha"], self.limits, None)
        self.assertIn("Setup unit — mode: draft.", (self.tmp / "prompt.txt").read_text())

    def test_an_old_baton_without_the_mode_is_accepted(self):
        self.ap.coord.hand_off("beta", "codex", "u2")
        self.assertNotIn("setup", self.ap.coord.read_claim("beta"))

    def test_a_request_cancelled_during_docker_does_not_run_a_normal_unit(self):
        self.request("alpha")
        self.state["alpha"]["status"] = "idle"

        def docker(cfg, project):
            self.setup.delete(self.tmp, "alpha")
            return "ok"
        self.ap.prepare_docker = docker
        engine = helpers.FakeEngine(self.echo_and_close("worked"))
        engine.label = "Fake"
        self.ap.run_project(self.cfg, engine, self.state, self.by_name["alpha"], self.limits, None)
        self.assertFalse(self.state["alpha"]["interrupted"])
        self.assertEqual(self.state["alpha"]["status"], "idle")
        self.assertIsNone(self.ap.coord.read_claim("alpha"))
        self.assertFalse(self.unit_file("alpha").exists())

    def test_a_stale_draft_file_does_not_reach_review(self):
        self.request("alpha")
        draft = self.root("alpha") / self.setup.DRAFT / "autopilot.md"
        draft.parent.mkdir(parents=True)
        draft.write_text("old")
        import os
        old = time.time() - 3600
        os.utime(draft, (old, old))
        self.run_and_close("alpha", closer(self.unit_file("alpha"), "waiting"))
        self.assertEqual(self.phase("alpha"), "requested")
        self.assertEqual(self.state["alpha"]["status"], "blocked")

    def test_an_approved_project_with_a_dirty_tree_still_runs_its_apply_unit(self):
        self.request("alpha", "approved")
        (self.root("alpha") / "docs").mkdir()
        (self.root("alpha") / "docs" / "autopilot.md").write_text("half applied")
        self.assertEqual(self.pick(), "alpha")
        self.setup.update(self.tmp, "alpha", lambda cur: {**cur, "status": "requested"})
        self.assertEqual(self.pick(), "beta")  # a draft unit still waits for a clean tree

    # around the units

    def test_a_project_in_review_stays_waiting(self):
        self.ap.process_decisions = self.real_process_decisions
        self.request("alpha", "review")
        self.state["alpha"]["status"] = "waiting"
        self.ap.process_decisions(self.cfg, self.state)
        self.assertEqual(self.state["alpha"]["status"], "waiting")
        self.setup.delete(self.tmp, "alpha")
        self.ap.process_decisions(self.cfg, self.state)
        self.assertEqual(self.state["alpha"]["status"], "active")

    def test_all_idle_is_not_done_while_a_setup_is_open(self):
        for name in ("alpha", "beta"):
            self.state[name]["status"] = "idle"
        self.request("alpha")
        self.limits["secondary"]["used_percent"] = 95  # nothing fits: pick returns None
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        self.assertIsNotNone(self.ap.run_once(self.cfg, engine, self.state))
        self.assertNotIn("Fake: fim", self.titles())


if __name__ == "__main__":
    unittest.main()
