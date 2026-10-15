import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

TRUE = [sys.executable, "-c", "pass"]


class Closed:
    def can_start(self, now):
        return False

    def contains(self, now):
        return False


class Open:
    def can_start(self, now):
        return True

    def contains(self, now):
        return True


class PastMargin:
    """Inside the window but already in the final margin: nothing starts."""
    def can_start(self, now):
        return False

    def contains(self, now):
        return True


class PhaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.project = {"name": "my-app", "path": str(self.tmp / "my-app"), "weekly_cap": 90}
        Path(self.project["path"]).mkdir()
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5, "safety_factor": 1.5,
                                 "primary_cap": 100, "blocked_cooldown_hours": 6},
                    "project": [self.project]}
        self.state = self.ap.load_state(self.cfg)

    def test_running_is_saved_before_the_cli_starts(self):
        self.ap.run_unit(self.cfg, helpers.FakeEngine(TRUE), self.state, self.project)
        saved = json.loads(self.ap.STATE.read_text())
        self.assertEqual(saved["_loop"], {"phase": "running", "project": "my-app", "until": None})

    def test_idle_phase(self):
        now = datetime.datetime.now()
        self.assertEqual(self.ap.idle_phase(None, now), "sleeping")
        self.assertEqual(self.ap.idle_phase(Open(), now), "sleeping")
        self.assertEqual(self.ap.idle_phase(Closed(), now), "outside_window")
        self.assertEqual(self.ap.idle_phase(PastMargin(), now), "sleeping")

    def test_set_phase(self):
        self.ap.set_phase(self.state, "sleeping", until=123.0)
        self.assertEqual(self.state["_loop"], {"phase": "sleeping", "project": None, "until": 123.0})


if __name__ == "__main__":
    unittest.main()
