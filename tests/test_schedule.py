import datetime as dt
import unittest

import helpers  # noqa: F401  (puts the root on sys.path)
from turno import schedule

FRI = dt.datetime(2026, 10, 2)


def at(day_offset, hour, minute, second=0):
    return FRI + dt.timedelta(days=day_offset, hours=hour, minutes=minute, seconds=second)


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.w = schedule.Window("fri 19:00", "sun 03:00", 15)

    def test_edges(self):
        self.assertFalse(self.w.contains(at(0, 18, 59)))
        self.assertTrue(self.w.contains(at(0, 19, 0)))
        self.assertTrue(self.w.contains(at(1, 12, 0)))
        self.assertTrue(self.w.contains(at(2, 2, 59, 59)))
        self.assertFalse(self.w.contains(at(2, 3, 0)))
        self.assertFalse(self.w.contains(at(4, 10, 0)))  # Tuesday

    def test_no_new_unit_in_the_last_15_minutes(self):
        self.assertTrue(self.w.can_start(at(2, 2, 44)))
        self.assertFalse(self.w.can_start(at(2, 2, 45)))
        self.assertFalse(self.w.can_start(at(0, 18, 0)))

    def test_end_and_next_start(self):
        self.assertEqual(self.w.end_of(at(0, 19, 0)), at(2, 3, 0))
        self.assertEqual(self.w.next_start(at(0, 18, 0)), at(0, 19, 0))
        self.assertEqual(self.w.next_start(at(4, 10, 0)), at(7, 19, 0))  # Tuesday -> next Friday
        self.assertEqual(self.w.next_start(at(1, 9, 0)), at(1, 9, 0))  # already inside

    def test_window_crossing_the_week_boundary(self):
        w = schedule.Window("sat 20:00", "mon 02:00")
        self.assertTrue(w.contains(at(3, 1, 0)))  # segunda 01:00
        self.assertFalse(w.contains(at(3, 2, 0)))

    def test_invalid_specs(self):
        for bad in ("friday 19:00", "fri 25:00", "fri 19:60", ""):
            with self.assertRaises(ValueError):
                schedule.parse(bad)
        with self.assertRaises(ValueError):
            schedule.Window("fri 19:00", "fri 19:00")

    def test_from_config(self):
        self.assertIsNone(schedule.from_config({}))
        w = schedule.from_config({"schedule": {"start": "fri 19:00", "end": "sun 03:00"}})
        self.assertEqual(w.margin, dt.timedelta(minutes=15))

    def test_next_occurrence(self):
        self.assertEqual(schedule.next_occurrence("sun 04:00", at(0, 20, 0)), at(2, 4, 0))
        self.assertEqual(schedule.next_occurrence("sun 04:00", at(2, 4, 0)), at(9, 4, 0))


if __name__ == "__main__":
    unittest.main()
