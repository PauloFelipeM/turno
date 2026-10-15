import json
import os
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import helpers

OLD_ROLE = "plan" + "-reviewer"  # the removed role, spelled so a grep for it stays clean

CODEX_SNAP = {"limit_id": "codex", "primary": {"used_percent": 99.0, "resets_at": 0},
              "secondary": {"used_percent": 50.0, "resets_at": 0}}
PREMIUM_SNAP = {"limit_id": "premium", "primary": None, "secondary": None}


class CodexEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.engines = self.ap.engines
        self.home = self.tmp / "codex-home"
        self.engine = self.engines.CodexEngine({"defaults": {}}, home=self.home)

    def session(self, *snaps):
        folder = self.home / "sessions" / "2026" / "09" / "28"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "rollout-2026-09-28T14-02-03-abc.jsonl"
        path.write_text("".join(json.dumps({"payload": {"rate_limits": s}}) + "\n" for s in snaps))
        return path

    def test_premium_snapshot_is_ignored(self):
        future = time.time() + 3600
        snap = json.loads(json.dumps(CODEX_SNAP))
        snap["primary"]["resets_at"] = snap["secondary"]["resets_at"] = future
        self.session(snap, PREMIUM_SNAP)
        used, reset = self.engines.window(self.engine.limits({}), "primary")
        self.assertEqual((used, reset), (99.0, int(future)))

    def test_retry_at_formats(self):
        self.assertIsNotNone(self.engines.retry_at("try again at 1:33 AM."))
        self.assertIsNotNone(self.engines.retry_at("try again at Oct 4th, 2026 11:51 PM."))
        self.assertIsNone(self.engines.retry_at("sem horário"))

    def test_usage_limit_uses_message_time_without_snapshot(self):
        out = self.tmp / "run.jsonl"
        out.write_text(json.dumps({"type": "error", "message": "You’ve hit your usage limit. try again at 1:33 AM."}) + "\n")
        err = self.tmp / "run.err"
        err.write_text("")
        status, reset, hard = self.engine.classify(str(out), str(err), 1)
        self.assertEqual((status, hard), ("usage_limit", True))
        self.assertEqual(reset, self.engines.retry_at("try again at 1:33 AM"))

    def test_resume_command(self):
        cfg = {"defaults": {"orchestrator_model": "m", "orchestrator_effort": "medium", "max_subagents": 2}}
        engine = self.engines.CodexEngine(cfg, home=self.home)
        engine.binary = sys.executable  # exists: does not trigger the binary search
        cmd, env = engine.command({}, {"name": "x", "path": "/tmp"}, {"session": "t1"}, True, self.tmp / "stem")
        self.assertEqual(cmd[:3], [sys.executable, "exec", "resume"])
        self.assertIn("t1", cmd)


class CodexRolesTest(unittest.TestCase):
    ROLES = {"planner": {"model": "gpt-6-astra", "effort": "xhigh"},
             "implementer": {"model": "gpt-6-sol", "effort": "medium"},
             "reviewer": {"model": "gpt-6-astra", "effort": "high"}}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.engines = self.ap.engines
        self.home = self.tmp / "codex-home"  # never the real CODEX_HOME
        self.cfg = {"defaults": {"orchestrator_model": "m", "orchestrator_effort": "medium", "max_subagents": 2,
                                 "roles": json.loads(json.dumps(self.ROLES))}}
        self.engine = self.engines.CodexEngine(self.cfg, home=self.home)
        self.engine.binary = sys.executable
        self.agents = self.home / "agents"

    def command(self):
        return self.engine.command({}, {"name": "x", "path": "/tmp"}, {"session": None}, False, self.tmp / "stem")

    def test_templates_exist_without_model_lines_or_plan_reviewer(self):
        names = sorted(p.name for p in self.engines.CODEX_AGENTS.glob("*.toml"))
        self.assertEqual(names, ["implementer.toml", "planner.toml", "reviewer.toml"])
        for name in names:
            text = (self.engines.CODEX_AGENTS / name).read_text()
            self.assertNotIn("model", text.split("developer_instructions")[0])
            self.assertNotIn(OLD_ROLE, text)

    def test_command_writes_the_roles_from_the_config(self):
        self.command()
        for role, conf in self.ROLES.items():
            data = tomllib.loads((self.agents / f"{role}.toml").read_text())
            self.assertEqual((data["name"], data["model"], data["model_reasoning_effort"]),
                             (role, conf["model"], conf["effort"]))
            self.assertTrue(data["developer_instructions"].strip())
        self.assertEqual(tomllib.loads((self.agents / "reviewer.toml").read_text())["sandbox_mode"], "read-only")

    def test_a_new_config_rewrites_and_an_unchanged_one_does_not(self):
        self.command()
        planner = self.agents / "planner.toml"
        before = planner.stat().st_mtime_ns
        self.command()
        self.assertEqual(planner.stat().st_mtime_ns, before)
        self.cfg["defaults"]["roles"]["planner"]["effort"] = "low"
        self.command()
        self.assertEqual(tomllib.loads(planner.read_text())["model_reasoning_effort"], "low")
        self.assertEqual(list(self.agents.glob("*.tmp")), [])

    def test_other_files_are_left_alone(self):
        self.agents.mkdir(parents=True)
        (self.agents / f"{OLD_ROLE}.toml").write_text("old\n")
        (self.agents / "mine.toml").write_text("mine\n")
        self.command()
        self.assertEqual((self.agents / f"{OLD_ROLE}.toml").read_text(), "old\n")
        self.assertEqual((self.agents / "mine.toml").read_text(), "mine\n")

    def test_write_failure_is_logged_and_the_unit_still_runs(self):
        self.home.mkdir()
        (self.home / "agents").write_text("a file, not a folder")
        messages = []
        self.engines.log = messages.append
        cmd, _ = self.command()
        self.assertEqual(cmd[:2], [sys.executable, "exec"])
        self.assertTrue(any("Could not write the Codex role" in m for m in messages))

    def test_effort_with_quotes_stays_valid_toml(self):
        self.cfg["defaults"]["roles"]["planner"]["effort"] = 'a"b'
        self.command()
        self.assertEqual(tomllib.loads((self.agents / "planner.toml").read_text())["model_reasoning_effort"], 'a"b')

    def test_codex_home_env_is_the_default_home(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            engine = self.engines.CodexEngine(self.cfg)
        self.assertEqual(engine.home, self.home)


class DirtyTreeTest(unittest.TestCase):
    def test_autopilot_files_do_not_make_the_tree_dirty(self):
        tmp = Path(tempfile.mkdtemp())
        ap = helpers.load(tmp)
        repo = helpers.git_repo(tmp / "repo")
        folder = repo / ".superpowers" / "autopilot" / "decisions"
        folder.mkdir(parents=True)
        (folder / "x.json").write_text("{}")
        self.assertFalse(ap.is_dirty(str(repo)))
        (repo / "README").write_text("changed\n")
        self.assertTrue(ap.is_dirty(str(repo)))


class RunUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.project = {"name": "my-api", "path": str(self.tmp)}
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5},
                    "project": [self.project]}
        self.state = self.ap.load_state(self.cfg)

    def test_unit_is_saved_as_open_before_the_process_starts(self):
        engine = helpers.FakeEngine([sys.executable, "-c",
                                     f"import json; print(json.load(open({str(self.ap.STATE)!r}))['my-api']['interrupted'])"])
        out, err, code, _ = self.ap.run_unit(self.cfg, engine, self.state, self.project)
        self.assertEqual(code, 0)
        self.assertEqual(Path(out).read_text().strip(), "True")

    def test_deadline_cuts_the_process(self):
        engine = helpers.FakeEngine([sys.executable, "-c", "import time; time.sleep(60)"])
        start = time.time()
        _, _, code, _ = self.ap.run_unit(self.cfg, engine, self.state, self.project, deadline=time.time() + 1)
        self.assertEqual(code, self.ap.CUT)
        self.assertLess(time.time() - start, 20)


if __name__ == "__main__":
    unittest.main()
