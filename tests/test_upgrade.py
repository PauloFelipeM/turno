"""`turno restart` and `turno update`, with the loops, the service and pipx/uv faked."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers


class FakeDist:
    def __init__(self, direct=None):
        self.direct = direct

    def read_text(self, name):
        return json.dumps(self.direct) if name == "direct_url.json" and self.direct is not None else None


class RestartTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.home)
        from turno import upgrade
        self.upgrade = upgrade
        self.events = []
        self.running = {"codex", "claude"}
        self.ap.running_pid = lambda k: 100 if k in self.running else None
        self.ap.loop_pids = lambda k: []
        self.ap.stop_loops = lambda names=None: self.events.append(("stop", names)) or self.running.clear()
        self.ap.start_loops = lambda names=None: self.events.append(("start", names))
        self.status = {"kind": "systemd", "installed": True, "active": True}
        self.restarted = True
        for name, fn in (("status", lambda: self.status), ("restart", self.fake_restart)):
            patch = mock.patch.object(upgrade.service, name, fn)
            patch.start()
            self.addCleanup(patch.stop)
        self.out = []

    def fake_restart(self):
        self.events.append(("service",))
        return self.restarted

    def test_with_a_service_the_panel_starts_the_engines(self):
        helpers.write_config(self.home, enabled=("codex", "claude"))
        self.assertEqual(self.upgrade.restart(self.home, out=self.out.append), 0)
        self.assertEqual(self.events, [("stop", None), ("service",)])

    def test_a_running_engine_without_autostart_is_started_here(self):
        helpers.write_config(self.home, enabled=("codex", "claude"),
                             edit=lambda d: d["engines"]["claude"].update(autostart=False))
        self.upgrade.restart(self.home, out=self.out.append)
        self.assertEqual(self.events, [("stop", None), ("service",), ("start", ["claude"])])

    def test_without_a_service_the_loops_that_ran_start_again(self):
        self.status = {"kind": None, "installed": False, "active": False}
        self.running = {"claude"}
        helpers.write_config(self.home, enabled=("codex", "claude"))
        self.assertEqual(self.upgrade.restart(self.home, out=self.out.append), 0)
        self.assertEqual(self.events, [("stop", None), ("start", ["claude"])])
        self.assertTrue(any("restart the panel yourself" in line for line in self.out))

    def test_a_service_that_does_not_restart_leaves_the_loops_running(self):
        self.restarted = False
        helpers.write_config(self.home, enabled=("codex", "claude"))
        self.assertEqual(self.upgrade.restart(self.home, out=self.out.append), 1)
        self.assertEqual(self.events, [("stop", None), ("service",), ("start", ["codex", "claude"])])

    def stuck_setup(self, finishes):
        """stop_loops reports codex "still stopping"; `finishes` says whether the loop ends in the extra wait."""
        self.ap.stop_loops = lambda names=None: self.events.append(("stop", names)) or {"codex": "still stopping", "claude": "stopped"}
        self.running = {"codex"}
        self.upgrade.STUCK_WAIT = 60
        self.clock = [0.0]
        self.upgrade._sleep = lambda s: (self.clock.__setitem__(0, self.clock[0] + s),
                                         finishes and self.clock[0] >= 10 and self.running.clear())
        patch = mock.patch.object(self.upgrade.time, "monotonic", lambda: self.clock[0])
        patch.start()
        self.addCleanup(patch.stop)
        helpers.write_config(self.home, enabled=("codex", "claude"))

    def test_a_loop_that_is_still_stopping_gets_60_more_seconds(self):
        self.stuck_setup(finishes=True)
        self.upgrade.restart(self.home, out=self.out.append)
        self.assertEqual(self.events, [("stop", None), ("service",)])
        self.assertFalse(any("Iniciar" in line for line in self.out))

    def test_a_loop_that_never_finishes_is_reported_and_not_started(self):
        self.stuck_setup(finishes=False)
        self.status = {"kind": None, "installed": False, "active": False}
        self.upgrade.restart(self.home, out=self.out.append)
        self.assertEqual(self.events, [("stop", None)])  # nothing is started over the old loop
        self.assertTrue(any("codex" in line and "Iniciar" in line for line in self.out))
        self.assertGreaterEqual(self.clock[0], 60)
        self.assertIn("Iniciar", self.ap.LOG.read_text())

    def test_nothing_running_starts_nothing_without_a_service(self):
        self.status = {"kind": None, "installed": False, "active": False}
        self.running = set()
        self.upgrade.restart(self.home, out=self.out.append)
        self.assertEqual(self.events, [("stop", None)])


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.home)
        from turno import upgrade
        self.upgrade = upgrade
        self.calls = []
        self.codes = {}

        def run(argv, **kw):
            self.calls.append((argv, kw.get("env", {}).get("TURNO_HOME")))
            return subprocess.CompletedProcess(argv, self.codes.get(argv[0], 0))

        patch = mock.patch.object(upgrade, "_run", run)
        patch.start()
        self.addCleanup(patch.stop)
        self.out = []

    def installed_as(self, prefix, direct=None):
        self.ap.runs_as_module = lambda: True
        self.ap.turno_argv = lambda: [sys.executable, "-m", "turno"]
        patches = [mock.patch.object(self.upgrade.importlib.metadata, "distribution", lambda name: FakeDist(direct)),
                   mock.patch.object(self.upgrade.sys, "prefix", prefix)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_detects_the_installer(self):
        self.installed_as("/home/me/.local/share/pipx/venvs/turno")
        self.assertEqual(self.upgrade.installer(), "pipx")

    def test_detects_uv(self):
        self.installed_as("/home/me/.local/share/uv/tools/turno")
        self.assertEqual(self.upgrade.installer(), "uv")

    def test_detects_an_editable_install(self):
        self.installed_as("/home/me/.local/share/pipx/venvs/turno", {"url": "file:///home/me/turno", "dir_info": {"editable": True}})
        self.assertEqual(self.upgrade.installer(), "editable")

    def test_a_checkout_that_is_not_installed_is_source(self):
        self.ap.runs_as_module = lambda: False
        self.assertEqual(self.upgrade.installer(), "source")

    def test_pipx_upgrade_then_the_new_code_restarts(self):
        self.installed_as("/home/me/.local/share/pipx/venvs/turno")
        self.assertEqual(self.upgrade.update(self.home, out=self.out.append), 0)
        self.assertEqual(self.calls, [(["pipx", "upgrade", "turno"], None),
                                      ([sys.executable, "-m", "turno", "restart"], str(self.home))])

    def test_uv_upgrade(self):
        self.installed_as("/home/me/.local/share/uv/tools/turno")
        self.upgrade.update(self.home, out=self.out.append)
        self.assertEqual(self.calls[0][0], ["uv", "tool", "upgrade", "turno"])

    def test_a_failed_upgrade_restarts_nothing(self):
        self.installed_as("/home/me/.local/share/pipx/venvs/turno")
        self.codes = {"pipx": 1}
        self.assertEqual(self.upgrade.update(self.home, out=self.out.append), 1)
        self.assertEqual(len(self.calls), 1)

    def test_editable_and_other_installs_only_explain(self):
        self.installed_as("/home/me/.local/share/pipx/venvs/turno", {"dir_info": {"editable": True}})
        self.assertEqual(self.upgrade.update(self.home, out=self.out.append), 1)
        self.assertIn("git pull", self.out[-1])
        self.assertIn("npm --prefix ui ci && npm --prefix ui run build", self.out[-1])
        self.installed_as("/usr/local")
        self.assertEqual(self.upgrade.update(self.home, out=self.out.append), 1)
        self.assertIn("pip install -U turno", self.out[-1])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
