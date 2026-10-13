import datetime as dt
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.ap.is_dirty = lambda path: False
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5},
                    "project": [{"name": "my-app", "path": str(self.tmp), "weekly_cap": 90}]}
        self.state = self.ap.load_state(self.cfg)
        self.limits = {"primary": {"used_percent": 22, "resets_at": time.time() + 3600},
                       "secondary": {"used_percent": 37, "resets_at": time.time() + 86400}}

    def lines(self, win=None, now=None):
        engine = helpers.FakeEngine([sys.executable, "-c", "pass"])
        return "\n".join(self.ap.status_lines(self.cfg, engine, self.state, self.limits, win,
                                               now or dt.datetime(2026, 10, 2, 20, 0)))

    def test_shows_usage_and_projects(self):
        text = self.lines()
        self.assertIn("37.0%", text)
        self.assertIn("my-app", text)

    def test_shows_claims_and_handoffs(self):
        self.ap.coord.claim("my-app", "claude")
        self.ap.coord.hand_off("my-app", "claude")
        self.assertIn("baton from claude", self.lines())

    def test_shows_window(self):
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        self.assertIn("inside", self.lines(win, dt.datetime(2026, 10, 2, 20, 0)))
        self.assertIn("outside", self.lines(win, dt.datetime(2026, 10, 6, 10, 0)))

    def test_window_days_stay_in_portuguese(self):
        win = self.ap.schedule.Window("fri 19:00", "sun 03:00")
        self.assertIn("opens sex 09/10 19:00", self.lines(win, dt.datetime(2026, 10, 6, 10, 0)))
        self.assertIn("closes dom 04/10 03:00", self.lines(win, dt.datetime(2026, 10, 2, 20, 0)))


if __name__ == "__main__":
    unittest.main()
