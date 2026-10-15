"""`autostart`, `update_check` and `serve --start-engines` (tmux is always faked)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers


class AutostartConfigTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.home)
        from turno import config
        self.config = config

    def test_autostart_defaults_on_and_must_be_a_boolean(self):
        data = self.config.empty()
        self.assertIs(data["engines"]["codex"]["autostart"], True)
        self.assertIs(data["engines"]["claude"]["autostart"], True)
        data["engines"]["claude"]["autostart"] = "yes"
        self.assertEqual(self.config.validate(data), ["engines.claude.autostart must be true or false"])

    def test_a_config_without_autostart_is_still_valid(self):
        data = self.config.empty()
        del data["engines"]["codex"]["autostart"]
        self.assertEqual(self.config.validate(data), [])

    def test_update_check_must_be_a_boolean(self):
        data = self.config.empty()
        data["update_check"] = False
        self.assertEqual(self.config.validate(data), [])
        data["update_check"] = "no"
        self.assertEqual(self.config.validate(data), ["update_check must be true or false"])

    def test_the_panel_can_switch_autostart(self):
        from turno import control
        helpers.write_config(self.home)
        self.assertEqual(control.put_engine(self.home, "claude", {"autostart": False})[0], 200)
        self.assertIs(self.config.load(self.home)["engines"]["claude"]["autostart"], False)
        with self.assertRaises(control.ApiError):
            control.put_engine(self.home, "claude", {"autostart": "no"})


class StartEnginesTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.home)
        from turno import control
        self.control = control
        self.calls = []

        def tmux(*args):
            self.calls.append(args)
            return (1, "") if args[0] == "has-session" else (0, "")

        self.ap.tmux = tmux
        self.ap.loop_pids = lambda engine: []
        self.ap.runs_as_module = lambda: True
        self.running = set()
        patch = mock.patch.object(control.panel, "loop_pid", lambda home, kind: 4242 if kind in self.running else None)
        patch.start()
        self.addCleanup(patch.stop)

    def launched(self):
        return [c[-1] for c in self.calls if c[0] in ("new-session", "new-window") and "logs" not in c]

    def test_starts_only_enabled_autostart_engines_that_are_not_running(self):
        helpers.write_config(self.home, enabled=("codex", "claude"),
                             edit=lambda d: d["engines"]["claude"].update(autostart=False))
        self.assertEqual(self.control.start_engines(self.home), {"codex": "started"})
        self.assertEqual(len(self.launched()), 1)
        self.assertTrue(self.launched()[0].endswith("-m turno --engine codex run"))

    def test_a_disabled_engine_is_never_started(self):
        helpers.write_config(self.home, enabled=("claude",))
        self.assertEqual(self.control.start_engines(self.home), {"claude": "started"})

    def test_a_running_loop_is_left_alone(self):
        helpers.write_config(self.home, enabled=("codex", "claude"))
        self.running = {"codex"}
        self.assertEqual(self.control.start_engines(self.home), {"claude": "started"})

    def test_nothing_to_start_runs_no_tmux(self):
        helpers.write_config(self.home, enabled=())
        self.assertEqual(self.control.start_engines(self.home), {})
        self.assertEqual(self.calls, [])

    def test_an_invalid_config_starts_nothing(self):
        helpers.write_config_text(self.home, "{not json")
        with mock.patch("builtins.print"):
            self.assertEqual(self.control.start_engines(self.home), {})
        self.assertEqual(self.calls, [])

    def test_serve_flag_reaches_the_server(self):
        seen = []
        from turno import server
        with mock.patch.object(server, "serve", lambda home, port, start_engines=False: seen.append((port, start_engines))), \
                mock.patch("sys.argv", ["turno", "serve", "--start-engines"]):
            self.ap.main()
        with mock.patch.object(server, "serve", lambda home, port, start_engines=False: seen.append((port, start_engines))), \
                mock.patch("sys.argv", ["turno", "serve", "--port", "8800"]):
            self.ap.main()
        self.assertEqual(seen, [(8765, True), (8800, False)])


if __name__ == "__main__":
    unittest.main()
