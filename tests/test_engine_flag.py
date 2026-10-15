import contextlib
import io
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import helpers

FIXTURES = Path(__file__).parent / "fixtures"

CLAUDE_TOML = '[instance]\nname = "claude"\n\n[defaults]\norchestrator_model = "m"\n'


class EngineFlagTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)

    def files(self):
        return self.ap.STATE.name, self.ap.LOG.name, self.ap.RUNS.name, self.ap.HISTORY.name

    def test_default_engine_is_codex(self):
        self.ap.configure()
        self.assertEqual(self.ap.ENGINE, "codex")

    def test_flag_selects_the_engine(self):
        self.ap.configure("claude")
        self.assertEqual(self.ap.ENGINE, "claude")
        self.ap.configure("codex")
        self.assertEqual(self.ap.ENGINE, "codex")

    def test_config_alias_by_file_name(self):
        # The file does not need to exist: the loops of the old launcher pass an absolute path.
        for path in ("claude.toml", "/somewhere/else/claude.toml", str(self.tmp / "claude.toml")):
            self.ap.configure(config_path=path)
            self.assertEqual(self.ap.ENGINE, "claude", path)

    def test_config_alias_by_instance_name(self):
        (self.tmp / "weekend.toml").write_text(CLAUDE_TOML)
        self.ap.configure(config_path="weekend.toml")  # relative: resolved from the data folder
        self.assertEqual(self.ap.ENGINE, "claude")
        self.ap.configure(config_path=str(self.tmp / "weekend.toml"))
        self.assertEqual(self.ap.ENGINE, "claude")

    def test_any_other_config_means_codex(self):
        (self.tmp / "projects.toml").write_text('[defaults]\norchestrator_model = "m"\n')
        (self.tmp / "other.toml").write_text('[instance]\nname = "other"\n')
        (self.tmp / "broken.toml").write_text("[nope")
        for path in ("projects.toml", "other.toml", "broken.toml", "missing.toml"):
            self.ap.configure("claude")
            self.ap.configure(config_path=path)
            self.assertEqual(self.ap.ENGINE, "codex", path)

    def test_flag_wins_over_the_deprecated_config(self):
        self.ap.configure("codex", "claude.toml")
        self.assertEqual(self.ap.ENGINE, "codex")

    def test_file_names_per_engine_did_not_change(self):
        self.ap.configure("codex")
        self.assertEqual(self.files(), ("state.json", "autopilot.log", "runs", "history-codex.jsonl"))
        self.ap.configure("claude")
        self.assertEqual(self.files(), ("state-claude.json", "autopilot-claude.log", "runs-claude", "history-claude.jsonl"))
        self.assertEqual({self.ap.STATE.parent, self.ap.LOG.parent, self.ap.RUNS.parent, self.ap.HISTORY.parent}, {self.tmp})

    def test_the_alias_gives_the_same_files_as_the_flag(self):
        self.ap.configure(config_path=str(self.tmp / "claude.toml"))
        self.assertEqual(self.files(), ("state-claude.json", "autopilot-claude.log", "runs-claude", "history-claude.jsonl"))

    def test_resolve_config_and_the_config_global_are_gone(self):
        self.assertFalse(hasattr(self.ap, "resolve_config"))
        self.assertFalse(hasattr(self.ap, "CONFIG"))
        self.assertFalse(hasattr(self.ap, "LOOPS"))

    def run_main(self, *argv):
        seen = []
        self.ap.cmd_status = lambda args: seen.append(self.ap.ENGINE)
        with mock.patch.object(sys, "argv", ["autopilot.py", *argv]), contextlib.redirect_stderr(io.StringIO()):
            self.ap.main()
        return seen

    def test_main_takes_the_engine_flag_and_the_old_alias(self):
        self.assertEqual(self.run_main("status"), ["codex"])
        self.assertEqual(self.run_main("--engine", "claude", "status"), ["claude"])
        self.assertEqual(self.run_main("--engine", "codex", "status"), ["codex"])
        self.assertEqual(self.run_main("--config", str(self.tmp / "claude.toml"), "status"), ["claude"])
        self.assertEqual(self.run_main("--config", "projects.toml", "status"), ["codex"])

    def test_main_refuses_an_unknown_engine(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_main("--engine", "gemini", "status")
        self.assertEqual(raised.exception.code, 2)


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.projects = helpers.make_home(self.tmp, topic="segredo")

        def edit(data):
            data["projects"][0]["weekly_cap"] = {"codex": 95, "claude": 70}
            data["engines"]["claude"]["orchestrator_model"] = "claude-model"
            data["engines"]["claude"]["schedule"] = {"start": "fri 19:00", "end": "sun 03:00", "start_margin_minutes": 15}
            data["notify"]["decision_timeout_hours"] = 2

        helpers.write_config(self.tmp, [{"name": n, "path": str(p)} for n, p in self.projects.items()], edit=edit)

    def test_each_engine_reads_its_part_of_config_json(self):
        self.ap.configure("codex")
        codex = self.ap.load_config()
        self.ap.configure("claude")
        claude = self.ap.load_config()
        self.assertEqual([p["name"] for p in codex["project"]], ["my-app", "my-api", "side-project"])
        self.assertEqual((codex["project"][0]["weekly_cap"], claude["project"][0]["weekly_cap"]), (95, 70))
        self.assertEqual(claude["defaults"]["orchestrator_model"], "claude-model")
        self.assertEqual((codex["engine"]["kind"], claude["engine"]["kind"]), ("codex", "claude"))
        self.assertNotIn("schedule", codex)
        self.assertEqual(claude["schedule"]["start"], "fri 19:00")
        self.assertTrue(codex["enabled"])

    def test_secrets_fill_the_ntfy_topic(self):
        cfg = self.ap.load_config()
        self.assertEqual(cfg["notify"]["topic"], "segredo")
        self.assertEqual(cfg["notify"]["decision_timeout_hours"], 2)

    def test_config_without_secrets_still_loads(self):
        (self.tmp / "secrets.toml").unlink()
        self.assertNotIn("topic", self.ap.load_config()["notify"])

    def test_the_toml_files_are_imported_the_first_time(self):
        (self.tmp / "config.json").unlink()
        shutil.copy(FIXTURES / "projects.toml", self.tmp / "projects.toml")
        shutil.copy(FIXTURES / "claude.toml", self.tmp / "claude.toml")
        self.ap.configure("claude")
        cfg = self.ap.load_config()
        self.assertTrue((self.tmp / "config.json").exists())
        self.assertEqual([p["name"] for p in cfg["project"]], [p["name"] for p in tomllib.loads(
            (FIXTURES / "claude.toml").read_text())["project"]])
        self.assertEqual(cfg["instance"], {"name": "claude"})

    def test_no_config_at_all_is_an_empty_disabled_engine(self):
        (self.tmp / "config.json").unlink()
        cfg = self.ap.load_config()
        self.assertEqual((cfg["project"], cfg["enabled"]), ([], False))

    def test_load_state_and_ensure_state_give_every_project_an_entry(self):
        cfg = self.ap.load_config()
        state = self.ap.load_state(cfg)
        self.assertEqual(state["side-project"]["status"], "active")
        cfg["project"].append({"name": "novo", "path": "/tmp/novo", "weekly_cap": 100})
        state["my-app"]["status"] = "blocked"
        self.ap.ensure_state(cfg, state)
        self.assertEqual(set(state["novo"]), set(state["side-project"]))
        self.assertEqual((state["novo"]["status"], state["novo"]["interrupted"], state["novo"]["retry_at"]),
                         ("active", False, 0))
        self.assertEqual(state["novo"]["cost"], {"primary": 8.0, "secondary": 1.5})
        self.assertEqual(state["my-app"]["status"], "blocked")  # an existing entry is left alone


class LoopsTest(unittest.TestCase):
    """Starting and stopping by engine name; tmux, pids and signals are all simulated."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.calls = []

        def tmux(*args):
            self.calls.append(args)
            return (1 if args[0] == "has-session" else 0), ""

        self.ap.tmux = tmux
        self.ap.running_pid = lambda name: None
        self.ap.loop_pids = lambda engine: []

    def start(self, names=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ap.start_loops(names)

    def launches(self):
        return [c for c in self.calls if c[0] in ("new-session", "new-window") and "logs" not in c]

    def test_loops_are_the_enabled_engines_in_order(self):
        self.assertEqual(self.ap.loops(), [])  # no config yet: both engines disabled
        helpers.write_config(self.tmp, enabled=("claude", "codex"))
        self.assertEqual(self.ap.loops(), ["codex", "claude"])
        helpers.write_config(self.tmp, enabled=("claude",))
        self.assertEqual(self.ap.loops(), ["claude"])
        helpers.write_config(self.tmp, edit=lambda d: d["engines"].pop("claude"))
        self.assertEqual(self.ap.loops(), ["codex"])

    def test_loop_command_uses_the_engine_flag(self):
        script = self.ap.shlex.quote(str(self.ap.BASE.parent / "autopilot.py"))
        home = self.ap.shlex.quote(str(self.tmp))
        self.assertEqual(self.ap.loop_command("codex"), f"TURNO_HOME={home} python3 {script} --engine codex run")
        self.assertEqual(self.ap.loop_command("claude"), f"TURNO_HOME={home} python3 {script} --engine claude run")

    def test_log_file_per_engine(self):
        self.assertEqual(self.ap.log_file("codex"), self.tmp / "autopilot.log")
        self.assertEqual(self.ap.log_file("claude"), self.tmp / "autopilot-claude.log")

    def test_is_loop_recognises_the_new_form(self):
        script = str(self.ap.BASE / "autopilot.py")
        new = lambda engine: ["python3", script, "--engine", engine, "run"]
        self.assertTrue(self.ap.is_loop(new("codex"), "codex"))
        self.assertFalse(self.ap.is_loop(new("codex"), "claude"))
        self.assertTrue(self.ap.is_loop(new("claude"), "claude"))
        self.assertFalse(self.ap.is_loop(new("claude"), "codex"))
        self.assertTrue(self.ap.is_loop(["python3", script, "--engine=claude", "run"], "claude"))
        self.assertFalse(self.ap.is_loop(["python3", script, "--engine", "claude", "status"], "claude"))

    def test_is_loop_still_recognises_loops_started_the_old_way(self):
        script = str(self.ap.BASE / "autopilot.py")
        old_codex = ["python3", script, "run"]
        old_claude = ["python3", script, "--config", "/home/someone/.turno/claude.toml", "run"]
        self.assertTrue(self.ap.is_loop(old_codex, "codex"))
        self.assertFalse(self.ap.is_loop(old_codex, "claude"))
        self.assertTrue(self.ap.is_loop(old_claude, "claude"))
        self.assertFalse(self.ap.is_loop(old_claude, "codex"))
        self.assertTrue(self.ap.is_loop(["python3", script, "--config", "claude.toml", "run"], "claude"))
        self.assertTrue(self.ap.is_loop(["python3", script, "--config=/x/claude.toml", "run"], "claude"))
        # Any other config file was a Codex loop.
        self.assertTrue(self.ap.is_loop(["python3", script, "--config", "/x/projects.toml", "run"], "codex"))
        self.assertFalse(self.ap.is_loop(["python3", script, "--config", "/x/projects.toml", "run"], "claude"))

    def test_is_loop_ignores_what_is_not_a_loop(self):
        script = str(self.ap.BASE / "autopilot.py")
        self.assertFalse(self.ap.is_loop(["python3", script, "status"], "codex"))
        self.assertFalse(self.ap.is_loop(["python3", script, "--engine", "claude"], "claude"))
        self.assertFalse(self.ap.is_loop(["tmux", "new", "-s", "autopilot", f"python3 {script} run"], "codex"))
        self.assertFalse(self.ap.is_loop(["python3", "/x/other.py", "--engine", "codex", "run"], "codex"))
        self.assertFalse(self.ap.is_loop([], "codex"))

    def test_start_launches_the_enabled_engines_with_the_flag(self):
        helpers.write_config(self.tmp)
        self.assertEqual(self.start(), {"codex": "started", "claude": "started"})
        commands = [c[-1] for c in self.launches()]
        self.assertEqual(commands, [self.ap.loop_command("codex"), self.ap.loop_command("claude")])

    def test_start_skips_a_disabled_engine(self):
        helpers.write_config(self.tmp, enabled=("codex",))
        self.assertEqual(self.start(), {"codex": "started", "claude": "disabled"})
        self.assertEqual([c[-1] for c in self.launches()], [self.ap.loop_command("codex")])

    def test_start_of_a_disabled_engine_by_name_starts_nothing(self):
        helpers.write_config(self.tmp, enabled=("codex",))
        self.assertEqual(self.start(["claude"]), {"claude": "disabled"})
        self.assertEqual(self.launches(), [])

    def test_start_does_not_duplicate_a_loop_started_the_old_way(self):
        helpers.write_config(self.tmp)
        self.ap.loop_pids = lambda engine: [4242] if engine == "claude" else []
        self.assertEqual(self.start(), {"codex": "started", "claude": "already running"})
        self.assertEqual(len(self.launches()), 1)

    def test_loop_pids_asks_is_loop_with_the_engine_name(self):
        self.ap = helpers.load(self.tmp)  # a fresh module, with its own loop_pids (it only reads /proc)
        asked = set()
        self.ap.is_loop = lambda argv, engine: asked.add(engine) or False
        self.assertEqual(self.ap.loop_pids("claude"), [])
        self.assertEqual(asked, {"claude"})

    def test_stop_reaches_a_disabled_engine_that_is_still_running(self):
        helpers.write_config(self.tmp, enabled=("codex",))
        killed = []
        self.ap.running_pid = lambda name: None if killed else {"codex": None, "claude": 222}[name]
        self.ap.loop_pids = lambda engine: [] if killed else ([444] if engine == "claude" else [])
        with mock.patch("os.kill", lambda pid, sig: killed.append((pid, sig))), mock.patch("time.sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            result = self.ap.stop_loops()
        self.assertEqual(result, {"codex": "not running", "claude": "stopped"})
        self.assertEqual(sorted(killed), [(222, self.ap.signal.SIGINT), (444, self.ap.signal.SIGINT)])

    def test_stop_does_not_need_a_readable_config(self):
        helpers.write_config_text(self.tmp, "{nope")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.ap.stop_loops(), {"codex": "not running", "claude": "not running"})

    def test_start_with_an_invalid_config_starts_nothing_and_says_why(self):
        helpers.write_config_text(self.tmp, "{nope")
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()):
            self.ap.cmd_start(None)
        self.assertIn("config.json is invalid; nothing started", str(raised.exception.code))
        self.assertEqual(self.calls, [])

    def test_logs_window_shows_both_logs(self):
        helpers.write_config(self.tmp, enabled=("codex",))
        self.start()
        tails = [c[-1] for c in self.calls if c[0] in ("new-window", "split-window") and "tail" in c[-1]]
        self.assertEqual(len(tails), 2)
        self.assertIn(str(self.tmp / "autopilot.log"), tails[0])
        self.assertIn(str(self.tmp / "autopilot-claude.log"), tails[1])


if __name__ == "__main__":
    unittest.main()
