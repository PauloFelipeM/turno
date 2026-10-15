import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers
from turno import overrides

TRUE = [sys.executable, "-c", "pass"]
INVALID = "config.json is invalid; keeping the last good configuration"


class ReloadTest(unittest.TestCase):
    """The loop re-reads `config.json` on every turn (`loop_turn`, the body of `cmd_run`'s loop)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.entries = []
        for name in ("alpha", "beta"):
            (self.tmp / name).mkdir()
            self.entries.append({"name": name, "path": str(self.tmp / name)})
        self.write()
        self.ap.is_dirty = lambda path: False
        self.ap.prepare_docker = lambda cfg, project: "ok"
        self.decisions = []
        self.ap.process_decisions = lambda cfg, state: self.decisions.append([p["name"] for p in cfg["project"]])
        self.limits = {"primary": {"used_percent": 10, "resets_at": time.time() + 3600},
                       "secondary": {"used_percent": 40, "resets_at": time.time() + 86400}}
        self.engine = helpers.FakeEngine(TRUE, name="codex", limits=self.limits)
        self.cfg = self.ap.load_config()
        self.state = self.ap.load_state(self.cfg)

    def write(self, entries=None, enabled=("codex", "claude"), edit=None):
        return helpers.write_config(self.tmp, self.entries if entries is None else entries, enabled, edit)

    def turn(self):
        self.cfg, wait = self.ap.loop_turn(self.cfg, self.engine, self.state)
        return wait

    def log(self):
        return self.ap.LOG.read_text() if self.ap.LOG.exists() else ""

    def names(self):
        return [p["name"] for p in self.cfg["project"]]

    # current_config

    def test_unchanged_config_is_not_reported_as_changed(self):
        cfg, changed = self.ap.current_config(self.cfg)
        self.assertEqual((cfg, changed), (self.cfg, False))

    def test_changed_config_is_returned_and_reported(self):
        self.write(self.entries[:1])
        cfg, changed = self.ap.current_config(self.cfg)
        self.assertTrue(changed)
        self.assertEqual([p["name"] for p in cfg["project"]], ["alpha"])

    def test_invalid_json_keeps_the_last_good_config_and_logs_once_per_change(self):
        helpers.write_config_text(self.tmp, "{nope")
        for _ in range(3):
            cfg, changed = self.ap.current_config(self.cfg)
            self.assertIs(cfg, self.cfg)
            self.assertFalse(changed)
        self.assertEqual(self.log().count(INVALID), 1)
        helpers.write_config_text(self.tmp, "[1, 2]")  # the file changed and is still wrong: say so again
        self.ap.current_config(self.cfg)
        self.ap.current_config(self.cfg)
        self.assertEqual(self.log().count(INVALID), 2)

    def test_config_that_fails_validation_is_refused_with_its_problems(self):
        self.write(edit=lambda d: (d.update(version=7), d["engines"]["codex"].update(safety_factor=0)))
        cfg, changed = self.ap.current_config(self.cfg)
        self.assertIs(cfg, self.cfg)
        self.assertIn(f"{INVALID}: version must be 1; engines.codex.safety_factor must be a number > 0", self.log())

    def test_a_fixed_config_is_picked_up_again(self):
        helpers.write_config_text(self.tmp, "{nope")
        self.ap.current_config(self.cfg)
        self.write(self.entries[1:])
        cfg, changed = self.ap.current_config(self.cfg)
        self.assertTrue(changed)
        self.assertEqual([p["name"] for p in cfg["project"]], ["beta"])

    def test_unreadable_config_file_keeps_the_last_good_config(self):
        (self.tmp / "config.json").unlink()
        (self.tmp / "config.json").mkdir()  # reading it raises OSError
        cfg, changed = self.ap.current_config(self.cfg)
        self.assertIs(cfg, self.cfg)
        self.assertEqual(self.log().count(INVALID), 1)

    def test_broken_secrets_file_does_not_stop_the_loop_either(self):
        (self.tmp / "secrets.toml").write_text("[notify\ntopic = ")
        cfg, changed = self.ap.current_config(self.cfg)
        self.assertIs(cfg, self.cfg)
        self.assertIn("secrets.toml is invalid; keeping the last good configuration", self.log())

    def test_the_first_load_of_the_loop_still_fails_loudly(self):
        helpers.write_config_text(self.tmp, "{nope")
        with self.assertRaises(self.ap.config.ConfigError):
            self.ap.load_config()

    # one turn of the loop

    def test_added_project_gets_a_state_entry_and_is_picked(self):
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "idle"
        (self.tmp / "gamma").mkdir()
        self.write(self.entries + [{"name": "gamma", "path": str(self.tmp / "gamma")}])
        self.assertNotIn("gamma", self.state)
        self.assertIsNotNone(self.turn())
        self.assertEqual(self.names(), ["alpha", "beta", "gamma"])
        self.assertEqual(self.state["gamma"]["cost"], {"primary": 8.0, "secondary": 1.5})
        self.assertIn("gamma: new unit", self.log())
        self.assertIn("gamma", json.loads(self.ap.STATE.read_text()))

    def test_removed_project_is_no_longer_picked_and_keeps_its_state(self):
        self.write(self.entries[1:])
        self.turn()
        self.assertIn("beta: new unit", self.log())
        self.assertNotIn("alpha: new unit", self.log())
        self.assertEqual(self.state["alpha"]["status"], "active")
        self.assertIn("alpha", json.loads(self.ap.STATE.read_text()))

    def test_removed_project_with_an_open_unit_is_not_resumed(self):
        self.state["alpha"].update(interrupted=True, session="s1")
        self.write(self.entries[1:])
        self.turn()
        self.assertNotIn("alpha:", self.log())
        self.assertTrue(self.state["alpha"]["interrupted"])  # nothing is closed behind the owner's back

    def test_new_order_in_the_config_changes_the_pick(self):
        self.write(self.entries[::-1])
        self.turn()
        self.assertIn("beta: new unit", self.log())
        self.assertNotIn("alpha: new unit", self.log())

    def test_new_cap_in_the_config_blocks_a_project(self):
        self.write(edit=lambda d: d["projects"][0].update(weekly_cap={"codex": 30}))  # usage is at 40%
        self.turn()
        self.assertIn("beta: new unit", self.log())
        self.assertNotIn("alpha: new unit", self.log())

    def test_invalid_config_keeps_the_loop_working_on_the_last_good_one(self):
        helpers.write_config_text(self.tmp, "{nope")
        self.turn()
        self.turn()
        self.assertEqual(self.names(), ["alpha", "beta"])
        self.assertIn("alpha: new unit", self.log())
        self.assertEqual(self.log().count(INVALID), 1)

    def test_model_change_reaches_the_engine(self):
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "waiting"
        self.write(edit=lambda d: d["engines"]["codex"].update(orchestrator_model="next-model", orchestrator_effort="high"))
        self.turn()
        self.assertEqual(self.engine.cfg["defaults"]["orchestrator_model"], "next-model")
        self.assertEqual(self.engine.cfg["defaults"]["orchestrator_effort"], "high")

    def test_model_change_reaches_the_command_of_a_real_engine(self):
        engine = self.ap.engines.make_engine(self.cfg)
        engine._bin = lambda: "codex"  # never looks for the real CLI
        self.write(edit=lambda d: d["engines"]["codex"].update(orchestrator_model="next-model"))
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "waiting"
        engine.limits = lambda state, max_age=1800: self.limits
        self.cfg, _ = self.ap.loop_turn(self.cfg, engine, self.state)
        cmd, _ = engine.command(self.state, self.cfg["project"][0], self.state["alpha"], False, self.tmp / "stem")
        self.assertIn("next-model", " ".join(cmd))

    def test_schedule_change_rebuilds_the_window(self):
        now = dt.datetime.now()
        days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        closed = {"start": f"{days[(now.weekday() + 2) % 7]} 10:00", "end": f"{days[(now.weekday() + 3) % 7]} 10:00",
                  "start_margin_minutes": 15}
        self.write(edit=lambda d: d["engines"]["codex"].update(schedule=closed))
        wait = self.turn()
        self.assertGreater(wait, 3600)
        self.assertEqual(self.state["_loop"]["phase"], "outside_window")
        self.assertEqual(self.engine.limits_calls, 0)  # outside the window the quota is not even queried
        self.assertNotIn("new unit", self.log())
        self.write()  # the schedule is removed again: the engine works at any time
        self.turn()
        self.assertIn("alpha: new unit", self.log())

    def test_turn_without_work_sets_the_idle_phase(self):
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "waiting"
        wait = self.turn()
        self.assertGreaterEqual(wait, 60)
        self.assertEqual(self.state["_loop"]["phase"], "sleeping")
        self.assertAlmostEqual(self.state["_loop"]["until"], time.time() + wait, delta=5)

    # the loop does not end by itself

    def fim(self):
        return [title for title, _ in self.ap.sent if title.endswith(": done")]

    def test_empty_project_list_is_a_wait_without_a_notice(self):
        self.write([])
        self.assertEqual(self.turn(), 600)
        self.assertEqual(self.turn(), 600)
        self.assertEqual(self.ap.sent, [])
        self.assertNotIn("all projects idle", self.log())
        self.assertEqual(self.state["_loop"]["phase"], "sleeping")
        self.assertFalse(self.state["_idle_notified"])

    def test_all_idle_is_a_wait_and_is_announced_once(self):
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "idle"
        self.assertEqual(self.turn(), 600)
        self.assertEqual(self.turn(), 600)
        self.assertEqual(self.fim(), ["Fake: done"])
        self.assertEqual(self.log().count("all projects idle; done"), 1)
        self.assertTrue(json.loads(self.ap.STATE.read_text())["_idle_notified"])
        self.assertAlmostEqual(self.state["_loop"]["until"], time.time() + 600, delta=5)

    def test_reset_clears_the_idle_mark_and_the_project_is_picked(self):
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "idle"
        self.turn()
        overrides.update(self.tmp, lambda d: d.setdefault("reset", {}).update(beta=time.time()))
        self.turn()
        self.assertIn("beta: new unit", self.log())
        self.assertFalse(self.state["_idle_notified"])
        self.state["beta"].update(status="idle", interrupted=False, session=None)
        self.ap.coord.release("beta", "codex")
        self.turn()
        self.assertEqual(self.fim(), ["Fake: done", "Fake: done"])  # idle again: a new notice

    def test_project_added_after_all_idle_is_picked_without_a_restart(self):
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "idle"
        self.turn()
        (self.tmp / "gamma").mkdir()
        self.write(self.entries + [{"name": "gamma", "path": str(self.tmp / "gamma")}])
        self.turn()
        self.assertIn("gamma: new unit", self.log())

    def test_run_once_still_returns_none_when_every_project_is_idle(self):
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "idle"
        self.assertIsNone(self.ap.run_once(self.cfg, self.engine, self.state))
        self.assertIsNone(self.ap.run_once(self.cfg, self.engine, self.state))
        self.assertEqual(self.fim(), ["Fake: done"])

    # malformed files

    def test_config_with_bytes_that_are_not_utf8_keeps_the_last_good_config(self):
        (self.tmp / "config.json").write_bytes(b'{"version": 1, "x": "\xff\xfe"}')
        for _ in range(2):
            cfg, changed = self.ap.current_config(self.cfg)
            self.assertIs(cfg, self.cfg)
            self.assertFalse(changed)
        self.assertEqual(self.log().count(INVALID), 1)
        self.turn()  # and a whole turn survives it
        self.assertIn("alpha: new unit", self.log())

    def test_secrets_whose_notify_is_not_a_table_are_ignored(self):
        for text in ('notify = "x"\n', "notify = 5\n", "notify = [1, 2]\n"):
            (self.tmp / "secrets.toml").write_text(text)
            cfg = self.ap.load_config()
            self.assertNotIn("topic", cfg["notify"], text)
            self.assertEqual(cfg["notify"]["server"], "https://ntfy.sh")
            got, _ = self.ap.current_config(self.cfg)
            self.assertEqual(got, cfg)
        self.assertEqual(self.log(), "")

    def test_any_value_or_type_error_while_loading_keeps_the_last_good_config(self):
        for error in (ValueError("bad value"), TypeError("bad type"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x")):
            def boom(error=error):
                raise error
            self.ap.load_config = boom
            self.ap.CONFIG_WARNED = None
            cfg, changed = self.ap.current_config(self.cfg)
            self.assertIs(cfg, self.cfg)
            self.assertFalse(changed)
        self.assertEqual(self.log().count("keeping the last good configuration"), 3)

    # disabled engine

    def test_disabled_engine_starts_nothing_and_sets_the_phase(self):
        self.write(enabled=("claude",))
        wait = self.turn()
        self.assertEqual(wait, 60)
        self.assertEqual(self.state["_loop"]["phase"], "disabled")
        self.assertEqual(json.loads(self.ap.STATE.read_text())["_loop"]["phase"], "disabled")
        self.assertEqual(self.engine.limits_calls, 0)
        self.assertNotIn("new unit", self.log())
        self.assertEqual(self.decisions, [["alpha", "beta"]])  # decisions keep flowing

    def test_disabled_engine_resumes_nothing_and_passes_the_baton(self):
        self.state["alpha"].update(interrupted=True, session="s1", unit_id="u-7")
        self.ap.coord.claim("alpha", "codex")
        self.write(enabled=("claude",))
        self.turn()
        self.assertNotIn("alpha: resuming", self.log())
        self.assertNotIn("new unit", self.log())
        claim = self.ap.coord.read_claim("alpha")
        self.assertEqual((claim["engine"], claim["handoff_from"], claim["unit_id"]), (self.ap.coord.ANY, "codex", "u-7"))
        self.assertFalse(self.state["alpha"]["interrupted"])
        self.assertIsNone(self.state["alpha"]["session"])
        # The other engine resumes it before anything else.
        project, lock, _ = self.ap.pick(self.cfg, helpers.FakeEngine(TRUE, name="claude"), self.state, self.limits)
        self.ap.coord.unlock(lock)
        self.assertEqual(project["name"], "alpha")

    def test_disabled_engine_leaves_a_unit_claimed_by_the_other_engine_alone(self):
        self.state["alpha"].update(interrupted=True, session="s1")
        self.ap.coord.claim("alpha", "claude")
        self.write(enabled=("claude",))
        self.turn()
        self.assertEqual(self.ap.coord.read_claim("alpha")["engine"], "claude")
        self.assertTrue(self.state["alpha"]["interrupted"])

    def test_disabling_and_enabling_are_logged_once_each(self):
        self.state["alpha"]["status"] = self.state["beta"]["status"] = "waiting"
        self.write(enabled=())
        for _ in range(3):
            self.turn()
        self.assertEqual(self.log().count("Fake is disabled in the config; starting nothing"), 1)
        self.assertNotIn("enabled again", self.log())
        self.write()
        for _ in range(3):
            self.turn()
        self.assertEqual(self.log().count("Fake is enabled again"), 1)
        self.write(enabled=())
        self.turn()
        self.assertEqual(self.log().count("Fake is disabled in the config; starting nothing"), 2)

    # a baton on a project this engine does not run (cap 0)

    def pick(self):
        project, lock, _ = self.ap.pick(self.cfg, self.engine, self.state, self.limits)
        if lock:
            self.ap.coord.unlock(lock)
        return project["name"] if project else None

    def zero_cap_on_alpha(self):
        self.write(edit=lambda d: d["projects"][0].update(weekly_cap={"codex": 0}))
        self.cfg, _ = self.ap.current_config(self.cfg)
        self.assertEqual(self.cfg["project"][0]["weekly_cap"], 0)

    def test_baton_on_a_project_with_cap_zero_is_not_taken(self):
        self.zero_cap_on_alpha()
        self.ap.coord.claim("alpha", "claude")
        self.ap.coord.hand_off("alpha", "claude", "u-1")
        self.assertEqual(self.pick(), "beta")
        self.state["beta"]["status"] = "idle"
        self.assertIsNone(self.pick())
        self.assertEqual(self.ap.coord.read_claim("alpha")["engine"], self.ap.coord.ANY)  # still there for Claude

    def test_baton_on_a_project_with_a_cap_is_still_taken_first(self):
        self.ap.coord.claim("beta", "claude")
        self.ap.coord.hand_off("beta", "claude", "u-1")
        self.assertEqual(self.pick(), "beta")

    def test_own_interrupted_unit_goes_first_even_with_cap_zero(self):
        self.zero_cap_on_alpha()
        self.state["alpha"].update(interrupted=True, session="s1")
        self.ap.coord.claim("alpha", "codex")
        self.assertEqual(self.pick(), "alpha")

    # status and check

    def test_status_says_when_the_engine_is_disabled(self):
        now = dt.datetime(2026, 10, 2, 20, 0)
        lines = self.ap.status_lines(self.cfg, self.engine, self.state, self.limits, None, now)
        self.assertEqual(lines[0], "engine Fake")
        self.assertFalse(any("disabled" in line for line in lines))
        self.write(enabled=())
        self.cfg, _ = self.ap.current_config(self.cfg)
        lines = self.ap.status_lines(self.cfg, self.engine, self.state, self.limits, None, now)
        self.assertEqual(lines[:2], ["engine Fake", "engine Fake: disabled in the config"])

    def check(self):
        self.ap.engines.make_engine = lambda cfg: self.engine
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.ap.cmd_check(None)
        return out.getvalue()

    def test_check_says_when_the_engine_is_disabled(self):
        self.assertIn("now: would start a unit on alpha", self.check())
        self.assertEqual(self.engine.max_ages, [0])
        self.write(enabled=())
        text = self.check()
        self.assertIn("engine Fake: disabled in the config", text)
        self.assertIn("now: the engine is disabled; no unit starts", text)
        self.assertNotIn("would start", text)
        self.assertEqual(self.engine.max_ages[-1], float("inf"))  # no fresh quota query for a disabled engine

    def test_enabling_the_engine_again_puts_it_back_to_work(self):
        self.write(enabled=())
        self.turn()
        self.write()
        self.turn()
        self.assertIn("alpha: new unit", self.log())
        self.assertNotEqual(self.state["_loop"]["phase"], "disabled")

    def test_disabled_turn_notes_the_overrides_so_the_nap_does_not_spin(self):
        self.ap.run_once(self.cfg, self.engine, self.state)  # an earlier turn read the overrides
        overrides.update(self.tmp, lambda d: d.update(next="beta"))
        self.write(enabled=())
        self.turn()
        self.assertEqual(self.ap.OVERRIDES_SEEN, self.ap.overrides_mtime())

    def test_config_without_the_enabled_key_counts_as_enabled(self):
        self.ap.load_config = lambda: {k: v for k, v in self.cfg.items() if k != "enabled"}
        self.cfg = self.ap.load_config()
        self.turn()
        self.assertIn("alpha: new unit", self.log())

    # nap

    def test_nap_returns_early_when_the_config_changes(self):
        self.ap.current_config(self.cfg)
        threading.Timer(0.3, lambda: self.write(self.entries[:1])).start()
        t = time.monotonic()
        self.ap.nap(self.cfg, self.state, 30)
        self.assertLess(time.monotonic() - t, 8)  # within one 5-second step

    def test_nap_wakes_for_a_config_change_made_between_the_load_and_the_nap(self):
        self.ap.current_config(self.cfg)
        self.write(self.entries[:1])  # a save in the panel that lands before the nap starts
        t = time.monotonic()
        with mock.patch("time.sleep"):
            self.ap.nap(self.cfg, self.state, 30)
        self.assertLess(time.monotonic() - t, 5)
        self.assertEqual(len(self.decisions), 1)

    def test_nap_wakes_when_the_config_file_appears(self):
        (self.tmp / "config.json").unlink()
        self.ap.current_config(self.cfg)  # no file and no TOML to import: the mtime noted is "missing"
        self.write()
        t = time.monotonic()
        with mock.patch("time.sleep"):
            self.ap.nap(self.cfg, self.state, 30)
        self.assertLess(time.monotonic() - t, 5)

    def test_nap_is_not_woken_by_a_config_that_did_not_change(self):
        self.ap.current_config(self.cfg)
        self.ap.read_overrides()
        with mock.patch("time.sleep"):
            self.ap.nap(self.cfg, self.state, 0.3)  # spins for 0.3 s: any false "changed" would show
        self.assertEqual(self.decisions, [])

    def test_nap_without_a_turn_before_it_starts_from_the_file_as_it_is(self):
        with mock.patch("time.sleep"):
            self.ap.nap(self.cfg, self.state, 0.3)
        self.assertEqual(self.decisions, [])

if __name__ == "__main__":
    unittest.main()
