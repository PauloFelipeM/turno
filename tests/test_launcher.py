import multiprocessing as mp
import os
import tempfile
import time
import unittest
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
    time.sleep(30)  # o SIGINT do stop interrompe aqui


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.calls = []
        self.windows = {}  # nome -> pane_dead ("0" vivo, "1" morto); None = sem sessão

        def tmux(*args):
            self.calls.append(args)
            if args[0] == "has-session":
                return 0 if self.windows is not None else 1, ""
            if args[0] == "list-windows":
                return 0, "".join(f"{n} {d}\n" for n, d in self.windows.items())
            return 0, ""

        self.ap.tmux = tmux
        self.ap.loop_pids = lambda config: []

    def commands(self, verb):
        return [c for c in self.calls if c[0] == verb]

    def loop_launches(self):
        """new-session/new-window dos loops (não da janela de logs)."""
        return [c for c in self.commands("new-session") + self.commands("new-window") if "logs" not in c]

    def test_start_creates_one_session_with_both_loops(self):
        self.windows = None
        self.ap.start_loops()
        new_session = self.commands("new-session")[0]
        self.assertIn("codex", new_session)
        self.assertTrue(new_session[-1].endswith("autopilot.py run"))
        claude = self.commands("new-window")[0]
        self.assertIn("claude", claude)
        self.assertIn(f"--config {self.ap.BASE / 'claude.toml'} run", claude[-1])
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
        self.ap.loop_pids = lambda config: [4242] if config is None else []
        self.ap.start_loops()
        launched = self.loop_launches()
        self.assertEqual(len(launched), 1)
        self.assertIn("claude", launched[0])

    def test_start_opens_a_logs_window_with_both_logs_selected(self):
        self.windows = None
        self.ap.start_loops()
        logs = [c for c in self.calls if "logs" in c and c[0] in ("new-window", "new-session")]
        self.assertEqual(len(logs), 1)
        self.assertIn(str(self.ap.BASE / "autopilot.log"), logs[0][-1])
        split = self.commands("split-window")
        self.assertEqual(len(split), 1)
        self.assertIn(str(self.ap.BASE / "autopilot-claude.log"), split[0][-1])
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
        self.ap.loop_pids = lambda config: [4242]  # os dois loops rodando fora do tmux
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

    def test_matches_loop_command_lines(self):
        script = str(self.ap.BASE / "autopilot.py")
        claude = str(self.ap.BASE / "claude.toml")
        self.assertTrue(self.ap.is_loop(["python3", script, "run"], None))
        self.assertFalse(self.ap.is_loop(["python3", script, "run"], claude))
        self.assertTrue(self.ap.is_loop(["python3", script, "--config", "claude.toml", "run"], claude))
        self.assertFalse(self.ap.is_loop(["python3", script, "status"], None))
        self.assertFalse(self.ap.is_loop(["tmux", "new", "-s", "autopilot", f"python3 {script} run"], None))


if __name__ == "__main__":
    unittest.main()
