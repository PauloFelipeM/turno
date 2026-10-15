"""`turno onboard` with flags and a fake home: config, existing files, the topic file, the service step.
Never runs a real CLI, systemctl, launchctl or the browser."""

import argparse
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers

FIXTURES = Path(__file__).parent / "fixtures"


class OnboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "data"
        self.home.mkdir()
        self.ap = helpers.load(self.home)
        from turno import config, onboard
        self.config, self.onboard = config, onboard
        self.found = {"codex": True, "claude": True}
        self.installs, self.opened, self.out = [], [], []
        self.service_kind = "systemd"
        self.service_active = True
        self.port_open = True
        patches = [
            mock.patch.object(onboard.engines, "cli_status",
                              lambda kind, max_age=60: {"found": self.found[kind], "path": f"/bin/{kind}", "version": "1.0"}),
            mock.patch.object(onboard, "_which", lambda name: f"/usr/bin/{name}"),
            mock.patch.object(onboard, "_run", lambda argv: subprocess.CompletedProcess(argv, 0, "v 1\n", "")),
            mock.patch.object(onboard.service, "kind", lambda: self.service_kind),
            mock.patch.object(onboard.service, "install", self.fake_install),
            mock.patch.object(onboard.service, "status",
                              lambda: {"kind": self.service_kind, "installed": True, "active": self.service_active}),
            mock.patch.object(onboard, "wait_for_port", lambda port, seconds=15: self.port_open),
            mock.patch.object(onboard, "open_url", self.opened.append),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_install(self, home, argv, ask=input, out=print):
        self.installs.append((Path(home), argv))
        return 0

    def args(self, *argv):
        parser = argparse.ArgumentParser()
        self.onboard.add_arguments(parser)
        return parser.parse_args(list(argv))

    def run_wizard(self, *argv, answers=()):
        answers = list(answers)

        def ask(question):
            if not answers:
                raise AssertionError(f"unexpected question: {question}")
            return answers.pop(0)

        code = self.onboard.run(self.args(*argv), self.home, ask=ask, out=self.out.append)
        self.assertEqual(answers, [], "unused answers")
        return code

    def test_flags_write_the_config_and_install_the_service(self):
        code = self.run_wizard("--language", "en", "--engines", "claude", "--no-ntfy", "--service")
        self.assertEqual(code, 0)
        data = self.config.load(self.home)
        self.assertEqual(data["language"], "en")
        self.assertEqual((data["engines"]["codex"]["enabled"], data["engines"]["claude"]["enabled"]), (False, True))
        self.assertIs(data["notify"]["enabled"], False)
        self.assertEqual(self.installs, [(self.home, self.ap.turno_argv())])
        self.assertEqual(self.opened, ["http://localhost:8765"])
        self.assertFalse((self.home / "secrets.toml").exists())

    def test_yes_takes_the_defaults(self):
        with mock.patch.dict(os.environ, {"LANG": "pt_BR.UTF-8", "LC_ALL": "", "LC_MESSAGES": ""}):
            self.assertEqual(self.run_wizard("--yes", "--no-open"), 0)
        data = self.config.load(self.home)
        self.assertEqual(data["language"], "en")  # English by default, whatever the locale says
        self.assertTrue(data["engines"]["codex"]["enabled"] and data["engines"]["claude"]["enabled"])
        self.assertIs(data["notify"]["enabled"], False)  # ntfy defaults to off
        self.assertEqual(len(self.installs), 1)
        self.assertEqual(self.opened, [])

    def test_interactive_answers(self):
        code = self.run_wizard("--no-open", answers=["en", "n", "", "y", "n"])
        self.assertEqual(code, 0)
        data = self.config.load(self.home)
        self.assertEqual((data["engines"]["codex"]["enabled"], data["engines"]["claude"]["enabled"]), (False, True))
        self.assertIs(data["notify"]["enabled"], True)
        self.assertEqual(self.installs, [])

    def test_ntfy_topic_file_is_private_and_shown_once(self):
        self.run_wizard("--yes", "--ntfy", "--no-service")
        secrets = self.home / "secrets.toml"
        self.assertEqual(stat.S_IMODE(secrets.stat().st_mode), 0o600)
        topic = secrets.read_text().split('"')[1]
        self.assertTrue(topic.startswith("turno-") and len(topic) > 20)
        self.assertEqual(sum(topic in line for line in self.out), 1)
        self.assertIs(self.config.load(self.home)["notify"]["enabled"], True)
        self.out.clear()
        self.run_wizard("--yes", "--ntfy", "--no-service")
        self.assertEqual(secrets.read_text().split('"')[1], topic)  # kept, never replaced
        self.assertFalse(any(topic in line for line in self.out))

    def test_a_secrets_file_without_topic_is_not_touched(self):
        (self.home / "secrets.toml").write_text("# mine\n")
        self.run_wizard("--yes", "--ntfy", "--no-service")
        self.assertEqual((self.home / "secrets.toml").read_text(), "# mine\n")
        self.assertIs(self.config.load(self.home)["notify"]["enabled"], False)

    def test_an_existing_config_is_kept_unless_the_owner_agrees(self):
        helpers.write_config(self.home, [{"name": "my-app", "path": "/home/me/my-app"}], enabled=("codex",))
        before = (self.home / "config.json").read_text()
        self.run_wizard("--language", "en", "--engines", "claude", "--no-ntfy", "--no-service", answers=["n"])
        self.assertEqual((self.home / "config.json").read_text(), before)
        self.run_wizard("--language", "en", "--engines", "claude", "--no-ntfy", "--no-service", answers=["y"])
        data = self.config.load(self.home)
        self.assertEqual([p["name"] for p in data["projects"]], ["my-app"])  # projects are never touched
        self.assertEqual((data["language"], data["engines"]["claude"]["enabled"], data["engines"]["codex"]["enabled"]),
                         ("en", True, False))

    def test_yes_never_changes_an_existing_config(self):
        helpers.write_config(self.home, enabled=("codex",))
        before = (self.home / "config.json").read_text()
        self.run_wizard("--yes", "--language", "en", "--no-service")
        self.assertEqual((self.home / "config.json").read_text(), before)

    def test_the_toml_files_are_imported_when_there_is_no_config(self):
        (self.home / "projects.toml").write_text((FIXTURES / "projects.toml").read_text())
        self.run_wizard("--yes", "--no-service")
        data = json.loads((self.home / "config.json").read_text())
        self.assertEqual([p["name"] for p in data["projects"]], ["my-app", "my-api", "side-project"])
        self.assertEqual(data["language"], "pt-BR")  # the old TOML setup was in Portuguese
        self.assertTrue((self.home / "projects.toml").exists())

    def test_no_engine_cli_stops_the_wizard(self):
        self.found = {"codex": False, "claude": False}
        self.assertEqual(self.run_wizard("--yes"), 1)
        self.assertFalse((self.home / "config.json").exists())
        self.assertEqual(self.installs, [])

    def test_an_engine_that_is_not_found_is_not_enabled(self):
        self.found = {"codex": True, "claude": False}
        self.run_wizard("--yes", "--engines", "codex,claude", "--no-service")
        data = self.config.load(self.home)
        self.assertEqual((data["engines"]["codex"]["enabled"], data["engines"]["claude"]["enabled"]), (True, False))
        self.assertTrue(any("claude: not found" in line for line in self.out))

    def test_without_a_service_manager_it_says_how_to_run_the_panel(self):
        self.service_kind = None
        self.assertEqual(self.run_wizard("--yes"), 0)
        self.assertEqual(self.installs, [])
        self.assertEqual(self.opened, [])
        self.assertTrue(any("turno serve --start-engines" in line for line in self.out))

    def test_an_open_port_is_not_enough_without_an_active_service(self):
        self.service_active = False  # the port answers, but something else holds it
        self.assertEqual(self.run_wizard("--yes", "--service"), 0)
        self.assertEqual(self.opened, [])
        self.assertFalse(any(line.startswith("panel: ") for line in self.out))
        self.assertTrue(any("port 8765 is in use by another process" in line for line in self.out))

    def test_a_service_that_is_not_running_and_a_silent_port(self):
        self.service_active = False
        self.port_open = False
        self.run_wizard("--yes", "--service")
        self.assertEqual(self.opened, [])
        self.assertTrue(any("turno service status" in line for line in self.out))
        self.assertFalse(any("in use by another" in line for line in self.out))

    def test_yes_keeps_a_different_service_unit_without_asking(self):
        asked = []
        self.onboard.service.install = lambda home, argv, ask=input, out=print: (
            asked.append(ask("replace?")), 1)[1]  # what the real install does with a different unit
        self.assertEqual(self.run_wizard("--yes", "--service"), 0)
        self.assertEqual(asked, ["n"])  # --yes answered "no" itself; input() never ran

    def test_no_terminal_takes_the_defaults_with_a_message(self):
        def eof(question):
            raise EOFError

        code = self.onboard.run(self.args("--no-open"), self.home, ask=eof, out=self.out.append)
        self.assertEqual(code, 0)
        self.assertTrue(any("no terminal" in line for line in self.out))
        self.assertTrue((self.home / "config.json").exists())

    def test_the_command_line_reaches_the_wizard(self):
        seen = []
        with mock.patch.object(self.onboard, "run", lambda args, home: seen.append((args.language, args.yes, home)) or 0), \
                mock.patch("sys.argv", ["turno", "onboard", "--language", "en", "--yes"]):
            with self.assertRaises(SystemExit) as done:
                self.ap.main()
        self.assertEqual(done.exception.code, 0)
        self.assertEqual(seen, [("en", True, self.ap.HOME)])
        self.assertFalse(self.ap.LOG.exists())  # onboard writes no loop log


if __name__ == "__main__":
    unittest.main()
