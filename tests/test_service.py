"""`turno service`: the unit and agent files (content only) and the calls, with a fake home.
Never runs systemctl or launchctl."""

import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers

ARGV = ["/home/me/.local/share/pipx/venvs/turno/bin/python", "-m", "turno"]


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        helpers.load(self.tmp / "data")
        from turno import service
        self.service = service
        self.fake_home = self.tmp / "home"
        self.fake_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.fake_home), "PATH": "/home/me/.local/bin:/usr/bin:/bin"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("XDG_CONFIG_HOME", None)
        self.calls, self.fail = [], set()

        def run(argv):
            self.calls.append(argv)
            return subprocess.CompletedProcess(argv, 1 if argv[1:3] and tuple(argv[:3]) in self.fail else 0, "", "boom")

        patch = mock.patch.object(service, "_run", run)
        patch.start()
        self.addCleanup(patch.stop)
        self.kind = "systemd"
        patch = mock.patch.object(service, "kind", lambda: self.kind)
        patch.start()
        self.addCleanup(patch.stop)
        self.out = []

    def test_systemd_unit_content(self):
        text = self.service.systemd_unit(ARGV, {"PATH": "/a b:/usr/bin", "TURNO_HOME": "/home/me/x%y"})
        self.assertIn('ExecStart="/home/me/.local/share/pipx/venvs/turno/bin/python" "-m" "turno" "serve" "--start-engines"', text)
        self.assertIn('Environment="PATH=/a b:/usr/bin"', text)
        self.assertIn('Environment="TURNO_HOME=/home/me/x%%y"', text)
        for line in ("Restart=on-failure", "KillMode=process", "WantedBy=default.target"):
            self.assertIn(line, text.splitlines())

    def test_turno_home_only_when_not_the_default(self):
        self.assertEqual(self.service.environment(self.fake_home / ".turno"), {"PATH": "/home/me/.local/bin:/usr/bin:/bin"})
        self.assertEqual(self.service.environment(self.tmp / "data")["TURNO_HOME"], str((self.tmp / "data").resolve()))

    def test_launchd_plist_content(self):
        data = plistlib.loads(self.service.launchd_plist(ARGV, {"PATH": "/usr/bin"}, self.tmp / "data").encode())
        self.assertEqual(data["Label"], "dev.turno.panel")
        self.assertEqual(data["ProgramArguments"], [*ARGV, "serve", "--start-engines"])
        self.assertIs(data["RunAtLoad"], True)
        self.assertEqual(data["KeepAlive"], {"SuccessfulExit": False})
        self.assertIs(data["AbandonProcessGroup"], True)
        self.assertEqual(data["EnvironmentVariables"], {"PATH": "/usr/bin"})

    def test_install_with_systemd(self):
        self.assertEqual(self.service.install(self.tmp / "data", ARGV, ask=self.no_question, out=self.out.append), 0)
        path = self.fake_home / ".config" / "systemd" / "user" / "turno.service"
        self.assertIn("ExecStart=", path.read_text())
        self.assertEqual(self.calls, [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "turno.service"]])
        self.assertTrue(any("loginctl enable-linger" in line for line in self.out))
        self.assertFalse(any("sudo" in " ".join(c) for c in self.calls))

    def test_install_with_launchd(self):
        self.kind = "launchd"
        self.assertEqual(self.service.install(self.tmp / "data", ARGV, ask=self.no_question, out=self.out.append), 0)
        path = self.fake_home / "Library" / "LaunchAgents" / "dev.turno.panel.plist"
        self.assertTrue(path.exists())
        self.assertEqual(self.calls[-1], ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])

    def test_an_existing_different_file_is_kept_unless_the_owner_agrees(self):
        path = self.fake_home / ".config" / "systemd" / "user" / "turno.service"
        path.parent.mkdir(parents=True)
        path.write_text("mine\n")
        self.assertEqual(self.service.install(self.tmp / "data", ARGV, ask=lambda q: "n", out=self.out.append), 1)
        self.assertEqual(path.read_text(), "mine\n")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.service.install(self.tmp / "data", ARGV, ask=lambda q: "y", out=self.out.append), 0)
        self.assertIn("ExecStart=", path.read_text())

    def test_the_same_file_again_asks_nothing(self):
        self.service.install(self.tmp / "data", ARGV, ask=self.no_question, out=self.out.append)
        self.assertEqual(self.service.install(self.tmp / "data", ARGV, ask=self.no_question, out=self.out.append), 0)

    def test_a_failing_systemctl_is_reported(self):
        self.fail = {("systemctl", "--user", "enable")}
        self.assertEqual(self.service.install(self.tmp / "data", ARGV, ask=self.no_question, out=self.out.append), 1)
        self.assertTrue(any("failed" in line for line in self.out))

    def test_without_a_service_manager_it_explains(self):
        self.kind = None
        self.assertEqual(self.service.install(self.tmp / "data", ARGV, ask=self.no_question, out=self.out.append), 1)
        self.assertIn("turno serve", self.out[0])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.service.status(), {"kind": None, "installed": False, "active": False})
        self.assertFalse(self.service.restart())

    def test_status_restart_and_uninstall(self):
        self.assertEqual(self.service.status()["installed"], False)
        self.assertFalse(self.service.restart())
        self.service.install(self.tmp / "data", ARGV, ask=self.no_question, out=self.out.append)
        self.assertEqual(self.service.status(), {"kind": "systemd", "installed": True, "active": True})
        self.assertTrue(self.service.restart())
        self.assertIn(["systemctl", "--user", "restart", "turno.service"], self.calls)
        self.assertEqual(self.service.uninstall(out=self.out.append), 0)
        self.assertFalse(self.service.unit_path().exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", "turno.service"], self.calls)

    def no_question(self, q):
        raise AssertionError(f"unexpected question: {q}")


if __name__ == "__main__":
    unittest.main()
