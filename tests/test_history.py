import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]
TRUE = [sys.executable, "-c", "pass"]


class CostEngine(helpers.FakeEngine):
    def unit_cost(self, state, s):
        return {"primary": 4.4, "secondary": 0.5}


class LimitEngine(helpers.FakeEngine):
    def classify(self, out, err, code):
        return "usage_limit", time.time() + 600, True


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.project = {"name": "my-api", "path": str(self.tmp / "my-api"), "weekly_cap": 90}
        Path(self.project["path"]).mkdir()
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5, "safety_factor": 1.5,
                                 "primary_cap": 100, "blocked_cooldown_hours": 6},
                    "project": [self.project]}
        self.state = self.ap.load_state(self.cfg)
        self.ap.is_dirty = lambda path: False
        self.ap.prepare_docker = lambda cfg, project: "ok"

    def lines(self):
        import history
        return history.read(self.ap.HISTORY)

    def do_run(self, engine, close=None, deadline=None):
        out, err, code, unit_file = self.ap.run_unit(self.cfg, engine, self.state, self.project, deadline)
        if close:
            unit_file.parent.mkdir(parents=True, exist_ok=True)
            unit_file.write_text(json.dumps(close))
        self.ap.after_unit(self.cfg, engine, self.state, self.project, out, err, code, unit_file)

    def test_run_without_last_unit_is_unfinished_and_keeps_the_unit_open(self):
        self.do_run(helpers.FakeEngine(TRUE))
        [line] = self.lines()
        self.assertEqual(line["outcome"], "unfinished")
        self.assertFalse(line["closed"])
        self.assertNotIn("summary", line)
        self.assertEqual(self.state["my-api"]["unit_id"], line["unit_id"])

    def test_closing_run_records_summary_and_cost_once(self):
        engine = CostEngine(TRUE)
        self.do_run(engine)
        self.do_run(engine, close={"status": "worked", "unit": "Task 4", "summary": "feito", "milestone": "M"})
        first, second = self.lines()
        self.assertEqual(first["unit_id"], second["unit_id"])
        self.assertNotIn("cost", first)
        self.assertEqual(second["outcome"], "worked")
        self.assertTrue(second["closed"])
        self.assertEqual((second["unit"], second["summary"], second["milestone"]), ("Task 4", "feito", "M"))
        self.assertEqual(second["cost"], {"primary": 4.4, "secondary": 0.5})
        self.assertIsNone(self.state["my-api"]["unit_id"])

    def test_next_unit_gets_a_new_id(self):
        engine = helpers.FakeEngine(TRUE)
        self.do_run(engine, close={"status": "worked", "unit": "a"})
        time.sleep(1.1)  # o id é o nome da execução, com resolução de segundos
        self.do_run(engine, close={"status": "worked", "unit": "b"})
        first, second = self.lines()
        self.assertNotEqual(first["unit_id"], second["unit_id"])

    def test_usage_limit_and_error_are_recorded(self):
        self.do_run(LimitEngine(TRUE))
        self.do_run(helpers.FakeEngine([sys.executable, "-c", "raise SystemExit(3)"]))
        self.assertEqual([l["outcome"] for l in self.lines()], ["usage_limit", "error"])

    def test_cut_passes_the_unit_id_with_the_baton(self):
        self.ap.coord.claim("my-api", "claude")
        self.do_run(helpers.FakeEngine(SLEEP, name="claude"), deadline=time.time() + 1)
        [cut] = self.lines()
        self.assertEqual(cut["outcome"], "cut")
        self.assertEqual(self.ap.coord.read_claim("my-api")["unit_id"], cut["unit_id"])
        self.assertIsNone(self.state["my-api"]["unit_id"])
        codex = helpers.FakeEngine(TRUE, name="codex")
        self.ap.run_project(self.cfg, codex, self.state, self.project, {}, None)
        self.assertEqual(self.lines()[-1]["unit_id"], cut["unit_id"])
        self.assertEqual(self.lines()[-1]["engine"], "codex")


class GroupTest(unittest.TestCase):
    def test_runs_of_one_unit_become_one_entry_newest_first(self):
        import history
        entries = [
            {"unit_id": "u1", "run": "r1", "project": "p", "engine": "claude", "outcome": "cut", "closed": False,
             "started_at": "2026-10-10T19:00:00-03:00", "ended_at": "2026-10-10T19:30:00-03:00"},
            {"unit_id": "u1", "run": "r2", "project": "p", "engine": "codex", "outcome": "worked", "closed": True,
             "started_at": "2026-10-10T20:00:00-03:00", "ended_at": "2026-10-10T20:10:00-03:00",
             "unit": "Task 4", "summary": "s", "milestone": None, "cost": {"primary": 1, "secondary": 0.1}},
            {"unit_id": "u0", "run": "r0", "project": "p", "engine": "codex", "outcome": "worked", "closed": True,
             "started_at": "2026-10-10T10:00:00-03:00", "ended_at": "2026-10-10T10:05:00-03:00", "unit": "x"},
        ]
        units = history.group(entries)
        self.assertEqual([u["unit_id"] for u in units], ["u1", "u0"])
        u1 = units[0]
        self.assertEqual(u1["engines"], ["claude", "codex"])
        self.assertEqual((u1["outcome"], u1["closed"], u1["unit"]), ("worked", True, "Task 4"))
        self.assertEqual(u1["duration"], 2400)
        self.assertEqual([r["run"] for r in u1["runs"]], ["r1", "r2"])

    def test_read_skips_broken_lines(self):
        import history
        path = Path(tempfile.mkdtemp()) / "h.jsonl"
        path.write_text('{"unit_id": "a"}\nnot json\n{"no_id": 1}\n')
        self.assertEqual([e["unit_id"] for e in history.read(path)], ["a"])


if __name__ == "__main__":
    unittest.main()
