import datetime as dt
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]
TRUE = [sys.executable, "-c", "pass"]


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.coord = self.ap.coord
        self.projects = [{"name": "my-app", "path": str(self.tmp / "my-app"), "weekly_cap": 90, "docker_group": "web"},
                         {"name": "my-api", "path": str(self.tmp / "my-api"), "weekly_cap": 90},
                         {"name": "side-project", "path": str(self.tmp / "side-project"), "weekly_cap": 90, "docker_group": "web"}]
        for p in self.projects:
            Path(p["path"]).mkdir()
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5, "safety_factor": 1.5,
                                 "primary_cap": 100, "blocked_cooldown_hours": 6},
                    "project": self.projects}
        self.state = self.ap.load_state(self.cfg)
        self.ap.is_dirty = lambda path: False
        self.ap.prepare_docker = lambda cfg, project: "ok"
        self.ap.process_decisions = lambda cfg, state: None
        self.limits = {"primary": {"used_percent": 10, "resets_at": time.time() + 3600},
                       "secondary": {"used_percent": 40, "resets_at": time.time() + 86400}}

    def pick(self, engine_name="claude"):
        engine = helpers.FakeEngine(TRUE, name=engine_name)
        project, lock, wait = self.ap.pick(self.cfg, engine, self.state, self.limits)
        if lock:
            self.coord.unlock(lock)
        return project["name"] if project else None

    def test_priority_order_without_claims(self):
        self.assertEqual(self.pick(), "my-app")

    def test_project_claimed_by_other_engine_is_skipped(self):
        self.coord.claim("my-app", "codex")
        self.assertEqual(self.pick("claude"), "my-api")

    def test_group_lock_held_elsewhere_skips_the_whole_group(self):
        held = self.coord.try_lock("web")
        self.state["my-app"]["status"] = "idle"
        self.assertEqual(self.pick(), "my-api")
        self.state["my-api"]["status"] = "idle"
        self.assertIsNone(self.pick())  # side-project is in the same group as the held lock
        self.coord.unlock(held)

    def test_handoff_goes_first_even_when_dirty(self):
        self.coord.claim("side-project", "claude")
        self.coord.hand_off("side-project", "claude")
        self.ap.is_dirty = lambda path: True
        self.assertEqual(self.pick("codex"), "side-project")

    def test_five_hour_window_skips_to_a_cheaper_project(self):
        self.limits["primary"]["used_percent"] = 62
        self.state["my-app"]["cost"]["primary"] = 28.9  # 62 + 1.5 * 28.9 > 100
        self.state["my-api"]["cost"]["primary"] = 18.5   # 62 + 1.5 * 18.5 < 100
        self.assertEqual(self.pick(), "my-api")

    def test_five_hour_window_full_waits_for_its_reset(self):
        self.limits["primary"].update(used_percent=95, resets_at=time.time() + 1800)
        engine = helpers.FakeEngine(TRUE)
        project, lock, wait = self.ap.pick(self.cfg, engine, self.state, self.limits)
        self.assertIsNone(project)
        self.assertAlmostEqual(wait, 1800 + 120, delta=5)

    def test_interrupted_unit_ignores_the_five_hour_check(self):
        self.limits["primary"]["used_percent"] = 99
        self.state["side-project"]["interrupted"] = True
        self.assertEqual(self.pick(), "side-project")

    def test_weekly_cap_blocks_new_units(self):
        self.limits["secondary"]["used_percent"] = 88  # 88 + 1.5 * 1.5 > 90
        self.assertIsNone(self.pick())

    def test_unit_cut_hands_off_and_clears_state(self):
        engine = helpers.FakeEngine(SLEEP)
        project = self.projects[1]
        self.coord.claim("my-api", "claude")
        out, err, code, unit_file = self.ap.run_unit(self.cfg, engine, self.state, project, deadline=time.time() + 1)
        self.ap.after_unit(self.cfg, engine, self.state, project, out, err, code, unit_file)
        s = self.state["my-api"]
        self.assertEqual((s["interrupted"], s["session"]), (False, None))
        claim = self.coord.read_claim("my-api")
        self.assertEqual((claim["engine"], claim["handoff_from"]), ("any", "claude"))

    def test_closed_unit_releases_the_claim(self):
        project = self.projects[1]
        unit = Path(project["path"]) / ".superpowers" / "autopilot" / "last-unit.json"
        engine = helpers.FakeEngine([sys.executable, "-c",
                                     f"import pathlib; p = pathlib.Path({str(unit)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
                                     "p.write_text('{\"status\": \"worked\", \"unit\": \"u\", \"summary\": \"s\"}')"])
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(self.state["my-api"]["status"], "active")
        self.assertIsNone(self.coord.read_claim("my-api"))
        self.assertFalse(self.state["my-api"]["interrupted"])

    def test_taking_a_handoff_starts_a_fresh_session(self):
        self.coord.claim("my-api", "codex")
        self.coord.hand_off("my-api", "codex")
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        self.state["my-api"]["session"] = "old"
        engine = helpers.FakeEngine(SLEEP)
        engine.resumable = lambda s: False
        self.ap.run_unit = lambda cfg, eng, state, project, deadline=None: (_ for _ in ()).throw(RuntimeError(project["name"]))
        with self.assertRaises(RuntimeError) as ctx:
            self.ap.run_once(self.cfg, engine, self.state)
        self.assertEqual(str(ctx.exception), "my-api")
        self.assertEqual(self.coord.read_claim("my-api")["engine"], "claude")
        self.assertTrue(self.state["my-api"]["interrupted"])
        self.assertIsNone(self.state["my-api"]["session"])

    def test_outside_window_does_not_touch_quota(self):
        engine = helpers.FakeEngine(TRUE)
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        tuesday = dt.datetime(2026, 10, 6, 10, 0)
        wait = self.ap.run_once(self.cfg, engine, self.state, win, now=tuesday)
        self.assertEqual(engine.limits_calls, 0)
        self.assertAlmostEqual(wait, (dt.datetime(2026, 10, 9, 19, 0) - tuesday).total_seconds(), delta=1)

    def test_last_minutes_wait_for_the_end(self):
        engine = helpers.FakeEngine(TRUE)
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        late = dt.datetime(2026, 10, 4, 2, 50)
        self.state["_window"]["active"] = True
        wait = self.ap.run_once(self.cfg, engine, self.state, win, now=late)
        self.assertEqual(engine.limits_calls, 0)
        self.assertAlmostEqual(wait, 600, delta=1)

    def test_window_start_and_end_are_notified_once(self):
        engine = helpers.FakeEngine(TRUE)
        engine.label = "Claude"
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        for p in self.projects:
            self.state[p["name"]]["status"] = "idle"
        self.state["my-app"]["status"] = "waiting"  # does not end the loop
        self.ap.run_once(self.cfg, engine, self.state, win, now=dt.datetime(2026, 10, 2, 19, 5))
        self.ap.run_once(self.cfg, engine, self.state, win, now=dt.datetime(2026, 10, 2, 19, 10))
        self.ap.run_once(self.cfg, engine, self.state, win, now=dt.datetime(2026, 10, 4, 3, 1))
        titles = [t for t, _ in self.ap.sent]
        self.assertEqual(titles, ["Claude: começou o fim de semana", "Claude: fim de semana encerrado"])

    def test_window_close_hands_off_an_interrupted_unit_that_is_not_running(self):
        engine = helpers.FakeEngine(TRUE)
        engine.label = "Claude"
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        self.coord.claim("my-app", "claude")
        self.state["my-app"].update(interrupted=True, session="s1")
        self.state["_window"]["active"] = True
        self.ap.run_once(self.cfg, engine, self.state, win, now=dt.datetime(2026, 10, 4, 3, 1))
        claim = self.coord.read_claim("my-app")
        self.assertEqual((claim["engine"], claim["handoff_from"]), ("any", "claude"))
        self.assertEqual((self.state["my-app"]["interrupted"], self.state["my-app"]["session"]), (False, None))
        self.assertIn("my-app", self.ap.sent[-1][1])

    def test_open_unit_found_outside_the_window_is_handed_off(self):
        engine = helpers.FakeEngine(TRUE)
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        self.coord.claim("my-api", "claude")
        self.state["my-api"]["interrupted"] = True
        self.ap.run_once(self.cfg, engine, self.state, win, now=dt.datetime(2026, 10, 6, 10, 0))
        self.assertTrue(self.coord.is_handoff("my-api"))

    def test_window_close_does_not_probe_the_quota(self):
        engine = helpers.FakeEngine(TRUE)
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        self.state["_window"]["active"] = True
        self.ap.run_once(self.cfg, engine, self.state, win, now=dt.datetime(2026, 10, 6, 10, 0))
        self.assertNotIn(0, engine.max_ages)

    def test_long_waits_inside_the_window_wake_up_at_its_end(self):
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        engine.classify = lambda out, err, code: ("usage_limit", time.time() + 48 * 3600, True)
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        self.state["_window"]["active"] = True
        now = dt.datetime.now()
        win = self.ap.schedule.Window(f"{['mon','tue','wed','thu','fri','sat','sun'][now.weekday()]} 00:00",
                                      f"{['mon','tue','wed','thu','fri','sat','sun'][now.weekday()]} 23:59", 0)
        end = win.end_of(now)
        if (end - now).total_seconds() < 120:
            self.skipTest("perto demais da meia-noite")
        wait = self.ap.run_once(self.cfg, engine, self.state, win, now=now)
        self.assertLessEqual(wait, (end - now).total_seconds() + 60)

    def test_idle_checks_do_not_fill_the_log(self):
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        for p in self.projects:
            self.state[p["name"]]["status"] = "waiting"
        self.ap.run_once(self.cfg, engine, self.state)
        log = self.ap.LOG.read_text() if self.ap.LOG.exists() else ""
        self.assertNotIn("nothing eligible", log)

    def test_finished_unit_does_not_log_the_wait(self):
        engine = helpers.FakeEngine(TRUE, limits=self.limits)
        engine.classify = lambda out, err, code: ("error", None, False)
        self.state["my-app"]["status"] = self.state["side-project"]["status"] = "idle"
        self.ap.run_once(self.cfg, engine, self.state)
        log = self.ap.LOG.read_text()
        self.assertIn("my-api: new unit", log)
        self.assertNotIn("waiting until", log)

    def test_claude_waits_when_quota_is_unknown(self):
        engine = helpers.FakeEngine(TRUE, limits={})
        engine.needs_limits = True
        self.assertEqual(self.ap.run_once(self.cfg, engine, self.state), 600)
        self.assertIsNone(self.coord.read_claim("my-app"))


if __name__ == "__main__":
    unittest.main()
