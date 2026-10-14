import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers

OLD_ROLE = "plan" + "-reviewer"  # the removed role, spelled so a grep for it stays clean

CLI = {"found": True, "path": "/bin/fake", "version": "1.0"}


class ConfigApiTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.home)
        import config
        import control
        import engines
        import panel
        import server
        self.config, self.control, self.engines, self.panel = config, control, engines, panel
        self.projects = helpers.make_home(self.home)
        (self.home / "shared" / "claims").mkdir(parents=True, exist_ok=True)
        patch = mock.patch.object(engines, "cli_status", lambda name, max_age=60: dict(CLI))  # never run a real CLI
        patch.start()
        self.addCleanup(patch.stop)
        self.hub = server.Hub(self.home, {"codex": self.home / "cx", "claude": self.home / "cl"}, interval=0.1)
        self.srv = server.Server(self.hub, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.extra = self.home / "projects" / "novo"
        self.extra.mkdir()

    def call(self, method, path, body=None, turno="1", raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if turno is not None:
            h["X-Turno"] = turno
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        if data is not None:
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        out = r.read()
        conn.close()
        return r.status, json.loads(out) if out else None

    def cfg(self):
        return self.config.load(self.home)

    def raw(self):
        return (self.home / "config.json").read_bytes()

    def snapshot(self):
        return {str(p.relative_to(self.home)): (p.read_bytes() if p.is_file() else None)
                for p in sorted(self.home.rglob("*")) if "shared/config.lock" not in str(p)}

    def start_loop(self, kind):
        script = self.home / "fake" / "autopilot.py"
        script.parent.mkdir(exist_ok=True)
        script.write_text("import time\ntime.sleep(30)\n")
        proc = subprocess.Popen([sys.executable, str(script), "run"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        (self.home / "shared" / f"instance-{kind}.lock").write_text(str(proc.pid))
        deadline = time.monotonic() + 5
        while self.panel.loop_pid(self.home, kind) is None and time.monotonic() < deadline:
            time.sleep(0.01)

    # --- GET /api/config ------------------------------------------------------------------

    def test_config_view_lists_projects_engines_and_notify(self):
        status, v = self.call("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual([p["name"] for p in v["projects"]], ["my-app", "my-api", "side-project"])
        p = v["projects"][0]
        self.assertEqual((p["weekly_cap"], p["removable"], p["blocked_by"]), ({"codex": 90, "claude": 90}, True, None))
        self.assertEqual(v["engines"]["codex"]["cli"], CLI)
        self.assertEqual(v["engines"]["claude"]["fable_cap"], 80)
        self.assertEqual(v["notify"]["server"], "https://ntfy.sh")
        self.assertNotIn("segredo-xyz", json.dumps(v))

    def test_config_view_checks(self):
        helpers.git_repo(self.projects["my-api"])
        (self.projects["my-api"] / "docs").mkdir()
        (self.projects["my-api"] / "docs" / "autopilot.md").write_text("x")
        (self.projects["my-api"] / ".gitignore").write_text(".superpowers/\n")
        helpers.git_repo(self.projects["side-project"])
        shutil.rmtree(self.projects["my-app"])
        v = self.panel.config_view(self.home)
        checks = {p["name"]: p["checks"] for p in v["projects"]}
        self.assertEqual(checks["my-app"], ["missing_path"])
        self.assertEqual(checks["my-api"], [])
        self.assertEqual(checks["side-project"], ["no_autopilot_doc", "superpowers_not_ignored"])
        plain = self.home / "projects" / "novo"
        self.assertEqual(self.panel.project_checks(plain), ["not_git", "no_autopilot_doc"])

    def test_config_view_removable_and_blocked_by(self):
        (self.home / "state.json").write_text(json.dumps({"my-api": {"interrupted": True}}))
        (self.home / "shared" / "claims" / "side-project.json").write_text(json.dumps({"engine": "codex"}))
        v = {p["name"]: p for p in self.panel.config_view(self.home)["projects"]}
        self.assertEqual([(v[n]["removable"], v[n]["blocked_by"]) for n in ("my-app", "my-api", "side-project")],
                         [(True, None), (False, "interrupted"), (False, "claimed")])
        (self.home / "state.json").write_text(json.dumps({"_loop": {"phase": "running", "project": "my-app"}}))
        self.start_loop("codex")
        v = {p["name"]: p for p in self.panel.config_view(self.home)["projects"]}
        self.assertEqual(v["my-app"]["blocked_by"], "running")

    def test_config_view_invalid_config_is_a_500_with_a_code(self):
        helpers.write_config_text(self.home, "{x")
        status, body = self.call("GET", "/api/config")
        self.assertEqual((status, body["error"]), (500, "config"))

    def test_overview_engines_have_enabled(self):
        helpers.write_config(self.home, [], enabled=("claude",))
        o = self.panel.overview(self.home, {}, {"codex": self.home / "cx", "claude": self.home / "cl"})
        self.assertEqual([(e["engine"], e["enabled"]) for e in o["engines"]], [("codex", False), ("claude", True)])

    # --- projects -----------------------------------------------------------------------------

    def test_add_project(self):
        body = {"name": "novo", "path": str(self.extra), "docker_group": "web", "weekly_cap": {"codex": 50}}
        self.assertEqual(self.call("POST", "/api/projects", body)[0], 200)
        p = self.cfg()["projects"][-1]
        self.assertEqual(p, {"name": "novo", "path": str(self.extra), "docker_group": "web", "weekly_cap": {"codex": 50}})
        self.assertEqual(self.call("POST", "/api/projects", {"name": "n2", "path": str(self.extra)})[0], 200)
        self.assertEqual(self.cfg()["projects"][-1], {"name": "n2", "path": str(self.extra)})

    def test_add_project_errors_write_nothing(self):
        before = self.raw()
        status, body = self.call("POST", "/api/projects", {"name": "my-app", "path": str(self.extra)})
        self.assertEqual((status, body["error"]), (409, "exists"))
        for missing in (str(self.home / "nope"), str(self.home / "secrets.toml")):
            status, body = self.call("POST", "/api/projects", {"name": "novo", "path": missing})
            self.assertEqual((status, body["error"]), (400, "bad_path"), missing)
        bad = [{"name": "novo", "path": "relative"}, {"name": "novo"}, {"path": str(self.extra)},
               {"name": "bad name", "path": str(self.extra)}, {"name": "novo", "path": str(self.extra), "x": 1},
               {"name": "novo", "path": str(self.extra), "weekly_cap": {"codex": 101}},
               {"name": "novo", "path": str(self.extra), "weekly_cap": {"gemini": 5}},
               {"name": "novo", "path": str(self.extra), "docker_group": 5}]
        for b in bad:
            status, body = self.call("POST", "/api/projects", b)
            self.assertEqual((status, body["error"]), (400, "bad_request"), b)
        self.assertEqual(self.raw(), before)

    def test_edit_project(self):
        status, _ = self.call("PUT", "/api/projects/side-project", {"path": str(self.extra), "docker_group": "web",
                                                              "weekly_cap": {"codex": 60}})
        self.assertEqual(status, 200)
        p = self.cfg()["projects"][2]
        self.assertEqual((p["path"], p["docker_group"], p["weekly_cap"]), (str(self.extra), "web", {"codex": 60, "claude": 90}))
        for empty in (None, ""):
            self.call("PUT", "/api/projects/side-project", {"docker_group": empty})
            self.assertNotIn("docker_group", self.cfg()["projects"][2])

    def test_edit_cannot_rename_and_errors(self):
        before = self.raw()
        status, body = self.call("PUT", "/api/projects/side-project", {"path": str(self.home / "nope")})
        self.assertEqual((status, body["error"]), (400, "bad_path"))
        for b in ({"name": "other"}, {"weekly_cap": {"codex": -1}}, {"weekly_cap": 5}):
            self.assertEqual(self.call("PUT", "/api/projects/side-project", b)[0], 400, b)
        status, body = self.call("PUT", "/api/projects/nope", {"path": str(self.extra)})
        self.assertEqual((status, body["error"]), (404, "not_found"))
        self.assertEqual(self.raw(), before)

    def test_remove_project_touches_nothing_else(self):
        (self.home / "state.json").write_text(json.dumps({"side-project": {"interrupted": False, "cost": {}}}))
        (self.home / "history-codex.jsonl").write_text('{"unit_id": "u"}\n')
        (self.projects["side-project"] / "keep.txt").write_text("k")
        before = {k: v for k, v in self.snapshot().items() if k != "config.json"}
        self.assertEqual(self.call("DELETE", "/api/projects/side-project")[0], 200)
        self.assertEqual([p["name"] for p in self.cfg()["projects"]], ["my-app", "my-api"])
        after = {k: v for k, v in self.snapshot().items() if k != "config.json"}
        self.assertEqual({k: v for k, v in after.items() if not k.endswith("overrides.json") and not k.endswith("overrides.lock")}, before)
        self.assertTrue((self.projects["side-project"] / "keep.txt").exists())
        self.assertEqual(self.call("DELETE", "/api/projects/side-project")[1]["error"], "not_found")

    def test_remove_project_in_use_is_409(self):
        (self.home / "state.json").write_text(json.dumps({
            "my-api": {"interrupted": True}, "my-app": {}, "_loop": {"phase": "running", "project": "my-app"}}))
        (self.home / "shared" / "claims" / "side-project.json").write_text(json.dumps({"engine": "claude"}))
        self.start_loop("codex")
        before = self.raw()
        for name, why in (("my-app", "running"), ("my-api", "interrupted"), ("side-project", "claimed")):
            status, body = self.call("DELETE", f"/api/projects/{name}")
            self.assertEqual((status, body["error"]), (409, "in_use"), name)
            self.assertIn(why, body["message"])
        self.assertEqual(self.raw(), before)

    # --- engines ------------------------------------------------------------------------------

    def test_put_engine(self):
        sched = {"start": "fri 19:00", "end": "sun 03:00"}
        body = {"enabled": False, "orchestrator_model": "m2", "fable_cap": 70, "safety_factor": 2, "schedule": sched}
        self.assertEqual(self.call("PUT", "/api/engines/claude", body)[0], 200)
        e = self.cfg()["engines"]["claude"]
        self.assertEqual((e["enabled"], e["orchestrator_model"], e["fable_cap"], e["safety_factor"]), (False, "m2", 70, 2))
        self.assertEqual(e["schedule"], {**sched, "start_margin_minutes": 15})
        self.assertEqual(self.call("PUT", "/api/engines/claude", {"schedule": None})[0], 200)
        self.assertIsNone(self.cfg()["engines"]["claude"]["schedule"])
        self.assertEqual(self.call("PUT", "/api/engines/codex", {"max_subagents": 3})[0], 200)
        self.assertEqual(self.cfg()["engines"]["codex"]["max_subagents"], 3)

    def test_put_engine_roles_merge_into_the_current_ones(self):
        d = self.config.ENGINE_DEFAULTS
        self.assertEqual(self.call("PUT", "/api/engines/claude", {"roles": {"planner": {"model": "claude-fable-5-1"}}})[0], 200)
        roles = self.cfg()["engines"]["claude"]["roles"]
        self.assertEqual(roles["planner"], {"model": "claude-fable-5-1", "effort": "high"})
        self.assertEqual(roles["reviewer"], d["claude"]["roles"]["reviewer"])
        self.assertEqual(self.call("PUT", "/api/engines/codex", {"roles": {"reviewer": {"effort": "low"}}})[0], 200)
        roles = self.cfg()["engines"]["codex"]["roles"]
        self.assertEqual(roles["reviewer"], {"model": "gpt-6-astra", "effort": "low"})
        self.assertEqual(roles["planner"], d["codex"]["roles"]["planner"])

    def test_put_engine_roles_errors_leave_the_file_alone(self):
        before = self.raw()
        bad = [{"roles": []}, {"roles": "x"}, {"roles": {"orchestrator": {"model": "m"}}},
               {"roles": {OLD_ROLE: {"model": "m"}}}, {"roles": {"planner": "x"}},
               {"roles": {"planner": {"model": ""}}}, {"roles": {"planner": {"model": "a b"}}},
               {"roles": {"planner": {"effort": ""}}}, {"roles": {"planner": {"colour": "red"}}}]
        for b in bad:
            status, body = self.call("PUT", "/api/engines/codex", b)
            self.assertEqual((status, body["error"]), (400, "bad_request"), b)
        self.assertEqual(self.raw(), before)

    def test_get_config_has_roles_and_known_models(self):
        status, v = self.call("GET", "/api/config")
        self.assertEqual(status, 200)
        for engine in ("codex", "claude"):
            self.assertEqual(v["engines"][engine]["roles"], self.config.ENGINE_DEFAULTS[engine]["roles"])
            self.assertEqual(v["engines"][engine]["known_models"], self.config.KNOWN_MODELS[engine])

    def test_put_engine_errors_leave_the_file_alone(self):
        before = self.raw()
        bad = [("claude", {"schedule": {"start": "someday", "end": "sun 03:00"}}),
               ("claude", {"schedule": {"start": "fri 19:00", "end": "fri 19:00"}}),
               ("claude", {"schedule": "x"}), ("claude", {"schedule": {"start": "fri 19:00", "end": "sun 03:00", "x": 1}}),
               ("claude", {"max_subagents": 2}), ("codex", {"fable_cap": 5}), ("codex", {"colour": "red"}),
               ("codex", {"enabled": "yes"}), ("codex", {"primary_cap": 101}), ("codex", {"safety_factor": 0}),
               ("codex", {"orchestrator_model": ""})]
        for engine, b in bad:
            status, body = self.call("PUT", f"/api/engines/{engine}", b)
            self.assertEqual((status, body["error"]), (400, "bad_request"), (engine, b))
        self.assertEqual(self.call("PUT", "/api/engines/gemini", {})[0], 404)
        self.assertEqual(self.raw(), before)

    def test_start_and_stop_with_an_invalid_config_are_400_config(self):
        helpers.write_config_text(self.home, "{broken")
        for action in ("start", "stop"):
            status, body = self.call("POST", f"/api/engines/codex/{action}")
            self.assertEqual((status, body["error"]), (400, "config"), action)
        (self.home / "config.json").write_bytes(b"\xff\xfe")
        self.assertEqual(self.call("POST", "/api/engines/codex/start")[0], 400)

    def test_start_on_a_disabled_engine_is_409(self):
        helpers.write_config(self.home, [], enabled=("claude",))
        with mock.patch.object(self.ap, "start_loops") as start:
            status, body = self.call("POST", "/api/engines/codex/start")
            self.assertEqual((status, body["error"]), (409, "disabled"))
            self.assertEqual(self.call("POST", "/api/engines/claude/start")[0], 202)
            deadline = time.monotonic() + 5
            while not start.called and time.monotonic() < deadline:
                time.sleep(0.01)
        start.assert_called_once_with(["claude"])

    def test_check_engine_returns_limits_and_writes_no_file(self):
        limits = {"primary": {"used_percent": 12, "resets_at": 1}}
        fake = []

        def build(cfg):
            eng = helpers.FakeEngine(["true"], limits=limits)
            fake.append((eng, cfg))
            return eng

        before = self.snapshot()
        with mock.patch.dict(self.engines.ENGINES, {"claude": build}):
            status, body = self.call("POST", "/api/engines/claude/check")
        self.assertEqual((status, body), (200, {"limits": limits}))
        self.assertEqual(fake[0][0].max_ages, [0])
        self.assertEqual(fake[0][1]["defaults"]["fable_cap"], 80)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.home / "state-claude.json").exists())

    def test_check_engine_errors_are_a_200_with_the_text(self):
        class Boom(helpers.FakeEngine):
            def limits(self, state, max_age=1800):
                raise RuntimeError("no binary")

        with mock.patch.dict(self.engines.ENGINES, {"codex": lambda cfg: Boom(["true"]),
                                                    "claude": lambda cfg: helpers.FakeEngine(["true"], limits={})}):
            status, body = self.call("POST", "/api/engines/codex/check")
            self.assertEqual((status, body["limits"]), (200, None))
            self.assertIn("no binary", body["error"])
            status, body = self.call("POST", "/api/engines/claude/check")
            self.assertEqual((status, body["limits"]), (200, None))
            self.assertTrue(body["error"])
        self.assertEqual(self.call("POST", "/api/engines/gemini/check")[0], 404)

    def test_check_engine_without_the_cli_does_not_build_the_engine(self):
        built = []
        before = self.snapshot()
        with mock.patch.object(self.engines, "cli_status", lambda name, max_age=60: {"found": False, "path": None, "version": None}), \
                mock.patch.dict(self.engines.ENGINES, {"claude": lambda cfg: built.append(cfg)}):
            status, body = self.call("POST", "/api/engines/claude/check")
        self.assertEqual((status, body), (200, {"limits": None, "error": "CLI not found"}))
        self.assertEqual(built, [])
        self.assertEqual(self.snapshot(), before)

    def test_check_engine_survives_a_system_exit_and_restores_the_log(self):
        class Exits(helpers.FakeEngine):
            def limits(self, state, max_age=1800):
                raise SystemExit("x")

        original = self.engines.log
        with mock.patch.dict(self.engines.ENGINES, {"claude": lambda cfg: Exits(["true"])}):
            status, body = self.call("POST", "/api/engines/claude/check")
        self.assertEqual((status, body), (200, {"limits": None, "error": "x"}))
        self.assertIs(self.engines.log, original)

    # --- guard and pokes ------------------------------------------------------------------------

    def test_new_routes_need_the_guard(self):
        before = self.raw()
        for method, path, body in (("POST", "/api/projects", {"name": "novo", "path": str(self.extra)}),
                                   ("PUT", "/api/projects/side-project", {"path": str(self.extra)}),
                                   ("DELETE", "/api/projects/side-project", None),
                                   ("PUT", "/api/engines/codex", {"enabled": False}),
                                   ("POST", "/api/engines/codex/check", None)):
            self.assertEqual(self.call(method, path, body, turno=None)[0], 403, path)
        self.assertEqual(self.raw(), before)

    def test_writes_poke_the_loops(self):
        import overrides
        self.call("POST", "/api/projects", {"name": "novo", "path": str(self.extra)})
        first = overrides.read(self.home).get("poke")
        self.assertTrue(first)
        time.sleep(0.01)
        self.call("PUT", "/api/engines/codex", {"enabled": False})
        self.assertNotEqual(overrides.read(self.home).get("poke"), first)


if __name__ == "__main__":
    unittest.main()
