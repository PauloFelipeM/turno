import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        helpers.load(self.home)
        from turno import server
        self.server_mod = server
        helpers.make_home(self.home)
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "sleeping"}}))
        self.hub = server.Hub(self.home, {"codex": self.home / "cx", "claude": self.home / "cl"}, interval=0.1)
        self.srv = server.Server(self.hub, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        threading.Thread(target=self.hub.run, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.hub.stop)

    def get(self, path, host=None, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if origin:
            headers["Origin"] = origin
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, r.getheader("Content-Type"), body

    def test_overview(self):
        status, ctype, body = self.get("/api/overview")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("application/json"))
        self.assertEqual([p["name"] for p in json.loads(body)["projects"]], ["my-api", "my-app", "side-project"])
        self.assertNotIn(b"segredo-xyz", body)

    def test_localhost_host_is_accepted(self):
        self.assertEqual(self.get("/api/overview", host=f"localhost:{self.port}")[0], 200)

    def test_foreign_host_and_origin_are_refused(self):
        self.assertEqual(self.get("/api/overview", host="evil.example")[0], 403)
        self.assertEqual(self.get("/api/overview", host=f"evil.example:{self.port}")[0], 403)
        self.assertEqual(self.get("/api/overview", origin="http://evil.example")[0], 403)
        self.assertEqual(self.get("/api/overview", origin=f"http://localhost:{self.port}")[0], 200)

    def test_pages_and_modules_are_served(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"))
        for path in sorted(p.relative_to(self.server_mod.WEB).as_posix() for p in self.server_mod.WEB.rglob("*") if p.is_file()):
            self.assertEqual(self.get(f"/static/{path}")[0], 200, path)

    def test_static_cannot_escape_the_web_folder(self):
        self.assertEqual(self.get("/static/../autopilot.py")[0], 404)
        self.assertEqual(self.get("/static/%2e%2e/autopilot.py")[0], 404)

    def test_unknown_routes(self):
        self.assertEqual(self.get("/api/projects/nope")[0], 404)
        self.assertEqual(self.get("/api/units/nope")[0], 404)
        self.assertEqual(self.get("/api/whatever")[0], 404)
        self.assertEqual(self.get("/api/units?limit=abc")[0], 400)

    def test_units_and_live(self):
        self.assertEqual(json.loads(self.get("/api/units")[2]), [])
        self.assertEqual(json.loads(self.get("/api/live")[2]), [])

    def test_error_returns_json_500(self):
        from turno import panel
        original = panel.overview
        panel.overview = lambda *a, **k: 1 / 0
        try:
            with mock.patch.object(self.server_mod.traceback, "print_exc"):
                status, ctype, body = self.get("/api/overview")
        finally:
            panel.overview = original
        self.assertEqual((status, json.loads(body)), (500, {"error": "internal"}))

    def test_events_send_overview_now_and_on_change(self):
        time.sleep(0.3)  # the watcher's first announcement goes out before the connection
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock.sendall(f"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
        stream = sock.makefile("rb")
        self.addCleanup(sock.close)

        def next_event():
            event = None
            while True:
                line = stream.readline().decode().rstrip("\r\n")
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event:
                    return event, json.loads(line[6:])

        self.assertEqual(next_event()[0], "overview")
        time.sleep(0.3)  # the watcher records the current signature
        state = self.home / "state.json"
        state.write_text(json.dumps({"_loop": {"phase": "sleeping", "until": 5}}))
        os.utime(state, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
        event, data = next_event()
        self.assertEqual(event, "overview")
        self.assertIn("projects", data)


class PortTest(unittest.TestCase):
    def test_busy_port_exits_with_a_message(self):
        home = Path(tempfile.mkdtemp())
        helpers.load(home)
        from turno import server
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        self.addCleanup(busy.close)
        with self.assertRaises(SystemExit) as ctx:
            server.serve(home, busy.getsockname()[1])
        self.assertIn("in use", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
