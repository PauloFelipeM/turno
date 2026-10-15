import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts the repo root on sys.path)

from turno import engines


def fake_bin(folder, name, body):
    path = Path(folder) / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class CliStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        engines.CLI_CACHE.clear()
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(engines.CLI_CACHE.clear)

    def test_found_with_version(self):
        path = fake_bin(self.tmp, "codex", "echo 'codex-cli 1.2.3'; echo extra\n")
        os.environ["CODEX_BIN"] = path
        self.assertEqual(engines.cli_status("codex"),
                         {"found": True, "path": path, "version": "codex-cli 1.2.3"})

    def test_claude_found_with_version(self):
        path = fake_bin(self.tmp, "claude", "echo '2.0.1 (Claude Code)'\n")
        os.environ["CLAUDE_BIN"] = path
        self.assertEqual(engines.cli_status("claude")["version"], "2.0.1 (Claude Code)")

    def test_silent_binary_has_no_version(self):
        path = fake_bin(self.tmp, "codex", "exit 0\n")
        os.environ["CODEX_BIN"] = path
        self.assertEqual(engines.cli_status("codex"), {"found": True, "path": path, "version": None})

    def test_missing_binary(self):
        os.environ.pop("CODEX_BIN", None)
        with mock.patch.object(engines.shutil, "which", return_value=None), \
                mock.patch.object(engines.Path, "home", return_value=self.tmp):
            self.assertEqual(engines.locate_codex(), None)
            self.assertEqual(engines.cli_status("codex"),
                             {"found": False, "path": None, "version": None})

    def test_missing_claude_binary(self):
        os.environ.pop("CLAUDE_BIN", None)
        with mock.patch.object(engines.shutil, "which", return_value=None), \
                mock.patch.object(engines.Path, "home", return_value=self.tmp):
            self.assertIsNone(engines.locate_claude())
            with self.assertRaises(SystemExit):
                engines.find_claude()
            with self.assertRaises(SystemExit):
                engines.find_codex()

    def test_unknown_engine(self):
        self.assertEqual(engines.cli_status("gemini"),
                         {"found": False, "path": None, "version": None})

    def test_cache_within_max_age(self):
        marker = self.tmp / "calls"
        path = fake_bin(self.tmp, "codex", f"echo x >> '{marker}'\necho v1\n")
        os.environ["CODEX_BIN"] = path
        engines.cli_status("codex")
        engines.cli_status("codex")
        self.assertEqual(len(marker.read_text().split()), 1)
        engines.cli_status("codex", max_age=0)
        self.assertEqual(len(marker.read_text().split()), 2)

    def test_hanging_binary_times_out(self):
        path = fake_bin(self.tmp, "codex", "sleep 5\n")
        os.environ["CODEX_BIN"] = path
        with mock.patch.object(engines, "CLI_VERSION_TIMEOUT", 0.5):
            status = engines.cli_status("codex")
        self.assertEqual(status, {"found": True, "path": path, "version": None})


if __name__ == "__main__":
    unittest.main()
