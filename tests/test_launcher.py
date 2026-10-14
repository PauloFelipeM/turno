import multiprocessing as mp
import os
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

import helpers

CTX = mp.get_context("fork")


def _hold_instance(shared, name, ready):
    import coord
    coord.SHARED = Path(shared)
    import autopilot
    handle = autopilot.instance_lock(name)
    assert handle is not None
    ready.set()
    time.sleep(30)  # stop's SIGINT interrupts here


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.calls = []
        self.windows = {}  # name -> pane_dead ("0" alive, "1" dead); None = no session

        def tmux(*args):
            self.calls.append(args)
            if args[0] == "has-session":
                return 0 if self.windows is not None else 1, ""
            if args[0] == "list-windows":
                return 0, "".join(f"{n} {d}\n" for n, d in self.windows.items())
            return 0, ""

        self.ap.tmux = tmux
        self.ap.loop_pids = lambda engine: []
        helpers.write_config(self.tmp)  # both engines enabled

    def commands(self, verb):
        return [c for c in self.calls if c[0] == verb]

    def loop_launches(self):
        """new-session/new-window of the loops (not of the logs window)."""
        return [c for c in self.commands("new-session") + self.commands("new-window") if "logs" not in c]

    def test_start_creates_one_session_with_both_loops(self):
        self.windows = None
        self.ap.start_loops()
        new_session = self.commands("new-session")[0]
        self.assertIn("codex", new_session)
        self.assertTrue(new_session[-1].endswith("autopilot.py --engine codex run"))
        claude = self.commands("new-window")[0]
        self.assertIn("claude", claude)
        self.assertTrue(claude[-1].endswith("autopilot.py --engine claude run"))
        self.assertNotIn("--config", claude[-1])
        self.assertTrue(any(c[:2] == ("set-option", "-t") and "remain-on-exit" in c for c in self.calls))

    def test_start_skips_a_loop_that_is_already_running(self):
        self.windows = {"codex": "0"}
        held = self.ap.instance_lock("codex")
        self.ap.start_loops()
        self.ap.coord.unlock(held)
        self.assertFalse(any("codex" in c for c in self.commands("respawn-window")))
        self.assertEqual(len(self.loop_launches()), 1)

    def test_start_respawns_a_dead_window(self):
        self.windows = {"codex": "1", "claude": "1"}
        self.ap.start_loops()
        self.assertEqual(len(self.commands("respawn-window")), 2)
        self.assertEqual(self.commands("new-session"), [])

    def test_start_does_not_duplicate_a_loop_started_the_old_way(self):
        self.windows = None
        self.ap.loop_pids = lambda engine: [4242] if engine == "codex" else []
        self.ap.start_loops()
        launched = self.loop_launches()
        self.assertEqual(len(launched), 1)
        self.assertIn("claude", launched[0])

    def test_start_opens_a_logs_window_with_both_logs_selected(self):
        self.windows = None
        self.ap.start_loops()
        logs = [c for c in self.calls if "logs" in c and c[0] in ("new-window", "new-session")]
        self.assertEqual(len(logs), 1)
        self.assertIn(str(self.ap.HOME / "autopilot.log"), logs[0][-1])
        split = self.commands("split-window")
        self.assertEqual(len(split), 1)
        self.assertIn(str(self.ap.HOME / "autopilot-claude.log"), split[0][-1])
        self.assertEqual(self.commands("select-window")[-1][-1], "autopilot:logs")

    def test_logs_window_is_not_duplicated(self):
        self.windows = {"codex": "0", "claude": "0", "logs": "0"}
        held = [self.ap.instance_lock("codex"), self.ap.instance_lock("claude")]
        self.ap.start_loops()
        for h in held:
            self.ap.coord.unlock(h)
        self.assertEqual(self.commands("split-window"), [])
        self.assertEqual(self.commands("select-window")[-1][-1], "autopilot:logs")

    def test_logs_window_needs_no_running_session(self):
        self.windows = None
        self.ap.loop_pids = lambda engine: [4242]  # both loops running outside tmux
        self.ap.start_loops()
        session = self.commands("new-session")
        self.assertEqual(len(session), 1)
        self.assertIn("logs", session[0])

    def test_second_instance_of_the_same_engine_is_refused(self):
        held = self.ap.instance_lock("claude")
        self.assertIsNone(self.ap.instance_lock("claude"))
        self.assertIsNotNone(self.ap.instance_lock("codex"))
        self.ap.coord.unlock(held)

    def test_stop_interrupts_the_running_loop(self):
        ready = CTX.Event()
        child = CTX.Process(target=_hold_instance, args=(str(self.ap.coord.SHARED), "codex", ready))
        child.start()
        self.assertTrue(ready.wait(10))
        self.ap.stop_loops()
        child.join(10)
        self.assertFalse(child.is_alive())
        self.assertIsNone(self.ap.running_pid("codex"))

    def test_loop_command_carries_turno_home(self):
        for engine in ("codex", "claude"):
            cmd = self.ap.loop_command(engine)
            self.assertTrue(cmd.startswith(f"TURNO_HOME={self.ap.shlex.quote(str(self.ap.HOME))} python3 "))
            self.assertTrue(cmd.endswith(" run"))

    def test_start_only_claude_creates_only_its_window_and_logs(self):
        self.windows = None
        result = self.ap.start_loops(["claude"])
        self.assertEqual(result, {"claude": "started"})
        launched = self.loop_launches()
        self.assertEqual(len(launched), 1)
        self.assertIn("claude", launched[0])
        self.assertEqual(len(self.commands("split-window")), 1)

    def test_start_returns_already_running(self):
        self.windows = {"codex": "0"}
        held = self.ap.instance_lock("codex")
        result = self.ap.start_loops()
        self.ap.coord.unlock(held)
        self.assertEqual(result, {"codex": "already running", "claude": "started"})

    def test_stop_only_codex_signals_only_codex_pids(self):
        killed = []
        self.ap.running_pid = lambda name: None if killed else {"codex": 111, "claude": 222}[name]
        self.ap.loop_pids = lambda engine: [] if killed else ([333] if engine == "codex" else [444])
        with mock.patch("os.kill", lambda pid, sig: killed.append(pid)), mock.patch("time.sleep"):
            result = self.ap.stop_loops(["codex"])
        self.assertEqual(sorted(killed), [111, 333])
        self.assertEqual(result, {"codex": "stopped"})

    def test_stop_reports_not_running_and_still_stopping(self):
        self.ap.running_pid = lambda name: None
        self.assertEqual(self.ap.stop_loops(["claude"]), {"claude": "not running"})
        self.ap.running_pid = lambda name: 555
        with mock.patch("os.kill"), mock.patch("time.sleep"):
            self.assertEqual(self.ap.stop_loops(["claude"]), {"claude": "still stopping"})

    def test_matches_loop_command_lines(self):
        script = str(self.ap.BASE / "autopilot.py")
        self.assertTrue(self.ap.is_loop(["python3", script, "run"], "codex"))
        self.assertFalse(self.ap.is_loop(["python3", script, "run"], "claude"))
        self.assertTrue(self.ap.is_loop(["python3", script, "--config", "claude.toml", "run"], "claude"))
        self.assertFalse(self.ap.is_loop(["python3", script, "status"], "codex"))
        self.assertFalse(self.ap.is_loop(["tmux", "new", "-s", "autopilot", f"python3 {script} run"], "codex"))


if __name__ == "__main__":
    unittest.main()
