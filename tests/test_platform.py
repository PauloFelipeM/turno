"""Linux, macOS and WSL, simulated: platform detection, Docker, loop detection without /proc, the instance file."""

import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers


class PlatformBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.proc = self.tmp / "proc"
        self.proc.mkdir()
        self.ap.PROC = self.proc
        env = mock.patch.dict(os.environ)  # restored after the test
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WSL_DISTRO_NAME", None)  # these tests run on WSL too


class DetectionTest(PlatformBase):
    def kind(self, version=None, platform="linux"):
        if version is not None:
            (self.proc / "version").write_text(version)
        with mock.patch.object(self.ap.sys, "platform", platform):
            return self.ap.platform_kind()

    def test_linux_wsl_and_macos(self):
        self.assertEqual(self.kind("Linux version 6.8.0-45-generic (buildd@lcy02-amd64-075)"), "linux")
        self.assertEqual(self.kind("Linux version 5.15.153.1-microsoft-standard-WSL2"), "wsl")
        (self.proc / "version").unlink()
        self.assertEqual(self.kind(platform="darwin"), "macos")
        self.assertEqual(self.kind(platform="linux"), "linux")
        os.environ["WSL_DISTRO_NAME"] = "Ubuntu"
        self.assertEqual(self.kind(platform="linux"), "wsl")


class DockerTest(PlatformBase):
    def ensure(self, kind, up_after=None, seconds=2):
        """ensure_docker on `kind`; Docker answers from the call after `up_after` (never when None)."""
        calls = []

        def up():
            calls.append(1)
            return up_after is not None and len(calls) > up_after
        self.ap.docker_up = up
        self.ap.platform_kind = lambda: kind
        self.ap.DOCKER_START_SECONDS, self.ap.DOCKER_POLL_SECONDS = seconds, 0
        with mock.patch.object(self.ap.subprocess, "Popen") as popen:
            ok = self.ap.ensure_docker({})
        return ok, popen

    def test_already_up_on_any_platform(self):
        self.ap.platform_kind = lambda: self.fail("no platform check when Docker answers")
        self.ap.docker_up = lambda: True
        self.assertTrue(self.ap.ensure_docker({}))

    def test_linux_starts_nothing_and_says_so(self):
        ok, popen = self.ensure("linux")
        self.assertFalse(ok)
        popen.assert_not_called()
        self.assertIn("Docker is down; turno does not start it on Linux", self.ap.LOG.read_text())

    def test_macos_opens_docker_desktop_and_waits(self):
        ok, popen = self.ensure("macos", up_after=2)
        self.assertTrue(ok)
        self.assertEqual(popen.call_args.args[0], ["open", "-a", "Docker"])
        self.assertIn("Docker is up", self.ap.LOG.read_text())

    def test_macos_gives_up_after_the_wait(self):
        started = time.monotonic()
        ok, popen = self.ensure("macos", seconds=0.2)
        self.assertFalse(ok)
        self.assertLess(time.monotonic() - started, 5)
        popen.assert_called_once()

    def test_wsl_opens_windows_docker_desktop(self):
        exe = self.tmp / "Docker Desktop.exe"
        exe.write_text("")
        self.ap.DOCKER_DESKTOP = exe
        ok, popen = self.ensure("wsl", up_after=1)
        self.assertTrue(ok)
        self.assertEqual(popen.call_args.args[0], [str(exe)])

    def test_wsl_without_docker_desktop_waits(self):
        self.ap.DOCKER_DESKTOP = self.tmp / "missing.exe"
        ok, popen = self.ensure("wsl")
        self.assertFalse(ok)
        popen.assert_not_called()

    def test_docker_down_on_linux_makes_the_project_wait(self):
        self.ap.docker_up = lambda: False
        self.ap.platform_kind = lambda: "linux"
        self.assertEqual(self.ap.prepare_docker({"project": []}, {"name": "x", "path": str(self.tmp)}), "docker-down")


class LoopDetectionTest(PlatformBase):
    def test_reads_proc_when_it_has_processes(self):
        d = self.proc / "55"
        d.mkdir()
        (d / "cmdline").write_bytes(b"python3\0/x/autopilot.py\0--engine\0claude\0run\0")
        with mock.patch.object(self.ap.subprocess, "run") as run:
            self.assertEqual(self.ap.loop_pids("claude"), [55])
            self.assertEqual(self.ap.loop_pids("codex"), [])
        run.assert_not_called()

    def test_falls_back_to_ps_without_proc(self):
        out = ("  101 python3 /x/autopilot.py --engine claude run\n"
               "  102 /usr/bin/python3 /x/autopilot.py run\n"
               "  103 python3 /x/autopilot.py --config /x/claude.toml run\n"
               f"  {os.getpid()} python3 /x/autopilot.py run\n"
               "garbage\n\n  104 -bash\n")
        done = subprocess.CompletedProcess(["ps"], 0, stdout=out, stderr="")
        with mock.patch.object(self.ap.subprocess, "run", return_value=done) as run:
            self.assertEqual(self.ap.loop_pids("claude"), [101, 103])
            self.assertEqual(self.ap.loop_pids("codex"), [102])
        self.assertEqual(run.call_args.args[0], ["ps", "-axo", "pid=,command="])

    def test_ps_fallback_with_a_path_that_has_a_space(self):
        out = ("  201 /usr/bin/python3 /Users/Ana Lima/turno/autopilot.py --engine claude run\n"
               "  202 python3 /Users/Ana Lima/turno/autopilot.py run\n"
               "  203 python3 /x/not autopilot.py.bak run\n")
        done = subprocess.CompletedProcess(["ps"], 0, stdout=out, stderr="")
        with mock.patch.object(self.ap.subprocess, "run", return_value=done):
            self.assertEqual(self.ap.loop_pids("claude"), [201])
            self.assertEqual(self.ap.loop_pids("codex"), [202])

    def test_without_proc_or_ps_finds_nothing(self):
        with mock.patch.object(self.ap.subprocess, "run", side_effect=FileNotFoundError("ps")):
            self.assertEqual(self.ap.loop_pids("codex"), [])
        with mock.patch.object(self.ap.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 10)):
            self.assertEqual(self.ap.loop_pids("codex"), [])


class InstanceFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)

    def path(self):
        return self.ap.coord.SHARED / "instance-codex.json"

    def test_written_atomically_and_removed(self):
        self.ap.write_instance_info("codex")
        info = json.loads(self.path().read_text())
        self.assertEqual((info["pid"], info["sigusr1"]), (os.getpid(), True))
        self.assertIsInstance(info["started_at"], float)
        self.assertEqual(list(self.ap.coord.SHARED.glob("*.tmp")), [])
        self.ap.remove_instance_info("codex")
        self.assertFalse(self.path().exists())

    def test_another_pids_file_is_left_alone(self):
        self.ap.coord.shared_dir()
        self.path().write_text(json.dumps({"pid": 1, "sigusr1": True}))
        self.ap.remove_instance_info("codex")
        self.assertTrue(self.path().exists())

    def test_a_write_failure_is_logged_and_never_raises(self):
        blocker = self.tmp / "not-a-folder"
        blocker.write_text("x")
        self.ap.coord.SHARED = blocker
        self.ap.write_instance_info("codex")
        self.ap.remove_instance_info("codex")
        self.assertIn("could not write instance-codex.json", self.ap.LOG.read_text())

    def test_the_loop_writes_it_at_startup_and_removes_it_on_exit(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))
        cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5}, "project": []}
        self.ap.load_config = lambda: cfg
        self.ap.engines.make_engine = lambda cfg: helpers.FakeEngine(["true"], name="codex")
        seen = {}

        def loop_turn(cfg, engine, state):
            seen["info"] = json.loads(self.path().read_text())
            raise SystemExit(130)  # what Ctrl+C does
        self.ap.loop_turn = loop_turn
        with self.assertRaises(SystemExit):
            self.ap.cmd_run(None)
        self.assertEqual(seen["info"]["pid"], os.getpid())
        self.assertFalse(self.path().exists())


if __name__ == "__main__":
    unittest.main()
