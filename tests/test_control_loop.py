import argparse
import contextlib
import io
import json
import os
import signal
import sys
import threading
import time
import unittest
from pathlib import Path

import helpers
import overrides
import test_loop

TRUE = test_loop.TRUE


class ControlLoopTest(unittest.TestCase):
    setUp = test_loop.LoopTest.setUp  # same fixtures as test_loop, without rerunning its tests

    def set_ov(self, fn):
        overrides.update(self.tmp, fn)

    def pick_cfg(self, engine_name="claude"):
        """pick as run_once does: the overrides applied to the base config."""
        ov = overrides.read(self.tmp)
        cfg = overrides.apply(self.cfg, engine_name, ov)
        engine = helpers.FakeEngine(TRUE, name=engine_name)
        nxt = ov.get("next") if isinstance(ov.get("next"), str) else None
        project, lock, wait = self.ap.pick(cfg, engine, self.state, self.limits, next_project=nxt)
        if lock:
            self.coord.unlock(lock)
        return project["name"] if project else None

    def test_paused_project_is_skipped_in_the_second_pass(self):
        self.set_ov(lambda d: d.setdefault("projects", {}).setdefault("my-app", {}).update(paused=True))
        self.assertEqual(self.pick_cfg(), "my-api")

    def test_paused_interrupted_project_is_skipped_in_the_first_pass(self):
        self.state["my-app"]["interrupted"] = True
        self.set_ov(lambda d: d.setdefault("projects", {}).setdefault("my-app", {}).update(paused=True))
        self.assertEqual(self.pick_cfg(), "my-api")

    def test_paused_baton_is_skipped(self):
        self.coord.claim("my-app", "codex")
        self.coord.hand_off("my-app", "codex")
        self.set_ov(lambda d: d.setdefault("projects", {}).setdefault("my-app", {}).update(paused=True))
        self.assertEqual(self.pick_cfg("claude"), "my-api")

    def test_next_goes_before_a_higher_priority_project(self):
        self.set_ov(lambda d: d.update(next="side-project"))
        self.assertEqual(self.pick_cfg(), "side-project")

    def test_next_that_does_not_fit_falls_back_to_the_normal_order(self):
        self.limits["secondary"]["used_percent"] = 95
        self.set_ov(lambda d: d.update(next="side-project"))
        self.cfg["project"][2]["weekly_cap"] = 50
        self.cfg["project"][0]["weekly_cap"] = 99
        self.assertEqual(self.pick_cfg(), "my-app")

    def test_next_that_is_paused_is_not_picked(self):
        self.set_ov(lambda d: (d.update(next="side-project"),
                               d.setdefault("projects", {}).setdefault("side-project", {}).update(paused=True)))
        self.assertEqual(self.pick_cfg(), "my-app")

    def test_order_and_caps_left_in_the_overrides_do_not_change_the_pick(self):
        # They live in config.json now (tests/test_config_reload.py covers the pick following it).
        self.set_ov(lambda d: (d.update(order=["side-project", "my-api", "my-app"], primary_cap={"claude": 0}),
                               d.setdefault("projects", {}).setdefault("my-app", {}).update(weekly_cap={"claude": 30})))
        self.assertEqual(self.pick_cfg(), "my-app")

    def run_once_unit(self, **ov):
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        engine.classify = lambda out, err, code: ("error", None, False)
        self.ap.run_once(self.cfg, engine, self.state)

    def test_new_unit_on_the_next_project_removes_next(self):
        self.set_ov(lambda d: d.update(next="my-api"))
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        self.run_once_unit()
        self.assertNotIn("next", overrides.read(self.tmp))

    def test_run_once_uses_the_overrides_and_takes_next(self):
        self.set_ov(lambda d: d.update(next="side-project"))
        self.run_once_unit()
        self.assertNotIn("next", overrides.read(self.tmp))
        self.assertIn("side-project: new unit", self.ap.LOG.read_text())

    def test_resume_keeps_next(self):
        self.state["my-api"]["interrupted"] = True
        self.state["my-api"]["session"] = "s"
        self.set_ov(lambda d: d.update(next="my-api"))
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        engine.resumable = lambda s: True
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(overrides.read(self.tmp).get("next"), "my-api")

    def test_reset_request_is_applied_once(self):
        s = self.state["my-api"]
        s.update(status="blocked", blocked_until=time.time() + 9999, errors=3)
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        self.set_ov(lambda d: d.setdefault("reset", {}).update(my-api=100.0))
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        engine.classify = lambda out, err, code: ("error", None, False)
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(self.state["_resets"]["my-api"], 100.0)
        self.assertEqual(s["blocked_until"], 0)
        self.assertIn("my-api: reset from the panel", self.ap.LOG.read_text())
        s.update(status="blocked", blocked_until=time.time() + 9999, errors=3, interrupted=False)
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(s["status"], "blocked")
        self.assertEqual(s["errors"], 3)

    def test_reset_for_an_unknown_project_is_ignored(self):
        self.set_ov(lambda d: d.setdefault("reset", {}).update(ghost=5.0))
        self.state["my-app"]["status"] = self.state["my-api"]["status"] = self.state["side-project"]["status"] = "idle"
        self.ap.run_once(self.cfg, helpers.FakeEngine(TRUE, limits=self.limits), self.state)
        self.assertNotIn("ghost", self.state.get("_resets", {}))

    def test_nap_returns_early_when_the_overrides_change(self):
        threading.Timer(0.2, lambda: self.set_ov(lambda d: d.update(next="my-api"))).start()
        t = time.time()
        self.ap.nap(self.cfg, self.state, 30)
        self.assertLess(time.time() - t, 7)


    # retry_at: the wait after an error, a usage limit or a busy Docker, which waking early must not cancel

    def failing_engine(self, status="error"):
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        engine.classify = lambda out, err, code: (status, None, False)
        return engine

    def pick_wait(self, engine):
        project, lock, wait = self.ap.pick(self.cfg, engine, self.state, self.limits)
        if lock:
            self.coord.unlock(lock)
        return (project["name"] if project else None), wait

    def test_load_state_starts_retry_at_at_zero(self):
        self.assertEqual(self.state["my-api"]["retry_at"], 0)

    def test_error_backoff_holds_the_interrupted_unit_until_retry_at(self):
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        engine = self.failing_engine()
        wait = self.ap.run_once(self.cfg, engine, self.state)
        s = self.state["my-api"]
        self.assertTrue(s["interrupted"])
        self.assertAlmostEqual(s["retry_at"], time.time() + wait, delta=5)
        name, again = self.pick_wait(engine)
        self.assertIsNone(name)
        self.assertAlmostEqual(again, wait, delta=5)
        s["retry_at"] = time.time() - 1
        self.assertEqual(self.pick_wait(engine)[0], "my-api")

    def test_waking_early_three_times_does_not_rerun_a_failed_unit(self):
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        engine = self.failing_engine()
        for _ in range(3):  # three clicks on the panel: three turns in a row
            self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(self.state["my-api"]["errors"], 1)
        self.assertEqual(self.ap.LOG.read_text().count("my-api: new unit"), 1)
        self.assertFalse([t for t, _ in self.ap.sent if "erros seguidos" in t])

    def test_usage_limit_backoff_holds_the_interrupted_unit_until_retry_at(self):
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        engine = self.failing_engine("usage_limit")
        wait = self.ap.run_once(self.cfg, engine, self.state)
        s = self.state["my-api"]
        self.assertGreaterEqual(wait, 300)
        self.assertAlmostEqual(s["retry_at"], time.time() + wait, delta=5)
        self.assertIsNone(self.pick_wait(engine)[0])
        s["retry_at"] = time.time() - 1
        self.assertEqual(self.pick_wait(engine)[0], "my-api")

    def test_retry_at_also_holds_a_project_without_an_interrupted_unit(self):
        self.state["my-app"]["retry_at"] = time.time() + 300
        self.assertEqual(self.pick_cfg(), "my-api")

    def test_closing_the_unit_clears_retry_at(self):
        project = self.projects[1]
        unit = Path(project["path"]) / ".superpowers" / "autopilot" / "last-unit.json"
        engine = helpers.FakeEngine([sys.executable, "-c",
                                     f"import pathlib; p = pathlib.Path({str(unit)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
                                     "p.write_text('{\"status\": \"worked\", \"unit\": \"u\", \"summary\": \"s\"}')"],
                                    limits=self.limits)
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        self.state["my-api"].update(interrupted=True, retry_at=time.time() - 1)
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertFalse(self.state["my-api"]["interrupted"])
        self.assertEqual(self.state["my-api"]["retry_at"], 0)

    def test_other_endings_of_a_run_clear_retry_at(self):
        project = self.projects[1]
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        s = self.state["my-api"]
        missing = Path(project["path"]) / "no-last-unit.json"
        for code in (self.ap.STOPPED, 0, self.ap.CUT):  # stopped, unfinished, cut
            self.coord.claim("my-api", "claude")
            s["retry_at"] = 123.0
            self.ap.after_unit(self.cfg, engine, self.state, project, "out", "err", code, missing)
            self.assertEqual(s["retry_at"], 0, code)

    # Docker

    def test_docker_busy_lets_the_queue_move_on(self):
        self.ap.prepare_docker = lambda cfg, project: "busy" if project["name"] == "my-app" else "ok"
        engine = self.failing_engine()
        wait = self.ap.run_once(self.cfg, engine, self.state)
        self.assertLessEqual(wait, 5)
        self.assertAlmostEqual(self.state["my-app"]["retry_at"], time.time() + 600, delta=5)
        self.assertIsNone(self.coord.read_claim("my-app"))  # nothing started: the claim is released
        self.assertEqual(self.ap.sent, [])  # busy is not a Docker problem: no alert
        self.ap.run_once(self.cfg, engine, self.state)
        text = self.ap.LOG.read_text()
        self.assertIn("my-api: new unit", text)
        self.assertNotIn("my-app: new unit", text)

    def test_docker_down_still_makes_the_loop_wait_and_warns_once(self):
        self.ap.prepare_docker = lambda cfg, project: "docker-down"
        engine = self.failing_engine()
        self.assertEqual(self.ap.run_once(self.cfg, engine, self.state), 600)
        self.assertEqual(self.ap.run_once(self.cfg, engine, self.state), 600)
        self.assertEqual(len([t for t, _ in self.ap.sent if "Docker" in t]), 1)
        self.assertEqual(self.state["my-app"]["retry_at"], 0)

    def test_next_is_only_consumed_when_docker_is_ready(self):
        self.set_ov(lambda d: d.update(next="side-project"))
        self.ap.prepare_docker = lambda cfg, project: "busy"
        engine = self.failing_engine()
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(overrides.read(self.tmp).get("next"), "side-project")
        self.assertFalse(self.ap.LOG.exists() and "new unit" in self.ap.LOG.read_text())
        self.ap.prepare_docker = lambda cfg, project: "ok"
        self.state["side-project"]["retry_at"] = 0
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertNotIn("next", overrides.read(self.tmp))
        self.assertIn("side-project: new unit", self.ap.LOG.read_text())

    # waking up, the start of the loop and a broken overrides file

    def test_nap_wakes_for_a_change_made_between_the_read_and_the_nap(self):
        for p in self.projects:
            self.state[p["name"]]["status"] = "idle"
        self.ap.run_once(self.cfg, helpers.FakeEngine(TRUE, limits=self.limits), self.state)  # reads the overrides
        self.set_ov(lambda d: d.update(next="my-api"))  # a click that lands before the nap starts
        t = time.monotonic()
        self.ap.nap(self.cfg, self.state, 14)
        self.assertLess(time.monotonic() - t, 8)

    def test_unreadable_overrides_is_logged_once_per_change(self):
        for p in self.projects:
            self.state[p["name"]]["status"] = "idle"
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        count = lambda: self.ap.LOG.read_text().count("overrides.json is unreadable; ignoring it") if self.ap.LOG.exists() else 0
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(count(), 0)  # a missing file is not an unreadable one
        path = self.tmp / "overrides.json"
        path.write_text("{nope")
        self.ap.run_once(self.cfg, engine, self.state)
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(count(), 1)
        path.write_text("[1, 2]")
        os.utime(path, ns=(1, path.stat().st_mtime_ns + 1_000_000))
        self.ap.run_once(self.cfg, engine, self.state)
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(count(), 2)

    def test_cmd_run_installs_the_handlers_before_the_lock_and_clears_a_stale_phase(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))
        self.ap.set_phase(self.state, "running", "my-api")  # left behind by a loop that was killed
        self.ap.save_state(self.state)
        self.ap.load_config = lambda: self.cfg
        self.ap.engines.make_engine = lambda cfg: helpers.FakeEngine(TRUE, name="codex")
        seen = {}
        real_lock = self.ap.instance_lock

        def lock(name):
            seen["handlers"] = [signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1)]
            return real_lock(name)

        def run_once(cfg, engine, state, win=None):
            seen["phase"] = json.loads(self.ap.STATE.read_text())["_loop"]["phase"]
            return None

        class EndOfTest(Exception):
            pass

        def nap(cfg, state, secs):  # the loop no longer ends by itself: the test leaves at the first nap
            seen["nap"] = secs
            raise EndOfTest

        self.ap.instance_lock, self.ap.run_once, self.ap.nap = lock, run_once, nap
        with self.assertRaises(EndOfTest):
            self.ap.cmd_run(None)
        self.assertEqual(seen["nap"], 600)  # nothing to do is a wait, not the end of the loop
        self.assertIs(seen["handlers"][2], self.ap.request_interrupt)
        self.assertTrue(all(callable(h) and h is not signal.default_int_handler for h in seen["handlers"][:2]))
        self.assertEqual(seen["phase"], "sleeping")


    # reset clears the wait

    def test_panel_reset_clears_retry_at_and_the_project_runs_again(self):
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        self.state["my-api"].update(interrupted=True, retry_at=time.time() + 900)
        engine = self.failing_engine()
        self.assertIsNone(self.pick_wait(engine)[0])
        self.set_ov(lambda d: d.setdefault("reset", {}).update(my-api=time.time()))
        engine.classify = lambda out, err, code: ("ok", None, False)
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertIn("my-api: new unit", self.ap.LOG.read_text())
        self.assertEqual(self.state["my-api"]["retry_at"], 0)

    def test_cli_reset_clears_retry_at(self):
        self.state["my-api"].update(status="blocked", retry_at=time.time() + 900)
        self.ap.save_state(self.state)
        self.ap.load_config = lambda: self.cfg
        with contextlib.redirect_stdout(io.StringIO()):
            self.ap.cmd_reset(argparse.Namespace(name="my-api"))
        saved = json.loads(self.ap.STATE.read_text())["my-api"]
        self.assertEqual((saved["status"], saved["retry_at"]), ("active", 0))

    def test_logs_of_the_loop_are_in_english(self):
        source = (helpers.ROOT / "autopilot.py").read_text(encoding="utf-8")
        for old in ("falhou", "Docker no ar", "portas ocupadas", "erro (exit=", "tentativa", "respostas", "configure [notify]"):
            self.assertNotIn(old, source)
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        self.ap.run_once(self.cfg, self.failing_engine(), self.state)
        self.assertRegex(self.ap.LOG.read_text(), r"my-api: error \(exit=0, attempt 1; see .*\.err\)")


if __name__ == "__main__":
    unittest.main()
