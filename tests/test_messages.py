import datetime as dt
import json
import re
import string
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

from turno import config  # noqa: E402  (helpers puts the repo root on sys.path)
from turno import messages  # noqa: E402


def fields(template):
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


class MessagesTest(unittest.TestCase):
    def test_every_language_has_the_same_keys_and_fields(self):
        base = messages.TEXTS[messages.DEFAULT]
        self.assertEqual(set(messages.TEXTS), set(config.LANGUAGES))
        self.assertEqual(set(messages.NAMES), set(config.LANGUAGES))
        for lang, table in messages.TEXTS.items():
            self.assertEqual(set(table), set(base), lang)
            for key, template in base.items():
                self.assertEqual(fields(table[key]), fields(template), (lang, key))

    def test_unknown_language_falls_back_to_pt_br(self):
        for lang in ("fr", None, "", 1, ["en"]):
            self.assertEqual(messages.text(lang, "idle_body"), "Backlog concluído.", lang)
        self.assertEqual(messages.language_name("xx"), "Brazilian Portuguese (pt-BR)")
        self.assertEqual(messages.language_name("en"), "English (en)")

    def test_fields_are_filled_and_values_kept_as_they_are(self):
        self.assertEqual(messages.text("en", "decision_title", project="app"), "app: decision")
        self.assertEqual(messages.text("pt-BR", "decision_title", project="app"), "app: decisão")
        self.assertEqual(messages.text("en", "answered_body", question="{x}?", label="A"), "{x}?\n→ A")

    def test_days(self):
        self.assertEqual([messages.day("en", i) for i in range(7)], ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        self.assertEqual(messages.day(None, 5), "sáb")

    def test_every_key_the_scheduler_asks_for_exists(self):
        source = (helpers.PKG / "autopilot.py").read_text()
        used = set(re.findall(r'say\(cfg, "(\w+)"', source))
        used |= set(re.findall(r'"(setup_missing_\w+)"', source))
        self.assertGreaterEqual(len(used), 40)
        self.assertLessEqual(used, set(messages.TEXTS["pt-BR"]))

    def test_no_portuguese_left_in_the_scheduler(self):
        for name in ("autopilot.py", "schedule.py", "panel.py", "control.py"):
            code = [line for line in (helpers.PKG / name).read_text().splitlines() if not line.lstrip().startswith("#")]
            self.assertFalse([line for line in code if re.search(r'"[^"]*[ãõçáéíóúâêô][^"]*"', line)], name)


class WindowTextsTest(unittest.TestCase):
    def test_window_and_moments_follow_the_language(self):
        from turno import schedule
        win = schedule.Window("fri 19:00", "sun 03:00")
        self.assertEqual(win.describe(), "sex 19:00 → dom 03:00")
        self.assertEqual(win.describe("pt-BR"), "sex 19:00 → dom 03:00")
        self.assertEqual(win.describe("en"), "Fri 19:00 → Sun 03:00")
        moment = dt.datetime(2026, 10, 9, 19, 0)
        self.assertEqual(schedule.label(moment), "sex 09/10 19:00")
        self.assertEqual(schedule.label(moment, "en"), "Fri 09/10 19:00")


class LoopTextsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.project = self.tmp / "my-app"
        folder = self.project / ".superpowers" / "autopilot" / "decisions"
        folder.mkdir(parents=True)
        self.path = folder / "x1.json"
        self.path.write_text(json.dumps({"id": "x1", "question": "Q?", "default": "a",
                                         "options": [{"id": "a", "label": "Yes"}, {"id": "b", "label": "No"}]}))
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5},
                    "notify": {"topic": "T", "decision_timeout_hours": 4},
                    "project": [{"name": "my-app", "path": str(self.project)}]}
        self.inbox = []
        self.ap.fetch_messages = lambda cfg, topic, since, label: [m for m in self.inbox if topic == "T"]

    def turn(self, lang=None):
        if lang:
            self.cfg["language"] = lang
        self.ap.process_decisions(self.cfg, self.ap.load_state(self.cfg))
        return self.ap.sent[-1]

    def test_decision_in_english(self):
        title, body = self.turn("en")
        self.assertEqual(title, "my-app: decision")
        self.assertIn("Answer with the buttons or by typing the letter (e.g. A).", body)
        self.assertIn("No answer in 4 h: Yes", body)

    def test_decision_without_a_language_is_unchanged(self):
        title, body = self.turn()
        self.assertEqual(title, "my-app: decisão")
        self.assertIn("Responda pelos botões ou digitando a letra (ex.: A).", body)
        self.assertIn("Sem resposta em 4 h: Yes", body)

    def test_typed_answer_is_confirmed_in_english(self):
        self.turn("en")
        self.inbox.append({"id": "m1", "message": "B", "title": None})
        self.assertEqual(self.turn("en"), ("my-app: noted", "Q?\n→ No"))

    def test_failed_setup_summary_follows_the_language(self):
        from turno import repo_setup as setup
        root = self.tmp / "alpha"
        root.mkdir()
        setup.update(self.tmp, "alpha", lambda cur: {**setup.new_request("g"), "status": "approved"})
        cfg = {"language": "en", "project": []}
        status, summary = self.ap.finish_setup(cfg, {"name": "alpha", "path": str(root)}, {}, "apply", "worked", "S.")
        self.assertEqual((status, summary), ("blocked", "The setup unit ended without docs/autopilot.md in place. S."))


if __name__ == "__main__":
    unittest.main()
