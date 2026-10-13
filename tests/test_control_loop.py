import threading
import time
import unittest

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
        self.set_ov(lambda d: (d.update(next="side-project"),
                               d.setdefault("projects", {}).setdefault("side-project", {}).update(weekly_cap={"claude": 50})))
        self.cfg["project"][0]["weekly_cap"] = 99
        self.assertEqual(self.pick_cfg(), "my-app")

    def test_next_that_is_paused_is_not_picked(self):
        self.set_ov(lambda d: (d.update(next="side-project"),
                               d.setdefault("projects", {}).setdefault("side-project", {}).update(paused=True)))
        self.assertEqual(self.pick_cfg(), "my-app")

    def test_order_override_changes_the_pick(self):
        self.set_ov(lambda d: d.update(order=["side-project", "my-api", "my-app"]))
        self.assertEqual(self.pick_cfg(), "side-project")

    def test_cap_override_blocks_a_project_the_config_allows(self):
        self.set_ov(lambda d: d.setdefault("projects", {}).setdefault("my-app", {}).update(weekly_cap={"claude": 30}))
        self.assertEqual(self.pick_cfg(), "my-api")

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


if __name__ == "__main__":
    unittest.main()
