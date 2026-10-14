import os
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


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)

    def test_data_lives_in_turno_home(self):
        self.assertEqual(self.ap.HOME, self.tmp)
        self.assertEqual(self.ap.coord.SHARED, self.tmp / "shared")

    def test_default_home_is_dot_turno(self):
        import paths
        old = os.environ.pop("TURNO_HOME")
        try:
            self.assertEqual(paths.home(), Path.home() / ".turno")
        finally:
            os.environ["TURNO_HOME"] = old

    def test_configure_puts_instance_files_in_home(self):
        self.ap.configure("claude")
        self.assertEqual(self.ap.STATE, self.tmp / "state-claude.json")
        self.assertEqual(self.ap.LOG, self.tmp / "autopilot-claude.log")
        self.assertEqual(self.ap.RUNS, self.tmp / "runs-claude")

    def test_deprecated_relative_config_is_looked_up_in_home(self):
        (self.tmp / "weekend.toml").write_text('[instance]\nname = "claude"\n' + MINIMAL)
        self.ap.configure(config_path="weekend.toml")
        self.assertEqual(self.ap.STATE, self.tmp / "state-claude.json")

    def test_launcher_uses_the_config_and_logs_from_home(self):
        helpers.write_config(self.tmp, enabled=("claude",))
        self.assertEqual(self.ap.loops(), ["claude"])
        self.assertEqual(self.ap.log_file("codex"), self.tmp / "autopilot.log")
        self.assertIn(f"TURNO_HOME={self.tmp} ", self.ap.loop_command("claude"))


if __name__ == "__main__":
    unittest.main()
