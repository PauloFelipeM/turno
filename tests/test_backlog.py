import json
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

TRUE = [sys.executable, "-c", "pass"]
GOOD = {"items": [
    {"id": "admin", "title": "Administração", "state": "doing", "tasks": {"done": 3, "total": 14}},
    {"id": "billing", "title": "Cobrança", "state": "todo"},
    {"id": "chat", "title": "Chat", "state": "done"},
]}


class ValidTest(unittest.TestCase):
    def setUp(self):
        helpers.load(Path(tempfile.mkdtemp()))
        from turno import backlog
        self.b = backlog

    def test_good(self):
        self.assertTrue(self.b.valid_backlog(GOOD))
        self.assertTrue(self.b.valid_backlog({"items": []}))

    def test_bad(self):
        item = GOOD["items"][0]
        for bad in (None, [], {"items": {}}, {"items": [dict(item, state="wip")]},
                    {"items": [dict(item, title="")]}, {"items": [dict(item, id=3)]},
                    {"items": [dict(item, tasks={"done": "3", "total": 14})]},
                    {"items": [dict(item, tasks={"done": 15, "total": 14})]},
                    {"items": [dict(item, tasks={"done": True, "total": 14})]},
                    {"items": [dict(item, tasks={"done": 0, "total": 0})]}):
            self.assertFalse(self.b.valid_backlog(bad), bad)

    def test_current_prefers_doing_then_first_not_done(self):
        self.assertEqual(self.b.current(GOOD["items"])["id"], "admin")
        self.assertEqual(self.b.current([{"id": "a", "title": "a", "state": "done"},
                                         {"id": "b", "title": "b", "state": "spec"}])["id"], "b")
        self.assertIsNone(self.b.current([{"id": "a", "title": "a", "state": "done"}]))


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        helpers.load(self.tmp)
        from turno import backlog
        self.b = backlog
        self.file = self.tmp / backlog.FILE
        self.file.parent.mkdir(parents=True)

    def test_missing(self):
        self.assertEqual(self.b.read_backlog(self.tmp), {"error": "missing"})

    def test_half_written_keeps_last_valid(self):
        cache = {}
        self.file.write_text(json.dumps(GOOD))
        self.assertEqual(self.b.read_backlog(self.tmp, cache), {"items": GOOD["items"]})
        self.file.write_text('{"items": [{"id": "adm')
        self.assertEqual(self.b.read_backlog(self.tmp, cache), {"items": GOOD["items"], "stale": True})
        self.assertEqual(self.b.read_backlog(self.tmp), {"error": "invalid"})

    def test_not_utf8_is_invalid(self):
        self.file.write_bytes(b"\xff\xfe{")
        self.assertEqual(self.b.read_backlog(self.tmp), {"error": "invalid"})


class AfterUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.project = {"name": "my-app", "path": str(self.tmp / "my-app"), "weekly_cap": 90}
        Path(self.project["path"]).mkdir()
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5, "safety_factor": 1.5,
                                 "primary_cap": 100, "blocked_cooldown_hours": 6},
                    "project": [self.project]}
        self.state = self.ap.load_state(self.cfg)

    def close(self, backlog_text):
        from turno import backlog
        engine = helpers.FakeEngine(TRUE)
        out, err, code, unit_file = self.ap.run_unit(self.cfg, engine, self.state, self.project)
        unit_file.parent.mkdir(parents=True, exist_ok=True)
        unit_file.write_text(json.dumps({"status": "worked", "unit": "x"}))
        if backlog_text is not None:
            (Path(self.project["path"]) / backlog.FILE).write_text(backlog_text)
        self.ap.after_unit(self.cfg, engine, self.state, self.project, out, err, code, unit_file)

    def test_invalid_backlog_is_logged_and_the_unit_still_closes(self):
        self.close("{}")
        self.assertIn("backlog.json missing or invalid", self.ap.LOG.read_text())
        self.assertFalse(self.state["my-app"]["interrupted"])

    def test_valid_backlog_is_not_logged(self):
        self.close(json.dumps(GOOD))
        self.assertNotIn("backlog.json", self.ap.LOG.read_text())


if __name__ == "__main__":
    unittest.main()
