"""The `turno` package, its entry points and the root shim (run as real processes, with a temporary TURNO_HOME)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers


class EntryPointsTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.env = {**os.environ, "TURNO_HOME": str(self.home), "CODEX_HOME": str(self.home / "codex-home")}
        self.env.pop("PYTHONPATH", None)

    def run_cli(self, argv, cwd):
        return subprocess.run(argv, cwd=cwd, env=self.env, capture_output=True, text=True, timeout=60)

    def test_root_shim_runs_from_another_folder(self):
        r = self.run_cli([sys.executable, str(helpers.ROOT / "autopilot.py"), "--help"], cwd=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("test-notify", r.stdout)

    def test_python_dash_m_turno(self):
        r = self.run_cli([sys.executable, "-m", "turno", "--help"], cwd=helpers.ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("serve", r.stdout)

    def test_shim_status_uses_the_data_folder(self):
        r = self.run_cli([sys.executable, str(helpers.ROOT / "autopilot.py"), "--engine", "claude", "status"], cwd=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("engine Claude", r.stdout)

    def test_code_files_live_in_the_package(self):
        for name in ("prompts/unit.md", "claude-agents.json", "codex-agents/planner.toml", "web/index.html"):
            self.assertTrue((helpers.PKG / name).is_file(), name)
        self.assertEqual(sorted(p.name for p in helpers.ROOT.glob("*.py")), ["autopilot.py"])


if __name__ == "__main__":
    unittest.main()
