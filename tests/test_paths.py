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

    def test_relative_config_resolves_from_home(self):
        self.assertEqual(self.ap.resolve_config("claude.toml"), self.tmp / "claude.toml")
        self.assertEqual(self.ap.resolve_config(None), self.tmp / "projects.toml")

    def test_absolute_config_is_kept(self):
        self.assertEqual(self.ap.resolve_config("/etc/x.toml"), Path("/etc/x.toml"))

    def test_configure_puts_instance_files_in_home(self):
        (self.tmp / "claude.toml").write_text('[instance]\nname = "claude"\n' + MINIMAL)
        self.ap.configure("claude.toml")
        self.assertEqual(self.ap.STATE, self.tmp / "state-claude.json")
        self.assertEqual(self.ap.LOG, self.tmp / "autopilot-claude.log")
        self.assertEqual(self.ap.RUNS, self.tmp / "runs-claude")

    def test_launcher_uses_configs_from_home(self):
        self.assertEqual(dict(self.ap.LOOPS)["claude"], self.tmp / "claude.toml")
        self.assertEqual(self.ap.log_file("codex", None), self.tmp / "autopilot.log")


if __name__ == "__main__":
    unittest.main()
