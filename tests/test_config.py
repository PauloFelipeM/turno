import tempfile
import unittest
from pathlib import Path

import helpers

MINIMAL = """
[defaults]
orchestrator_model = "m"
orchestrator_effort = "medium"

[[project]]
name = "x"
path = "/tmp/x"
weekly_cap = 50
"""


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text)
        return path

    def test_default_instance_keeps_current_file_names(self):
        self.ap.configure(str(self.write("projects.toml", MINIMAL)))
        self.assertEqual((self.ap.STATE.name, self.ap.LOG.name, self.ap.RUNS.name),
                         ("state.json", "autopilot.log", "runs"))

    def test_named_instance_uses_suffixed_files(self):
        self.ap.configure(str(self.write("claude.toml", '[instance]\nname = "claude"\n' + MINIMAL)))
        self.assertEqual((self.ap.STATE.name, self.ap.LOG.name, self.ap.RUNS.name),
                         ("state-claude.json", "autopilot-claude.log", "runs-claude"))

    def test_secrets_fill_the_ntfy_topic(self):
        self.write("secrets.toml", '[notify]\ntopic = "segredo"\n')
        self.ap.configure(str(self.write("projects.toml", MINIMAL + "\n[notify]\ndecision_timeout_hours = 2\n")))
        cfg = self.ap.load_config()
        self.assertEqual(cfg["notify"]["topic"], "segredo")
        self.assertEqual(cfg["notify"]["decision_timeout_hours"], 2)

    def test_config_without_secrets_still_loads(self):
        self.ap.configure(str(self.write("projects.toml", MINIMAL)))
        self.assertNotIn("topic", self.ap.load_config().get("notify", {}))


if __name__ == "__main__":
    unittest.main()
