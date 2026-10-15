import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers

ECHO_PROMPT = [sys.executable, "-c", "import sys; print(sys.stdin.read())"]


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.coord = self.ap.coord
        self.projects = [{"name": n, "path": str(self.tmp / n), "weekly_cap": 90}
                         for n in ("my-api", "my-app", "side-project")]
        for p in self.projects:
            Path(p["path"]).mkdir()
        self.cfg = {"language": "pt-BR", "defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5, "safety_factor": 1.5,
                                 "primary_cap": 100, "blocked_cooldown_hours": 6},
                    "notify": {"topic": "T"}, "project": self.projects}
        self.by_name = {p["name"]: p for p in self.projects}
        self.state = self.ap.load_state(self.cfg)

    def say(self, text):
        self.ap.typed_reply(self.cfg, self.by_name, text)

    def pending(self, name):
        return [n["text"] for n in self.coord.pending_notes(name)]

    def decision(self, name, ident="d1"):
        folder = Path(self.by_name[name]["path"]) / ".superpowers" / "autopilot" / "decisions"
        folder.mkdir(parents=True, exist_ok=True)
        d = {"id": ident, "question": "Q?", "default": "a", "notified_at": time.time(), "deadline": time.time() + 3600,
             "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}
        path = folder / f"{ident}.json"
        path.write_text(json.dumps(d))
        return path

    def test_message_naming_a_project_goes_only_there(self):
        self.say("Prioriza o chat no my-api")
        self.assertEqual(self.pending("my-api"), ["Prioriza o chat no my-api"])
        self.assertEqual(self.pending("my-app"), [])
        self.assertIn("my-api", self.ap.sent[-1][1])

    def test_message_naming_two_projects_goes_to_both(self):
        self.say("my-api e side-project: atualizem o web")
        self.assertEqual((len(self.pending("my-api")), len(self.pending("my-app")), len(self.pending("side-project"))), (1, 0, 1))

    def test_message_without_a_project_goes_to_all(self):
        self.say("não mexam no módulo de pagamento")
        for name in self.by_name:
            self.assertEqual(self.pending(name), ["não mexam no módulo de pagamento"])

    def test_answer_with_comment_also_reaches_the_agent(self):
        path = self.decision("my-api")
        self.say("B, mas só para médicos ativos")
        self.assertEqual(json.loads(path.read_text())["answer"], "b")
        notes = self.pending("my-api")
        self.assertEqual(len(notes), 1)
        self.assertIn("d1", notes[0])
        self.assertIn("mas só para médicos ativos", notes[0])

    def test_bare_letter_without_open_question_still_warns(self):
        self.say("B")
        self.assertEqual(self.ap.sent[-1][0], "autopilot: nenhuma pergunta aberta")
        self.assertEqual(self.pending("my-api"), [])

    def test_sentence_that_starts_like_a_letter_is_a_message(self):
        self.decision("my-api")
        self.say("E aí, como vai o chat do my-api?")
        self.assertEqual(self.pending("my-api"), ["E aí, como vai o chat do my-api?"])

    def test_empty_ntfy_message_is_ignored(self):
        self.say("triggered")
        self.assertEqual(self.pending("my-api"), [])
        self.assertEqual(self.ap.sent, [])

    def test_pending_messages_go_into_the_prompt(self):
        self.coord.add_note("my-app", "use o tema escuro por padrão")
        engine = helpers.FakeEngine(ECHO_PROMPT)
        out, *_ = self.ap.run_unit(self.cfg, engine, self.state, self.by_name["my-app"])
        prompt = Path(out).read_text()
        self.assertIn("use o tema escuro por padrão", prompt)
        self.assertIn("Owner messages", prompt)

    def test_prompt_without_messages_has_no_section(self):
        engine = helpers.FakeEngine(ECHO_PROMPT)
        out, *_ = self.ap.run_unit(self.cfg, engine, self.state, self.by_name["my-app"])
        self.assertNotIn("Owner messages", Path(out).read_text())

    def test_message_is_delivered_when_the_unit_closes(self):
        self.coord.add_note("my-app", "use o tema escuro")
        unit = Path(self.by_name["my-app"]["path"]) / ".superpowers" / "autopilot" / "last-unit.json"
        engine = helpers.FakeEngine([sys.executable, "-c",
                                     f"import pathlib; p = pathlib.Path({str(unit)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
                                     "p.write_text('{\"status\": \"worked\", \"unit\": \"u\", \"summary\": \"s\"}')"])
        engine.label = "Codex"
        project = self.by_name["my-app"]
        out, err, code, unit_file = self.ap.run_unit(self.cfg, engine, self.state, project)
        self.ap.after_unit(self.cfg, engine, self.state, project, out, err, code, unit_file)
        self.assertEqual(self.pending("my-app"), [])
        self.assertIn("my-app: mensagem entregue ao Codex", [t for t, _ in self.ap.sent])

    def test_message_stays_pending_when_the_unit_is_cut(self):
        self.coord.add_note("my-app", "use o tema escuro")
        engine = helpers.FakeEngine([sys.executable, "-c", "import time; time.sleep(60)"])
        project = self.by_name["my-app"]
        out, err, code, unit_file = self.ap.run_unit(self.cfg, engine, self.state, project, deadline=time.time() + 1)
        self.ap.after_unit(self.cfg, engine, self.state, project, out, err, code, unit_file)
        self.assertEqual(self.pending("my-app"), ["use o tema escuro"])

    def test_message_sent_during_a_unit_is_not_marked_delivered(self):
        engine = helpers.FakeEngine(ECHO_PROMPT)
        project = self.by_name["my-app"]
        out, err, code, unit_file = self.ap.run_unit(self.cfg, engine, self.state, project)
        self.coord.add_note("my-app", "chegou depois")
        unit_file.parent.mkdir(parents=True, exist_ok=True)
        unit_file.write_text('{"status": "worked"}')
        self.ap.after_unit(self.cfg, engine, self.state, project, out, err, code, unit_file)
        self.assertEqual(self.pending("my-app"), ["chegou depois"])


if __name__ == "__main__":
    unittest.main()
