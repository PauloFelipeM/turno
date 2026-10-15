import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers

FIXTURES = Path(__file__).parent / "fixtures"
BACKLOG = {"items": [{"id": "admin", "title": "Administração", "state": "doing", "tasks": {"done": 3, "total": 14}},
                     {"id": "billing", "title": "Cobrança", "state": "todo"},
                     {"id": "chat", "title": "Chat", "state": "done"}]}
DECISION = {"id": "pay", "question": "Cobrar por clínica?", "context": "c",
            "options": [{"id": "a", "label": "Sim"}, {"id": "b", "label": "Não"}], "default": "a",
            "notified_at": 1, "deadline": 2}


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        helpers.load(self.home)
        from turno import panel
        self.panel = panel
        self.projects = helpers.make_home(self.home)
        self.homes = {"codex": self.home / "codex-cli", "claude": self.home / "claude-cli"}
        self.cache = {}
        my_api = self.projects["my-api"] / ".superpowers" / "autopilot"
        (my_api / "backlog.json").write_text(json.dumps(BACKLOG))
        (my_api / "decisions" / "pay.json").write_text(json.dumps(DECISION))
        (my_api / "decisions" / "old.json").write_text(json.dumps(dict(DECISION, id="old", answer="a")))
        (self.home / "state.json").write_text(json.dumps({
            "my-api": {"status": "active", "interrupted": True, "unit_id": "u1", "run": {"id": "r2", "started": time.time()},
                        "cost": {"primary": 8, "secondary": 1.5}},
            "my-app": {"status": "blocked", "blocked_until": 99, "cost": {"primary": 8, "secondary": 1.5}},
            "_loop": {"phase": "running", "project": "my-api", "until": None}}))
        (self.home / "state-claude.json").write_text(json.dumps({
            "_engine": {"limits": {"primary": {"used_percent": 10, "resets_at": time.time() + 3600},
                                   "secondary": {"used_percent": 40, "resets_at": time.time() + 86400}},
                        "limits_at": 0}}))
        (self.home / "shared" / "claims").mkdir(parents=True)
        (self.home / "shared" / "claims" / "my-api.json").write_text(json.dumps({"engine": "codex", "since": 1}))
        (self.home / "runs").mkdir()
        shutil.copy(FIXTURES / "codex-run.jsonl", self.home / "runs" / "r1.jsonl")
        shutil.copy(FIXTURES / "codex-run.jsonl", self.home / "runs" / "r2.jsonl")
        lines = [
            {"unit_id": "u0", "run": "r0", "project": "my-app", "engine": "claude", "outcome": "worked", "closed": True,
             "started_at": "2026-10-10T10:00:00-03:00", "ended_at": "2026-10-10T10:05:00-03:00", "unit": "antiga"},
            {"unit_id": "u1", "run": "r1", "project": "my-api", "engine": "codex", "outcome": "unfinished", "closed": False,
             "started_at": "2026-10-11T00:27:02-03:00", "ended_at": "2026-10-11T00:35:07-03:00"},
        ]
        (self.home / "history-claude.jsonl").write_text(json.dumps(lines[0]) + "\n")
        (self.home / "history-codex.jsonl").write_text(json.dumps(lines[1]) + "\n")

    def overview(self):
        return self.panel.overview(self.home, self.cache, self.homes)

    def start_loop(self, kind):
        """A process whose name looks like the loop's, with its pid in the instance lock."""
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
        return proc

    def test_unreadable_config_keeps_the_overview_alive(self):
        (self.home / "config.json").write_bytes(b"\xff\xfe\x80")
        o = self.overview()
        self.assertEqual(o["engines"], [])
        self.assertEqual([e["error"] for e in o["errors"]], ["config"])

    def test_invalid_toml_import_keeps_the_overview_alive(self):
        (self.home / "config.json").unlink()
        (self.home / "projects.toml").write_text("[[project\n")
        o = self.overview()
        self.assertEqual(o["engines"], [])
        self.assertEqual([e["error"] for e in o["errors"]], ["config"])

    def test_instances_come_from_the_config_file(self):
        found = self.panel.instances(self.home)
        self.assertEqual([(i["config"], i["engine"], i["enabled"]) for i in found],
                         [("config.json", "codex", True), ("config.json", "claude", True)])
        self.assertEqual(found[1]["state"], self.home / "state-claude.json")
        self.assertEqual(found[0]["history"], self.home / "history-codex.jsonl")

    def test_a_disabled_engine_is_still_listed_with_enabled_false(self):
        helpers.write_config(self.home, [], enabled=("codex",))
        self.assertEqual([(e["engine"], e["enabled"]) for e in self.overview()["engines"]],
                         [("codex", True), ("claude", False)])

    def test_overview_engines_and_loop_phase(self):
        self.start_loop("codex")
        o = self.overview()
        codex, claude = o["engines"]
        self.assertEqual((codex["engine"], codex["loop"]["phase"], codex["loop"]["project"]), ("codex", "running", "my-api"))
        self.assertEqual(claude["loop"]["phase"], "stopped")
        self.assertTrue(claude["limits"]["known"])
        self.assertEqual(claude["limits"]["secondary"]["used"], 40)
        self.assertEqual(claude["weekly_cap"], 90)

    def test_stale_or_reused_pid_is_stopped(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        (self.home / "shared" / "instance-codex.lock").write_text(str(dead.pid))
        self.assertIsNone(self.panel.loop_pid(self.home, "codex"))
        import os
        (self.home / "shared" / "instance-codex.lock").write_text(str(os.getpid()))  # alive, but not the loop
        self.assertIsNone(self.panel.loop_pid(self.home, "codex"))

    def test_overview_projects(self):
        o = self.overview()
        self.assertEqual([p["name"] for p in o["projects"]], ["my-api", "my-app", "side-project"])
        my_api, my_app, side_project = o["projects"]
        self.assertEqual(my_api["claim"], {"engine": "codex", "handoff_from": None})
        self.assertEqual(my_api["engines"]["codex"]["interrupted"], True)
        self.assertEqual(my_api["backlog"]["current"]["id"], "admin")
        self.assertEqual((my_api["backlog"]["done"], my_api["backlog"]["total"]), (1, 3))
        self.assertEqual(my_api["decisions"], 1)
        self.assertEqual(my_app["engines"]["codex"]["status"], "blocked")
        self.assertEqual(my_app["backlog"], {"error": "missing"})

    def test_overview_control_fields(self):
        from turno import overrides
        my_api, my_app, side_project = self.overview()["projects"]
        self.assertEqual((my_api["paused"], my_api["next"], side_project["paused"]), (False, False, False))
        self.assertEqual([d["id"] for d in my_api["decisions_open"]], ["pay"])
        self.assertEqual(my_api["decisions"], 1)
        self.assertEqual(side_project["decisions_open"], [])
        overrides.update(self.home, lambda d: d.update(next="side-project", projects={"my-app": {"paused": True}}))
        my_api, my_app, side_project = self.overview()["projects"]
        self.assertEqual((my_app["paused"], my_app["next"], side_project["next"]), (True, False, True))

    def test_decisions_open_only_has_sent_unanswered_decisions(self):
        folder = self.projects["my-api"] / ".superpowers" / "autopilot" / "decisions"
        (folder / "unsent.json").write_text(json.dumps(dict(DECISION, id="unsent", notified_at=None)))
        my_api = self.overview()["projects"][0]
        self.assertEqual([d["id"] for d in my_api["decisions_open"]], ["pay"])
        self.assertEqual(my_api["decisions"], 2)

    def test_blocked_summary_is_the_latest_closed_blocked_unit(self):
        self.assertIsNone(self.overview()["projects"][1]["blocked_summary"])
        rows = [
            {"unit_id": "b1", "project": "my-app", "engine": "codex", "outcome": "blocked", "closed": True,
             "started_at": "2026-10-11T10:00:00-03:00", "ended_at": "2026-10-11T10:05:00-03:00", "summary": "velho"},
            {"unit_id": "b2", "project": "my-app", "engine": "codex", "outcome": "blocked", "closed": True,
             "started_at": "2026-10-12T10:00:00-03:00", "ended_at": "2026-10-12T10:05:00-03:00", "summary": "novo"},
            {"unit_id": "b3", "project": "my-app", "engine": "codex", "outcome": "blocked", "closed": False,
             "started_at": "2026-10-13T10:00:00-03:00", "ended_at": "2026-10-13T10:05:00-03:00", "summary": "aberto"},
        ]
        with (self.home / "history-codex.jsonl").open("a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        o = self.overview()["projects"]
        self.assertEqual(o[1]["blocked_summary"], "novo")
        self.assertIsNone(o[0]["blocked_summary"])
        # a blocked unit in the history does not show while no engine has the project blocked
        row = dict(rows[1], unit_id="b4", project="side-project", summary="de outro")
        with (self.home / "history-codex.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        self.assertIsNone(self.overview()["projects"][2]["blocked_summary"])

    def test_settings_come_from_the_config(self):
        s = self.panel.settings(self.home)
        self.assertEqual(s["order"], ["my-api", "my-app", "side-project"])
        self.assertEqual(s["engines"]["codex"], {"primary_cap": 100, "weekly_cap": {"my-api": 90, "my-app": 90, "side-project": 90}})
        self.assertNotIn("overridden", s)

        def edit(d):
            d["projects"].reverse()
            d["projects"][0]["weekly_cap"] = {"codex": 55}
            d["engines"]["claude"]["primary_cap"] = 70
        from turno import config
        config.save(self.home, edit)
        s = self.panel.settings(self.home)
        self.assertEqual(s["order"], ["side-project", "my-app", "my-api"])
        self.assertEqual(s["engines"]["codex"]["weekly_cap"]["side-project"], 55)
        self.assertEqual(s["engines"]["claude"]["weekly_cap"]["side-project"], 100)  # no value for the engine: 100
        self.assertEqual((s["engines"]["claude"]["primary_cap"], s["engines"]["codex"]["primary_cap"]), (70, 100))

    def test_live_only_while_the_loop_runs(self):
        self.assertEqual(self.overview()["live"], [])
        self.start_loop("codex")
        [live] = self.overview()["live"]
        self.assertEqual((live["engine"], live["project"], live["unit_id"], live["run"]), ("codex", "my-api", "u1", "r2"))
        self.assertNotIn("path", live)
        [run] = self.panel.live(self.home, self.homes)
        self.assertEqual(run["items"][0]["kind"], "message")

    def test_broken_project_affects_only_its_card(self):
        shutil.rmtree(self.projects["side-project"])
        (self.projects["my-app"] / ".superpowers" / "autopilot" / "backlog.json").write_text('{"items": [{"id": 1}]}')
        o = self.overview()
        self.assertEqual(o["projects"][2]["backlog"], {"error": "missing_path"})
        self.assertEqual(o["projects"][1]["backlog"], {"error": "invalid"})
        self.assertEqual(o["projects"][0]["backlog"]["current"]["id"], "admin")

    def test_unreadable_config_is_reported(self):
        helpers.write_config_text(self.home, "[[[")
        o = self.overview()
        self.assertEqual(o["errors"], [{"config": "config.json", "error": "config"}])
        self.assertEqual(o["engines"], [])

    def test_no_secret_anywhere(self):
        self.start_loop("codex")
        dumps = json.dumps([self.overview(), self.panel.project_detail(self.home, "my-api", self.cache),
                            self.panel.units(self.home), self.panel.unit_detail(self.home, "u1"),
                            self.panel.live(self.home, self.homes)])
        self.assertNotIn("segredo-xyz", dumps)

    def test_panel_writes_nothing(self):
        before = sorted((str(p), p.stat().st_mtime_ns) for p in self.home.rglob("*"))
        self.overview()
        self.panel.units(self.home)
        self.panel.unit_detail(self.home, "u1")
        self.panel.project_detail(self.home, "my-api", self.cache)
        after = sorted((str(p), p.stat().st_mtime_ns) for p in self.home.rglob("*"))
        self.assertEqual(before, after)

    def test_project_detail(self):
        d = self.panel.project_detail(self.home, "my-api", self.cache)
        self.assertEqual([i["id"] for i in d["backlog"]["items"]], ["admin", "billing", "chat"])
        self.assertEqual([x["id"] for x in d["decisions"]], ["pay"])
        self.assertIsNone(self.panel.project_detail(self.home, "nope", self.cache))

    def test_units_filter_and_page(self):
        self.assertEqual([u["unit_id"] for u in self.panel.units(self.home)], ["u1", "u0"])
        self.assertEqual([u["unit_id"] for u in self.panel.units(self.home, project="my-app")], ["u0"])
        self.assertEqual([u["unit_id"] for u in self.panel.units(self.home, before="2026-10-11T00:00:00-03:00")], ["u0"])
        self.assertEqual(len(self.panel.units(self.home, limit=1)), 1)

    def test_unit_detail_with_and_without_the_run_file(self):
        u = self.panel.unit_detail(self.home, "u1")
        self.assertEqual(u["runs"][0]["feed"][0]["kind"], "message")
        self.assertIsNone(self.panel.unit_detail(self.home, "u0")["runs"][0]["feed"])  # runs-claude/r0 does not exist
        self.assertIsNone(self.panel.unit_detail(self.home, "nope"))

    def test_watched_files(self):
        files = {str(p) for p in self.panel.watched_files(self.home)}
        self.assertIn(str(self.home / "state.json"), files)
        self.assertIn(str(self.home / "history-codex.jsonl"), files)
        self.assertIn(str(self.projects["my-api"] / ".superpowers" / "autopilot" / "backlog.json"), files)

    def test_project_without_path_is_a_config_error(self):
        helpers.write_config(self.home, [{"name": "x"}])
        o = self.overview()
        self.assertEqual(o["errors"], [{"config": "config.json", "error": "config"}])
        self.assertEqual(o["engines"], [])

    def test_unexpected_json_types_are_tolerated(self):
        (self.home / "state.json").write_text("[]")
        (self.home / "shared" / "claims" / "my-api.json").write_text("[]")
        (self.projects["my-api"] / ".superpowers" / "autopilot" / "decisions" / "bad.json").write_text('"x"')
        o = self.overview()
        self.assertEqual(len(o["engines"]), 2)
        self.assertIsNone(o["projects"][0]["claim"])
        self.assertEqual(o["projects"][0]["decisions"], 1)

    def test_panel_never_queries_the_claude_quota(self):
        from turno import engines
        def boom(*a, **k):
            raise AssertionError("probe")
        orig = engines.ClaudeEngine.probe
        engines.ClaudeEngine.probe = boom
        self.addCleanup(setattr, engines.ClaudeEngine, "probe", orig)
        self.overview()
        self.panel.live(self.home, self.homes)
        self.panel.unit_detail(self.home, "u1")


    def test_overrides_file_is_watched(self):
        self.assertIn(self.home / "overrides.json", self.panel.watched_files(self.home))

    def test_history_is_only_read_when_a_project_is_blocked(self):
        calls = []
        real = self.panel.units
        self.panel.units = lambda *a, **k: calls.append(1) or real(*a, **k)
        self.overview()
        self.assertEqual(len(calls), 1)  # my-app is blocked in the fixture
        state = json.loads((self.home / "state.json").read_text())
        state["my-app"]["status"] = "active"
        (self.home / "state.json").write_text(json.dumps(state))
        calls.clear()
        self.assertTrue(all(p["blocked_summary"] is None for p in self.overview()["projects"]))
        self.assertEqual(calls, [])

    def test_unused_aliases_are_gone(self):
        from turno import overrides
        self.assertFalse(hasattr(self.panel, "_ok") or hasattr(self.panel, "_project_path"))
        self.assertFalse(hasattr(overrides, "_cap"))


    def test_overview_shows_retry_at_only_while_it_is_in_the_future(self):
        state = json.loads((self.home / "state.json").read_text())
        soon = time.time() + 600
        state["my-api"]["retry_at"] = soon
        state["my-app"]["retry_at"] = time.time() - 5
        state["side-project"] = {"status": "active", "retry_at": "soon"}
        (self.home / "state.json").write_text(json.dumps(state))
        my_api, my_app, side_project = self.overview()["projects"]
        self.assertEqual(my_api["engines"]["codex"]["retry_at"], soon)
        self.assertIsNone(my_api["engines"]["claude"]["retry_at"])
        self.assertIsNone(my_app["engines"]["codex"]["retry_at"])
        self.assertIsNone(side_project["engines"]["codex"]["retry_at"])


if __name__ == "__main__":
    unittest.main()
