import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import helpers

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


class InterruptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.project = {"name": "my-api", "path": str(self.tmp / "my-api"), "weekly_cap": 90}
        Path(self.project["path"]).mkdir()
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5, "safety_factor": 1.5,
                                 "primary_cap": 100, "blocked_cooldown_hours": 6},
                    "project": [self.project]}
        self.state = self.ap.load_state(self.cfg)
        self.engine = helpers.FakeEngine(SLEEP, name="codex")
        for name in ("CURRENT", "CURRENT_UNIT", "STOP_REQUESTED", "EXIT_REQUESTED"):
            self.addCleanup(setattr, self.ap, name, getattr(self.ap, name))

    def fire_when_running(self, sig):
        """Sends `sig` to this very process (main thread) once the unit's process exists."""
        def work():
            for _ in range(200):
                if self.ap.CURRENT is not None:
                    break
                time.sleep(0.05)
            time.sleep(0.3)
            os.kill(os.getpid(), sig)
        t = threading.Thread(target=work)
        t.start()
        self.addCleanup(t.join)
        signal.alarm(20)  # safety net: a hang fails the test instead of the suite
        self.addCleanup(signal.alarm, 0)
        old_alarm = signal.signal(signal.SIGALRM, lambda *_: self.fail("run_unit hung"))
        self.addCleanup(signal.signal, signal.SIGALRM, old_alarm)

    def lines(self):
        import history
        return history.read(self.ap.HISTORY)

    def run_in_thread(self):
        box = {}

        def work():
            out, err, code, unit_file = self.ap.run_unit(self.cfg, self.engine, self.state, self.project)
            box["code"] = code
            box["wait"] = self.ap.after_unit(self.cfg, self.engine, self.state, self.project, out, err, code,
                                             unit_file)
        t = threading.Thread(target=work)
        t.start()
        for _ in range(100):
            if self.ap.CURRENT is not None:
                break
            time.sleep(0.05)
        self.assertIsNotNone(self.ap.CURRENT)
        return t, box

    def test_interrupt_stops_the_unit_and_keeps_it_open(self):
        self.ap.coord.claim("my-api", "codex")
        t, box = self.run_in_thread()
        self.state["my-api"]["session"] = "sess-1"
        self.ap.request_interrupt()
        t.join(60)
        self.assertEqual(box["code"], self.ap.STOPPED)
        self.assertEqual(box["wait"], 5)
        [line] = self.lines()
        self.assertEqual(line["outcome"], "stopped")
        self.assertFalse(line["closed"])
        s = self.state["my-api"]
        self.assertTrue(s["interrupted"])
        self.assertEqual(s["session"], "sess-1")
        self.assertEqual(self.ap.coord.read_claim("my-api")["engine"], "codex")
        self.assertFalse(self.ap.STOP_REQUESTED)

    def test_sigusr1_on_the_main_thread_does_not_deadlock(self):
        old = signal.signal(signal.SIGUSR1, self.ap.request_interrupt)
        self.addCleanup(signal.signal, signal.SIGUSR1, old)
        self.fire_when_running(signal.SIGUSR1)
        start = time.time()
        out, err, code, unit_file = self.ap.run_unit(self.cfg, self.engine, self.state, self.project)
        self.assertEqual(code, self.ap.STOPPED)
        self.assertLess(time.time() - start, 15)
        self.assertTrue(self.state["my-api"]["interrupted"])

    def test_exit_signal_makes_run_unit_record_save_and_exit_130(self):
        old = signal.signal(signal.SIGTERM, self.ap.make_exit_handler(self.state))
        self.addCleanup(signal.signal, signal.SIGTERM, old)
        self.fire_when_running(signal.SIGTERM)
        with self.assertRaises(SystemExit) as cm:
            self.ap.run_unit(self.cfg, self.engine, self.state, self.project)
        self.assertEqual(cm.exception.code, 130)
        [line] = self.lines()
        self.assertEqual(line["outcome"], "stopped")
        self.assertIsNone(self.ap.CURRENT_UNIT)
        import json
        saved = json.loads(self.ap.STATE.read_text())
        self.assertTrue(saved["my-api"]["interrupted"])

    def test_interrupt_ignores_a_process_that_already_ended(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.ap.CURRENT = proc
        self.ap.request_interrupt()
        self.assertFalse(self.ap.STOP_REQUESTED)

    def test_interrupt_without_a_running_unit_changes_nothing(self):
        self.ap.request_interrupt()
        self.assertFalse(self.ap.STOP_REQUESTED)
        self.assertEqual(self.lines(), [])

    def test_record_interrupted_run_writes_a_stopped_line(self):
        self.ap.CURRENT_UNIT = (self.engine, self.project, self.state["my-api"])
        self.state["my-api"]["run"] = {"id": "r1", "started": time.time()}
        self.ap.record_interrupted_run()
        [line] = self.lines()
        self.assertEqual(line["outcome"], "stopped")
        self.assertEqual(line["run"], "r1")

    def test_record_interrupted_run_is_a_noop_without_a_unit(self):
        self.ap.CURRENT_UNIT = None
        self.ap.record_interrupted_run()
        self.assertEqual(self.lines(), [])


    def test_exit_signal_still_lets_the_engine_read_the_run(self):
        calls = []
        self.engine.after_run = lambda state, s, out, resume: calls.append((Path(out).name, resume))
        old = signal.signal(signal.SIGTERM, self.ap.make_exit_handler(self.state))
        self.addCleanup(signal.signal, signal.SIGTERM, old)
        self.fire_when_running(signal.SIGTERM)
        with self.assertRaises(SystemExit):
            self.ap.run_unit(self.cfg, self.engine, self.state, self.project)
        [line] = self.lines()
        self.assertEqual(calls, [(line["run"] + ".jsonl", False)])


if __name__ == "__main__":
    unittest.main()
