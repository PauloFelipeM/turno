import http.client
import json
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers

DECISION = {"id": "pay", "question": "Charge per clinic?", "context": "c",
            "options": [{"id": "a", "label": "Yes"}, {"id": "b", "label": "No"}], "default": "a",
            "notified_at": 1, "deadline": 2}


class ControlApiTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.home)
        from turno import control
        from turno import overrides
        from turno import server
        self.overrides, self.control = overrides, control
        self.projects = helpers.make_home(self.home)
        (self.home / "shared").mkdir(exist_ok=True)
        self.dec = self.projects["my-api"] / ".superpowers" / "autopilot" / "decisions"
        (self.dec / "pay.json").write_text(json.dumps(DECISION))
        (self.dec / "unsent.json").write_text(json.dumps(dict(DECISION, id="unsent", notified_at=None)))
        self.hub = server.Hub(self.home, {"codex": self.home / "cx", "claude": self.home / "cl"}, interval=0.1)
        self.srv = server.Server(self.hub, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, body=None, headers=None, raw=None, ctype="application/json", turno="1", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if turno is not None:
            h["X-Turno"] = turno
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        if data is not None:
            h["Content-Type"] = ctype
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        out = r.read()
        conn.close()
        return r.status, json.loads(out) if out else None

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body, **kw)

    def ov(self):
        return self.overrides.read(self.home)

    def start_loop(self, kind, handler=False):
        """A fake loop (argv ends in autopilot.py); with `handler`, SIGUSR1 writes a marker file."""
        script = self.home / "fake" / "autopilot.py"
        script.parent.mkdir(exist_ok=True)
        marker = self.home / "usr1.marker"
        ready = self.home / "fake" / "ready"
        ready.unlink(missing_ok=True)
        script.write_text(
            "import signal, sys, time, pathlib\n"
            f"signal.signal(signal.SIGUSR1, lambda *a: pathlib.Path({str(marker)!r}).write_text('x'))\n"
            f"pathlib.Path({str(ready)!r}).write_text('x')\n"
            "time.sleep(30)\n" if handler else
            f"import time, pathlib\npathlib.Path({str(ready)!r}).write_text('x')\ntime.sleep(30)\n")
        proc = subprocess.Popen([sys.executable, str(script), "run"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        (self.home / "shared" / f"instance-{kind}.lock").write_text(str(proc.pid))
        for _ in range(100):  # the script says when it is up (and its handler installed)
            if ready.exists():
                break
            time.sleep(0.05)
        self.assertTrue(ready.exists())
        return proc, marker

    # guard

    def test_guard_refuses_missing_header_foreign_origin_and_wrong_type(self):
        self.assertEqual(self.post("/api/projects/side-project/pause", turno=None)[0], 403)
        self.assertEqual(self.post("/api/projects/side-project/pause", turno="0")[0], 403)
        self.assertEqual(self.post("/api/projects/side-project/pause", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.post("/api/projects/side-project/pause", host="evil.example")[0], 403)
        self.assertEqual(self.post("/api/messages", raw=b'{"text": "oi"}', ctype="text/plain")[0], 403)
        status, body = self.post("/api/projects/side-project/pause", turno=None)
        self.assertEqual(body["error"], "forbidden")
        self.assertEqual(self.ov(), {})

    def test_bad_bodies(self):
        self.assertEqual(self.post("/api/messages", raw=b"{nope")[0], 400)
        self.assertEqual(self.post("/api/messages", raw=b"[1]")[0], 400)
        self.assertEqual(self.post("/api/messages", raw=b" " * 70000)[0], 400)
        self.assertEqual(self.post("/api/messages", {"text": ""})[0], 400)
        self.assertEqual(self.post("/api/messages", {"text": "x" * 2001})[0], 400)
        self.assertEqual(self.post("/api/messages", {"text": 5})[0], 400)
        self.assertEqual(self.post("/api/messages", {"text": "oi", "projects": ["nope"]})[0], 400)
        self.assertEqual(self.post("/api/messages", {"text": "oi", "projects": []})[0], 400)
        self.assertFalse((self.home / "shared" / "notes").exists())

    def test_unknown_routes_and_methods(self):
        self.assertEqual(self.post("/api/whatever")[0], 404)
        self.assertEqual(self.post("/api/projects/side-project/dance")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/projects/side-project/pause")[0], 404)
        self.assertEqual(self.call("PUT", "/api/messages", {})[0], 404)

    # project actions

    def test_pause_resume_next_reset(self):
        self.assertEqual(self.post("/api/projects/side-project/pause")[0], 200)
        self.assertTrue(self.ov()["projects"]["side-project"]["paused"])
        self.assertIn("poke", self.ov())
        self.assertEqual(self.post("/api/projects/side-project/next")[0], 409)
        self.assertEqual(self.post("/api/projects/side-project/next")[1]["error"], "paused")
        self.assertNotIn("next", self.ov())
        self.assertEqual(self.post("/api/projects/side-project/resume")[0], 200)
        self.assertFalse(self.ov()["projects"]["side-project"]["paused"])
        self.assertEqual(self.post("/api/projects/side-project/next")[0], 200)
        self.assertEqual(self.ov()["next"], "side-project")
        before = time.time()
        self.assertEqual(self.post("/api/projects/my-app/reset", {})[0], 200)
        self.assertGreaterEqual(self.ov()["reset"]["my-app"], before)

    def test_unknown_project_is_404(self):
        for action in ("pause", "resume", "next", "reset", "interrupt"):
            self.assertEqual(self.post(f"/api/projects/nope/{action}")[0], 404, action)
        self.assertEqual(self.ov(), {})

    def test_interrupt_pauses_and_signals_the_loop(self):
        self.assertEqual(self.post("/api/projects/my-api/interrupt")[0], 409)  # no loop
        self.assertEqual(self.ov(), {})
        proc, marker = self.start_loop("codex", handler=True)
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "sleeping"}}))
        self.assertEqual(self.post("/api/projects/my-api/interrupt")[0], 409)  # alive but not running it
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "running", "project": "my-app"}}))
        self.assertEqual(self.post("/api/projects/my-api/interrupt")[0], 409)
        self.assertFalse(marker.exists())
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "running", "project": "my-api"}}))
        self.assertEqual(self.post("/api/projects/my-api/interrupt")[0], 202)
        self.assertTrue(self.ov()["projects"]["my-api"]["paused"])
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.1)
        self.assertTrue(marker.exists())

    def test_interrupt_never_signals_a_pid_that_is_not_the_loop(self):
        import os
        (self.home / "shared" / "instance-codex.lock").write_text(str(os.getpid()))  # alive, not autopilot.py
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "running", "project": "my-api"}}))
        with mock.patch("os.kill") as kill:
            self.assertEqual(self.post("/api/projects/my-api/interrupt")[0], 409)
        self.assertFalse([c for c in kill.call_args_list if c.args[1:] == (signal.SIGUSR1,)])

    # engines

    def test_start_and_stop_call_the_launcher_with_the_engine(self):
        done = threading.Event()
        calls = []
        def fake(name):
            def f(names=None):
                calls.append((name, names))
                done.set()
                return {}
            return f
        with mock.patch.object(self.ap, "start_loops", fake("start")), mock.patch.object(self.ap, "stop_loops", fake("stop")):
            self.assertEqual(self.post("/api/engines/claude/start"), (202, {"ok": True}))
            self.assertTrue(done.wait(5))
            self.assertEqual(self.post("/api/engines/codex/stop")[0], 409)  # not running
            self.start_loop("codex")
            done.clear()
            self.assertEqual(self.post("/api/engines/codex/stop"), (202, {"ok": True}))
            self.assertTrue(done.wait(5))
            self.assertEqual(self.post("/api/engines/codex/start")[1]["error"], "already_running")
        self.assertEqual(calls, [("start", ["claude"]), ("stop", ["codex"])])

    def test_unknown_engine_is_404(self):
        self.assertEqual(self.post("/api/engines/gemini/start")[0], 404)
        self.assertEqual(self.post("/api/engines/gemini/stop")[0], 404)

    # decisions and messages

    def test_decision_answer_with_comment(self):
        status, _ = self.post("/api/decisions/my-api/pay", {"option": "b", "comment": "because"})
        self.assertEqual(status, 200)
        d = json.loads((self.dec / "pay.json").read_text())
        self.assertEqual((d["answer"], d["answered_by"]), ("b", "owner"))
        notes = json.loads((self.home / "shared" / "notes" / "my-api.json").read_text())
        self.assertEqual([n["text"] for n in notes], ["Comment on the answer to decision pay (option b): because"])
        self.assertIn("poke", self.ov())
        self.assertEqual(self.post("/api/decisions/my-api/pay", {"option": "a"})[0], 404)  # no longer open

    def test_decision_answer_without_comment_adds_no_note(self):
        self.assertEqual(self.post("/api/decisions/my-api/pay", {"option": "a"})[0], 200)
        self.assertFalse((self.home / "shared" / "notes" / "my-api.json").exists())

    def test_decision_answer_refusals(self):
        self.assertEqual(self.post("/api/decisions/my-api/pay", {"option": "z"}), (400, {"error": "bad_option", "message": mock.ANY}))
        self.assertEqual(self.post("/api/decisions/my-api/pay", {})[0], 400)
        self.assertEqual(self.post("/api/decisions/my-api/pay", {"option": "a", "comment": 3})[0], 400)
        self.assertEqual(self.post("/api/decisions/my-api/unsent", {"option": "a"})[0], 404)
        self.assertEqual(self.post("/api/decisions/my-api/..%2Fx", {"option": "a"})[0], 404)
        self.assertEqual(self.post("/api/decisions/nope/pay", {"option": "a"})[0], 404)
        self.assertNotIn("answer", json.loads((self.dec / "pay.json").read_text()))

    def test_decision_answer_busy_lock_is_409(self):
        self.control.LOCK_WAIT = 0.5
        self.addCleanup(setattr, self.control, "LOCK_WAIT", 5.0)
        from turno import coord
        handle = coord.try_lock("decisions")
        self.assertIsNotNone(handle)
        t = time.monotonic()  # not the wall clock: it can step back under WSL
        status, body = self.post("/api/decisions/my-api/pay", {"option": "a"})
        coord.unlock(handle)
        self.assertEqual((status, body["error"]), (409, "busy"))
        self.assertGreaterEqual(time.monotonic() - t, 0.4)
        self.assertNotIn("answer", json.loads((self.dec / "pay.json").read_text()))
        self.assertEqual(self.post("/api/decisions/my-api/pay", {"option": "a"})[0], 200)  # lock released

    def test_messages(self):
        self.assertEqual(self.post("/api/messages", {"text": "hello", "projects": ["side-project", "my-app"]})[0], 200)
        notes = lambda n: json.loads((self.home / "shared" / "notes" / f"{n}.json").read_text())
        self.assertEqual(notes("side-project")[0]["text"], "hello")
        self.assertEqual(notes("my-app")[0]["text"], "hello")
        self.assertFalse((self.home / "shared" / "notes" / "my-api.json").exists())
        self.assertIn("poke", self.ov())
        self.assertEqual(self.post("/api/messages", {"text": "all"})[0], 200)
        self.assertEqual(notes("my-api")[0]["text"], "all")
        self.assertEqual(len(notes("side-project")), 2)

    # settings

    def cfg(self):
        from turno import config
        return config.load(self.home)

    def test_settings_get_and_put_write_the_config(self):
        status, s = self.call("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(s["order"], ["my-api", "my-app", "side-project"])
        body = {"order": ["side-project", "my-api"], "weekly_cap": {"side-project": {"codex": 40, "claude": 80.5}},
                "primary_cap": {"codex": 90}}
        status, s = self.call("PUT", "/api/settings", body)
        self.assertEqual(status, 200)
        data = self.cfg()
        self.assertEqual([p["name"] for p in data["projects"]], ["side-project", "my-api", "my-app"])
        self.assertEqual(data["projects"][0]["weekly_cap"], {"codex": 40, "claude": 80.5})
        self.assertEqual(data["engines"]["codex"]["primary_cap"], 90)
        self.assertEqual(self.ov(), {"poke": self.ov().get("poke")})  # only the poke touches the overrides
        self.assertEqual(self.call("GET", "/api/settings")[1], s)
        self.assertEqual(s["order"], ["side-project", "my-api", "my-app"])
        self.assertEqual(s["engines"]["codex"]["weekly_cap"]["side-project"], 40)
        # merge: a second PUT keeps the rest
        self.call("PUT", "/api/settings", {"weekly_cap": {"side-project": {"codex": 10}, "my-app": {"claude": 5}}})
        data = self.cfg()
        self.assertEqual(data["projects"][0]["weekly_cap"], {"codex": 10, "claude": 80.5})
        self.assertEqual(data["projects"][2]["weekly_cap"], {"codex": 90, "claude": 5})
        self.assertEqual([p["name"] for p in data["projects"]], ["side-project", "my-api", "my-app"])

    def test_delete_settings_is_gone(self):
        self.assertEqual(self.call("DELETE", "/api/settings")[0], 404)
        self.assertFalse(hasattr(self.control, "delete_settings") or hasattr(self.control, "reset_settings"))

    def test_corrupt_overrides_types_do_not_break_writes(self):
        (self.home / "overrides.json").write_text(json.dumps({"projects": [], "reset": 5, "primary_cap": 1}))
        self.assertEqual(self.post("/api/projects/side-project/pause")[0], 200)
        self.assertEqual(self.post("/api/projects/side-project/resume")[0], 200)
        self.assertEqual(self.post("/api/projects/side-project/next")[0], 200)
        self.assertEqual(self.post("/api/projects/side-project/reset")[0], 200)
        (self.home / "overrides.json").write_text(json.dumps({"projects": {"side-project": []}, "reset": 5}))
        self.assertEqual(self.call("PUT", "/api/settings", {"weekly_cap": {"side-project": {"codex": 5}}, "primary_cap": {"codex": 9}})[0], 200)
        (self.home / "overrides.json").write_text(json.dumps({"projects": {"side-project": {"weekly_cap": 3}}}))
        self.assertEqual(self.call("PUT", "/api/settings", {"weekly_cap": {"side-project": {"codex": 6}}})[0], 200)
        self.assertEqual(self.cfg()["projects"][2]["weekly_cap"]["codex"], 6)

    def test_settings_validation_writes_nothing(self):
        bad = [{"order": ["nope"]}, {"order": "side-project"}, {"order": ["side-project", "side-project"]}, {"order": [1]},
               {"weekly_cap": {"nope": {"codex": 50}}}, {"weekly_cap": {"side-project": {"gemini": 50}}},
               {"weekly_cap": {"side-project": {"codex": 101}}}, {"weekly_cap": {"side-project": {"codex": -1}}},
               {"weekly_cap": {"side-project": {"codex": "50"}}}, {"weekly_cap": {"side-project": {"codex": True}}},
               {"weekly_cap": {"side-project": 50}}, {"weekly_cap": {"side-project": {}}}, {"weekly_cap": []},
               {"primary_cap": {"codex": 101}}, {"primary_cap": {"gemini": 50}}, {"primary_cap": 5},
               {"colour": "red"},
               # a valid part next to an invalid one: nothing is written
               {"order": ["side-project"], "primary_cap": {"codex": 500}}]
        before = self.cfg()
        for body in bad:
            self.assertEqual(self.call("PUT", "/api/settings", body)[0], 400, body)
        self.assertEqual(self.call("PUT", "/api/settings", raw=b"{x")[0], 400)
        self.assertEqual(self.ov(), {})
        self.assertEqual(self.cfg(), before)

    def test_settings_writes_need_the_guard(self):
        self.assertEqual(self.call("PUT", "/api/settings", {"order": ["side-project"]}, turno=None)[0], 403)
        self.assertEqual(self.call("PUT", "/api/settings", raw=b'{"order": ["side-project"]}', ctype="text/plain")[0], 403)
        self.assertEqual(self.ov(), {})


    # interrupt against a loop that cannot take SIGUSR1

    def test_hub_signature_changes_when_the_overrides_change(self):
        before = self.hub.signature()
        self.overrides.update(self.home, lambda d: None)
        self.assertNotEqual(self.hub.signature(), before)

    def test_interrupt_refuses_a_loop_without_a_sigusr1_handler(self):
        proc, marker = self.start_loop("codex")  # an old loop: SIGUSR1 would kill it
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "running", "project": "my-api"}}))
        status, body = self.post("/api/projects/my-api/interrupt")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "restart_needed")
        self.assertTrue(body["message"])
        time.sleep(0.5)
        self.assertIsNone(proc.poll())  # still alive
        self.assertEqual(self.ov(), {})  # not paused, nothing written

    def test_interrupt_refuses_when_the_handlers_cannot_be_read(self):
        proc, marker = self.start_loop("codex", handler=True)
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "running", "project": "my-api"}}))
        with mock.patch.object(self.control, "_caught_signals", return_value=None):
            status, body = self.post("/api/projects/my-api/interrupt")
        self.assertEqual((status, body["error"]), (409, "restart_needed"))
        self.assertFalse(marker.exists())
        self.assertEqual(self.ov(), {})


    # interrupt where /proc is missing (macOS)

    def no_proc(self):
        for module in (self.control, self.control.panel):
            patch = mock.patch.object(module, "PROC", self.home / "no-proc")
            patch.start()
            self.addCleanup(patch.stop)

    def instance_file(self, kind, **info):
        (self.home / "shared" / f"instance-{kind}.json").write_text(json.dumps({"sigusr1": True, "started_at": 1, **info}))

    def running_on_my_api(self):
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "running", "project": "my-api"}}))

    def test_interrupt_without_proc_trusts_the_loops_instance_file(self):
        proc, marker = self.start_loop("codex", handler=True)
        self.running_on_my_api()
        self.instance_file("codex", pid=proc.pid)
        self.no_proc()
        self.assertEqual(self.post("/api/projects/my-api/interrupt")[0], 202)
        self.assertTrue(self.ov()["projects"]["my-api"]["paused"])
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.1)
        self.assertTrue(marker.exists())

    def test_interrupt_without_proc_refuses_a_loop_without_the_file(self):
        proc, marker = self.start_loop("codex", handler=True)  # started by a version that does not write it
        self.running_on_my_api()
        self.no_proc()
        status, body = self.post("/api/projects/my-api/interrupt")
        self.assertEqual((status, body["error"]), (409, "restart_needed"))
        self.assertEqual(self.ov(), {})
        self.assertFalse(marker.exists())

    def test_interrupt_without_proc_refuses_a_file_that_does_not_match(self):
        proc, marker = self.start_loop("codex", handler=True)
        self.running_on_my_api()
        self.no_proc()
        for info in ({"pid": proc.pid + 1}, {"pid": proc.pid, "sigusr1": False}, {"pid": str(proc.pid)}):
            self.instance_file("codex", **info)
            self.assertEqual(self.post("/api/projects/my-api/interrupt")[0], 409, info)
        (self.home / "shared" / "instance-codex.json").write_text("not json")
        self.assertEqual(self.post("/api/projects/my-api/interrupt")[0], 409)
        self.assertEqual(self.ov(), {})

    def test_interrupt_without_proc_refuses_a_reused_pid(self):
        # The loop died by SIGKILL, leaving its lock and instance file; another process took the pid.
        victim = subprocess.Popen(["sleep", "30"])
        self.addCleanup(victim.wait)
        self.addCleanup(victim.kill)
        (self.home / "shared" / "instance-codex.lock").write_text(str(victim.pid))
        self.instance_file("codex", pid=victim.pid)
        self.running_on_my_api()
        self.no_proc()
        with mock.patch("os.kill") as kill:
            status, body = self.post("/api/projects/my-api/interrupt")
        self.assertEqual(status, 409)
        self.assertEqual([c for c in kill.call_args_list if c.args[1] != 0], [])  # no signal at all
        self.assertIsNone(victim.poll())
        self.assertEqual(self.ov(), {})
        self.assertIsNone(self.control.panel.loop_pid(self.home, "codex"))

    def test_with_proc_the_kernel_mask_wins_over_the_file(self):
        proc, marker = self.start_loop("codex")  # no handler: SIGUSR1 would kill it
        self.running_on_my_api()
        self.instance_file("codex", pid=proc.pid)
        status, body = self.post("/api/projects/my-api/interrupt")
        self.assertEqual((status, body["error"]), (409, "restart_needed"))
        time.sleep(0.3)
        self.assertIsNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
