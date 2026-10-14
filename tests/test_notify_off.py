"""ntfy switched off, or on without a topic: decisions still get a deadline, nothing reaches the network."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers

import config  # noqa: E402  (helpers puts the repo root on sys.path)


class NotifyOffLoopTest(unittest.TestCase):
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
                    "notify": {"enabled": False, "topic": "T", "decision_timeout_hours": 4},
                    "project": [{"name": "my-app", "path": str(self.project)}]}
        self.fetched = []
        self.ap.fetch_messages = lambda cfg, topic, since, label: self.fetched.append(topic) or []

    def decision(self):
        return json.loads(self.path.read_text())

    def test_off_starts_the_deadline_and_sends_nothing(self):
        before = time.time()
        self.ap.process_decisions(self.cfg, self.ap.load_state(self.cfg))
        d = self.decision()
        self.assertGreaterEqual(d["notified_at"], before)
        self.assertAlmostEqual(d["deadline"], d["notified_at"] + 4 * 3600, delta=1)
        self.assertEqual((self.ap.sent, self.fetched), ([], []))
        self.assertIn("my-app: decision x1 waits in the panel (no ntfy); deadline 4 h", self.ap.LOG.read_text())

    def test_off_applies_the_default_at_the_deadline(self):
        state = self.ap.load_state(self.cfg)
        state["my-app"]["status"] = "waiting"
        self.ap.process_decisions(self.cfg, state)
        self.path.write_text(json.dumps({**self.decision(), "deadline": time.time() - 1}))
        self.ap.process_decisions(self.cfg, state)
        d = self.decision()
        self.assertEqual((d["answer"], d["answered_by"]), ("a", "timeout"))
        self.assertEqual(state["my-app"]["status"], "active")
        self.assertEqual((self.ap.sent, self.fetched), ([], []))

    def test_on_without_a_topic_behaves_as_off(self):
        self.cfg["notify"] = {"decision_timeout_hours": 4}
        self.ap.process_decisions(self.cfg, self.ap.load_state(self.cfg))
        self.assertTrue(self.decision()["notified_at"])
        self.assertEqual((self.ap.sent, self.fetched), ([], []))

    def test_turning_it_back_on_does_not_replay_what_was_typed_meanwhile(self):
        self.ap.process_decisions(self.cfg, self.ap.load_state(self.cfg))
        saved = json.loads((self.ap.coord.SHARED / "notify.json").read_text())
        self.assertEqual(saved["since"], saved["since_typed"])
        self.assertGreaterEqual(int(saved["since"]), int(time.time()) - 5)

    def test_on_with_a_topic_still_sends(self):
        self.cfg["notify"]["enabled"] = True
        self.ap.process_decisions(self.cfg, self.ap.load_state(self.cfg))
        self.assertEqual([t for t, _ in self.ap.sent], ["my-app: decisão"])
        self.assertEqual(self.fetched, ["T-reply", "T"])

    def test_the_sender_itself_stays_off_the_network(self):
        urlopen = mock.MagicMock()
        with mock.patch.object(self.ap.urllib.request, "urlopen", urlopen):
            self.ap.real_ntfy(self.cfg, "t", "m")  # off
            self.ap.real_ntfy({"notify": {"decision_timeout_hours": 4}}, "t", "m")  # on, no topic
            self.ap.real_ntfy({"notify": {"topic": "  "}}, "t", "m")  # on, blank topic
            urlopen.assert_not_called()
            self.ap.real_ntfy({"notify": {"topic": "T"}}, "t", "m")
        self.assertEqual(urlopen.call_count, 1)


class NotifyOffConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        helpers.make_home(self.tmp)
        self.ap.is_dirty = lambda path: False

    def set(self, **notify):
        data = config.load(self.tmp)
        data["notify"].update(notify)
        helpers.write_config_text(self.tmp, json.dumps(data))

    def status(self):
        cfg = self.ap.load_config()
        engine = helpers.FakeEngine([sys.executable, "-c", "pass"])
        return self.ap.status_lines(cfg, engine, self.ap.load_state(cfg), {}, None, None)

    def test_off_does_not_read_secrets_toml(self):
        self.set(enabled=False)
        (self.tmp / "secrets.toml").write_text("not toml [")  # reading it would raise
        self.assertNotIn("topic", self.ap.load_config()["notify"])

    def test_secrets_toml_cannot_switch_ntfy_on_or_off(self):
        (self.tmp / "secrets.toml").write_text('[notify]\ntopic = "segredo-xyz"\nenabled = false\n')
        cfg = self.ap.load_config()
        self.assertEqual(cfg["notify"]["topic"], "segredo-xyz")
        self.assertTrue(self.ap.notify_on(cfg))

    def test_status_says_how_decisions_reach_the_owner(self):
        self.assertFalse([line for line in self.status() if line.startswith("ntfy")])
        (self.tmp / "secrets.toml").unlink()
        self.assertIn("ntfy: on, but secrets.toml has no topic (notify_no_topic); decisions wait in the panel",
                      self.status())
        self.set(enabled=False)
        self.assertIn("ntfy: off; decisions wait in the panel", self.status())

    def test_test_notify_says_ntfy_is_off(self):
        self.set(enabled=False)
        with self.assertRaises(SystemExit) as cm:
            self.ap.cmd_test_notify(None)
        self.assertIn("ntfy is off", str(cm.exception.code))
        self.assertEqual(self.ap.sent, [])

    def test_test_notify_in_english(self):
        data = config.load(self.tmp)
        data["language"] = "en"
        helpers.write_config_text(self.tmp, json.dumps(data))
        self.ap.cmd_test_notify(None)
        self.assertEqual(self.ap.sent, [("autopilot: test", "If you are reading this on your phone, notifications work.")])


if __name__ == "__main__":
    unittest.main()
