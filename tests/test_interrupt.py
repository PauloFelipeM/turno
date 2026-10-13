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


if __name__ == "__main__":
    unittest.main()
