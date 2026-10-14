import json
import tempfile
import time
import unittest
from pathlib import Path

import helpers


def decision(project_dir, ident, notified=False):
    folder = project_dir / ".superpowers" / "autopilot" / "decisions"
    folder.mkdir(parents=True, exist_ok=True)
    d = {"id": ident, "question": f"{ident}?", "default": "a",
         "options": [{"id": "a", "label": "Opção a"}, {"id": "b", "label": "Opção b"}]}
    if notified:
        d.update(notified_at=time.time(), deadline=time.time() + 3600)
    path = folder / f"{ident}.json"
    path.write_text(json.dumps(d))
    return path


class DecisionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.coord = self.ap.coord
        self.project = self.tmp / "my-app"
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5},
                    "notify": {"topic": "T"}, "project": [{"name": "my-app", "path": str(self.project)}]}
        self.messages = {"T": [], "T-reply": []}
        self.ap.fetch_messages = lambda cfg, topic, since, label: [
            m for m in self.messages[topic] if m["id"] > since]

    def state(self):
        return self.ap.load_state(self.cfg)

    def titles(self):
        return [t for t, _ in self.ap.sent]

    def test_decision_is_sent_once_across_instances(self):
        decision(self.project, "x1")
        self.ap.process_decisions(self.cfg, self.state())
        self.ap.process_decisions(self.cfg, self.state())
        self.assertEqual(self.titles(), ["my-app: decisão"])

    def test_dispatch_skipped_while_other_instance_holds_the_lock(self):
        decision(self.project, "x1")
        held = self.coord.try_lock("decisions")
        self.ap.process_decisions(self.cfg, self.state())
        self.assertEqual(self.ap.sent, [])
        self.coord.unlock(held)
        self.ap.process_decisions(self.cfg, self.state())
        self.assertEqual(self.titles(), ["my-app: decisão"])

    def test_typed_reply_is_read_once(self):
        path = decision(self.project, "x1", notified=True)
        self.messages["T"].append({"id": "m1", "message": "B", "title": None})
        self.ap.process_decisions(self.cfg, self.state())
        self.ap.process_decisions(self.cfg, self.state())
        self.assertEqual(self.titles(), ["my-app: anotado"])
        self.assertEqual(json.loads(path.read_text())["answer"], "b")

    def test_waiting_project_is_released_even_without_the_lock(self):
        path = decision(self.project, "x1", notified=True)
        state = self.state()
        state["my-app"]["status"] = "waiting"
        d = json.loads(path.read_text())
        d.update(answer="a", answered_by="owner")
        path.write_text(json.dumps(d))
        held = self.coord.try_lock("decisions")
        self.ap.process_decisions(self.cfg, state)
        self.coord.unlock(held)
        self.assertEqual(state["my-app"]["status"], "active")

    def test_notify_position_migrates_from_state(self):
        state = self.state()
        state["_notify"].update(since="s-old", since_typed="t-old")
        self.ap.process_decisions(self.cfg, state)
        saved = json.loads((self.coord.SHARED / "notify.json").read_text())
        self.assertEqual((saved["since"], saved["since_typed"]), ("s-old", "t-old"))


    def test_decision_file_that_is_not_an_object_is_ignored(self):
        path = decision(self.project, "x1")
        path.write_text("[1, 2]")
        project = self.cfg["project"][0]
        self.assertIsNone(self.ap.read_decision(path))
        self.assertEqual(self.ap.open_decisions(project), [])
        state = self.state()
        state["my-app"]["status"] = "waiting"
        self.ap.process_decisions(self.cfg, state)  # dispatch_decisions and the pending check
        self.assertEqual(state["my-app"]["status"], "active")
        self.assertEqual(path.read_text(), "[1, 2]")
        self.assertEqual(self.titles(), [])
        self.assertAlmostEqual(self.ap.next_deadline(project), time.time() + 300, delta=5)
        self.ap.is_dirty = lambda p: False
        self.cfg["project"][0]["weekly_cap"] = 90
        lines = self.ap.status_lines(self.cfg, helpers.FakeEngine(["true"]), state, {}, None, None)
        self.assertFalse([line for line in lines if "pending decision" in line])


if __name__ == "__main__":
    unittest.main()
