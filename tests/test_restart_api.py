"""POST /api/restart: a detached `turno restart`, never run for real here (Popen is faked)."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import helpers


class RestartApiTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.home)
        from turno import control, server
        self.control = control
        helpers.write_config(self.home)
        self.ap.runs_as_module = lambda: True
        self.spawned = []
        self.child_pid = os.getpid()  # a live pid: the test process itself
        patch = mock.patch.object(control.subprocess, "Popen", self.fake_popen)
        patch.start()
        self.addCleanup(patch.stop)
        self.installed = False
        patch = mock.patch("turno.service.status", lambda: {"kind": "systemd", "installed": self.installed, "active": self.installed})
        patch.start()
        self.addCleanup(patch.stop)
        hub = server.Hub(self.home, interval=0.1)
        self.srv = server.Server(hub, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def fake_popen(self, argv, **kw):
        self.spawned.append((argv, kw))
        return mock.Mock(pid=self.child_pid, wait=lambda: None)

    def post(self, path, turno="1"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if turno:
            headers["X-Turno"] = turno
        conn.request("POST", path, headers=headers)
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"null")

    def test_spawns_a_detached_restart(self):
        status, body = self.post("/api/restart")
        self.assertEqual((status, body), (202, {"ok": True, "service": False}))
        (argv, kw), = self.spawned
        self.assertEqual(argv[-3:], ["-m", "turno", "restart"])
        self.assertIs(kw["start_new_session"], True)
        self.assertEqual(kw["env"]["TURNO_HOME"], str(self.home))
        self.assertTrue((self.home / "restart.log").exists())

    def test_says_whether_a_service_will_restart_the_panel(self):
        self.installed = True
        self.assertEqual(self.post("/api/restart")[1], {"ok": True, "service": True})

    def test_a_second_request_while_one_runs_is_busy(self):
        self.assertEqual(self.post("/api/restart")[0], 202)
        status, body = self.post("/api/restart")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "busy")
        self.assertEqual(len(self.spawned), 1)
        lock = self.home / "shared" / "restart.lock"
        self.assertEqual(lock.read_text().strip(), str(self.child_pid))

    def test_a_lock_of_a_dead_pid_does_not_count(self):
        pid = os.fork()  # (control.subprocess.Popen is faked in this test)
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        (self.home / "shared").mkdir(exist_ok=True)
        (self.home / "shared" / "restart.lock").write_text(f"{pid}\n")
        self.assertEqual(self.post("/api/restart")[0], 202)
        self.assertEqual(len(self.spawned), 1)

    def test_needs_the_write_header(self):
        self.assertEqual(self.post("/api/restart", turno=None)[0], 403)
        self.assertEqual(self.spawned, [])


if __name__ == "__main__":
    unittest.main()
