"""The `turno` package, its entry points and the root shim (run as real processes, with a temporary TURNO_HOME)."""

import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

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


    def test_version_flag(self):
        from turno import __version__
        r = self.run_cli([sys.executable, "-m", "turno", "--version"], cwd=helpers.ROOT)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"turno {__version__}"))


class CommandLinesTest(unittest.TestCase):
    def setUp(self):
        self.ap = helpers.load(Path(tempfile.mkdtemp()))

    def test_the_three_forms_of_a_loop(self):
        for argv, engine in (
                (["/usr/bin/python3", "-m", "turno", "--engine", "claude", "run"], "claude"),
                (["/usr/bin/python3", "-m", "turno", "run"], "codex"),
                (["python3", "/x/autopilot.py", "--engine", "claude", "run"], "claude"),
                (["python3", "/x/autopilot.py", "--config", "/x/claude.toml", "run"], "claude"),
                (["python3", "/x/autopilot.py", "run"], "codex"),
                (["/v/bin/python", "/v/bin/turno", "--engine=claude", "run"], "claude"),
                (["turno", "run"], "codex")):
            self.assertEqual(self.ap.loop_engine(argv), engine, argv)
            self.assertTrue(self.ap.is_loop(argv, engine), argv)

    def test_what_is_not_a_loop(self):
        for argv in (["python3", "-m", "turno", "serve"], ["python3", "-m", "turnover", "run"],
                     ["python3", "-m", "turno", "--engine", "claude"], ["python3", "/x/turno.py", "run"],
                     ["tmux", "new", "-s", "autopilot", "python3 -m turno run"], []):
            self.assertIsNone(self.ap.loop_engine(argv), argv)

    def test_any_turno_command_counts_for_the_pid_check(self):
        self.assertTrue(self.ap.is_turno_command(["python3", "-m", "turno", "serve"]))
        self.assertTrue(self.ap.is_turno_command(["python3", "/x/autopilot.py", "status"]))
        self.assertFalse(self.ap.is_turno_command(["python3", "/x/other.py", "run"]))

    def test_ps_line_with_dash_m_and_a_space_in_the_interpreter_path(self):
        line = "/Users/Ana Lima/.local/pipx/venvs/turno/bin/python -m turno --engine claude run"
        self.assertEqual(self.ap._split_command(line),
                         ["/Users/Ana Lima/.local/pipx/venvs/turno/bin/python", "-m", "turno", "--engine", "claude", "run"])

    def test_the_panel_pid_check_accepts_a_dash_m_loop(self):
        from turno import panel
        home = Path(tempfile.mkdtemp())
        proc = home / "proc"
        (proc / str(os.getpid())).mkdir(parents=True)
        (home / "shared").mkdir()
        (home / "shared" / "instance-claude.lock").write_text(str(os.getpid()))
        cmdline = proc / str(os.getpid()) / "cmdline"
        with mock.patch.object(panel, "PROC", proc):
            cmdline.write_bytes(b"/usr/bin/python3\0-m\0turno\0--engine\0claude\0run\0")
            self.assertEqual(panel.loop_pid(home, "claude"), os.getpid())
            cmdline.write_bytes(b"/usr/bin/python3\0/old/checkout/autopilot.py\0--engine\0claude\0run\0")
            self.assertEqual(panel.loop_pid(home, "claude"), os.getpid())
            cmdline.write_bytes(b"/usr/bin/python3\0-m\0http.server\0")
            self.assertIsNone(panel.loop_pid(home, "claude"))  # the pid was reused by something else

    def test_a_checkout_that_is_not_installed_does_not_run_as_a_module(self):
        self.assertFalse(self.ap.runs_as_module())  # the tests run from the checkout, never installed
        self.assertEqual(self.ap.turno_argv(), [sys.executable, str(helpers.ROOT / "autopilot.py")])


class PackageDataTest(unittest.TestCase):
    def test_every_data_file_is_declared_in_pyproject(self):
        meta = tomllib.loads((helpers.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        globs = meta["tool"]["setuptools"]["package-data"]["turno"]
        files = [p.relative_to(helpers.PKG).as_posix() for p in helpers.PKG.rglob("*")
                 if p.is_file() and p.suffix not in (".py", ".pyc")]
        self.assertTrue(files)
        for rel in files:
            self.assertTrue(any(fnmatch.fnmatchcase(rel, g) and rel.count("/") == g.count("/") for g in globs), rel)

    def test_console_script_and_version(self):
        from turno import __version__
        meta = tomllib.loads((helpers.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(meta["project"]["scripts"], {"turno": "turno.cli:main"})
        self.assertEqual(meta["project"]["requires-python"], ">=3.11")
        self.assertEqual(meta["project"]["dependencies"], [])
        self.assertEqual(meta["tool"]["setuptools"]["dynamic"]["version"], {"attr": "turno.__version__"})
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


@unittest.skipUnless(os.environ.get("TURNO_TEST_WHEEL") == "1", "builds a wheel; set TURNO_TEST_WHEEL=1 (needs pip and the network)")
class WheelTest(unittest.TestCase):
    """Builds the wheel and installs it in a throwaway virtualenv: the package data must come along."""

    def test_installed_wheel_finds_its_data_and_runs(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        src = work / "src"
        shutil.copytree(helpers.ROOT, src, ignore=shutil.ignore_patterns(".git", ".superpowers", "build", "*.egg-info", "__pycache__"))
        venv = work / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        py = venv / "bin" / "python"
        subprocess.run([str(py), "-m", "pip", "wheel", str(src), "--no-deps", "-q", "-w", str(work / "dist")], check=True)
        wheel = next((work / "dist").glob("turno-*.whl"))
        subprocess.run([str(py), "-m", "pip", "install", "-q", str(wheel)], check=True)
        env = {**os.environ, "TURNO_HOME": str(work / "home")}
        env.pop("PYTHONPATH", None)
        probe = ("import turno.autopilot as a, turno.engines as e, turno.server as s; "
                 "assert (a.PROMPTS / 'unit.md').is_file() and (a.PROMPTS / 'setup.md').is_file(); "
                 "assert e.ROLES.is_file() and len(list(e.CODEX_AGENTS.glob('*.toml'))) == 3; "
                 "assert (s.WEB / 'index.html').is_file() and (s.WEB / 'i18n' / 'en.js').is_file(); "
                 "assert a.runs_as_module() and a.turno_argv()[1:] == ['-m', 'turno']")
        subprocess.run([str(py), "-c", probe], cwd=work, env=env, check=True)
        out = subprocess.run([str(venv / "bin" / "turno"), "--version"], env=env, capture_output=True, text=True, check=True)
        self.assertTrue(out.stdout.startswith("turno "))
        out = subprocess.run([str(venv / "bin" / "turno"), "--engine", "claude", "status"], cwd=work, env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
