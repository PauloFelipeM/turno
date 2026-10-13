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
        import panel
        self.panel = panel
        self.projects = helpers.make_home(self.home)
        self.homes = {"codex": self.home / "codex-cli", "claude": self.home / "claude-cli"}
        self.cache = {}
        my-app = self.projects["my-app"] / ".superpowers" / "autopilot"
        (my-app / "backlog.json").write_text(json.dumps(BACKLOG))
        (my-app / "decisions" / "pay.json").write_text(json.dumps(DECISION))
        (my-app / "decisions" / "old.json").write_text(json.dumps(dict(DECISION, id="old", answer="a")))
        (self.home / "state.json").write_text(json.dumps({
            "my-app": {"status": "active", "interrupted": True, "unit_id": "u1", "run": {"id": "r2", "started": time.time()},
                        "cost": {"primary": 8, "secondary": 1.5}},
            "my-api": {"status": "blocked", "blocked_until": 99, "cost": {"primary": 8, "secondary": 1.5}},
            "_loop": {"phase": "running", "project": "my-app", "until": None}}))
        (self.home / "state-claude.json").write_text(json.dumps({
            "_engine": {"limits": {"primary": {"used_percent": 10, "resets_at": time.time() + 3600},
                                   "secondary": {"used_percent": 40, "resets_at": time.time() + 86400}},
                        "limits_at": 0}}))
        (self.home / "shared" / "claims").mkdir(parents=True)
        (self.home / "shared" / "claims" / "my-app.json").write_text(json.dumps({"engine": "codex", "since": 1}))
        (self.home / "runs").mkdir()
        shutil.copy(FIXTURES / "codex-run.jsonl", self.home / "runs" / "r1.jsonl")
        shutil.copy(FIXTURES / "codex-run.jsonl", self.home / "runs" / "r2.jsonl")
        lines = [
            {"unit_id": "u0", "run": "r0", "project": "my-api", "engine": "claude", "outcome": "worked", "closed": True,
             "started_at": "2026-10-10T10:00:00-03:00", "ended_at": "2026-10-10T10:05:00-03:00", "unit": "antiga"},
            {"unit_id": "u1", "run": "r1", "project": "my-app", "engine": "codex", "outcome": "unfinished", "closed": False,
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
        time.sleep(0.2)
        return proc

    def test_instances_skip_secrets(self):
        names = [i["config"] for i in self.panel.instances(self.home)]
        self.assertEqual(names, ["projects.toml", "claude.toml"])

    def test_overview_engines_and_loop_phase(self):
        self.start_loop("codex")
        o = self.overview()
        codex, claude = o["engines"]
        self.assertEqual((codex["engine"], codex["loop"]["phase"], codex["loop"]["project"]), ("codex", "running", "my-app"))
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
        self.assertEqual([p["name"] for p in o["projects"]], ["my-app", "my-api", "side-project"])
        my-app, my-api, side-project = o["projects"]
        self.assertEqual(my-app["claim"], {"engine": "codex", "handoff_from": None})
        self.assertEqual(my-app["engines"]["codex"]["interrupted"], True)
        self.assertEqual(my-app["backlog"]["current"]["id"], "admin")
        self.assertEqual((my-app["backlog"]["done"], my-app["backlog"]["total"]), (1, 3))
        self.assertEqual(my-app["decisions"], 1)
        self.assertEqual(my-api["engines"]["codex"]["status"], "blocked")
        self.assertEqual(my-api["backlog"], {"error": "missing"})

    def test_live_only_while_the_loop_runs(self):
        self.assertEqual(self.overview()["live"], [])
        self.start_loop("codex")
        [live] = self.overview()["live"]
        self.assertEqual((live["engine"], live["project"], live["unit_id"], live["run"]), ("codex", "my-app", "u1", "r2"))
        self.assertNotIn("path", live)
        [run] = self.panel.live(self.home, self.homes)
        self.assertEqual(run["items"][0]["kind"], "message")

    def test_broken_project_affects_only_its_card(self):
        shutil.rmtree(self.projects["side-project"])
        (self.projects["my-api"] / ".superpowers" / "autopilot" / "backlog.json").write_text('{"items": [{"id": 1}]}')
        o = self.overview()
        self.assertEqual(o["projects"][2]["backlog"], {"error": "missing_path"})
        self.assertEqual(o["projects"][1]["backlog"], {"error": "invalid"})
        self.assertEqual(o["projects"][0]["backlog"]["current"]["id"], "admin")

    def test_unreadable_config_is_reported_and_the_rest_works(self):
        (self.home / "broken.toml").write_text("[[[")
        o = self.overview()
        self.assertEqual(o["errors"], [{"config": "broken.toml", "error": "config"}])
        self.assertEqual(len(o["engines"]), 2)

    def test_no_secret_anywhere(self):
        self.start_loop("codex")
        dumps = json.dumps([self.overview(), self.panel.project_detail(self.home, "my-app", self.cache),
                            self.panel.units(self.home), self.panel.unit_detail(self.home, "u1"),
                            self.panel.live(self.home, self.homes)])
        self.assertNotIn("segredo-xyz", dumps)

    def test_panel_writes_nothing(self):
        before = sorted((str(p), p.stat().st_mtime_ns) for p in self.home.rglob("*"))
        self.overview()
        self.panel.units(self.home)
        self.panel.unit_detail(self.home, "u1")
        self.panel.project_detail(self.home, "my-app", self.cache)
        after = sorted((str(p), p.stat().st_mtime_ns) for p in self.home.rglob("*"))
        self.assertEqual(before, after)

    def test_project_detail(self):
        d = self.panel.project_detail(self.home, "my-app", self.cache)
        self.assertEqual([i["id"] for i in d["backlog"]["items"]], ["admin", "billing", "chat"])
        self.assertEqual([x["id"] for x in d["decisions"]], ["pay"])
        self.assertIsNone(self.panel.project_detail(self.home, "nope", self.cache))

    def test_units_filter_and_page(self):
        self.assertEqual([u["unit_id"] for u in self.panel.units(self.home)], ["u1", "u0"])
        self.assertEqual([u["unit_id"] for u in self.panel.units(self.home, project="my-api")], ["u0"])
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
        self.assertIn(str(self.projects["my-app"] / ".superpowers" / "autopilot" / "backlog.json"), files)

    def test_project_without_path_is_a_config_error(self):
        with open(self.home / "projects.toml", "a") as f:
            f.write('\n[[project]]\nname = "x"\n')
        o = self.overview()
        self.assertIn({"config": "projects.toml", "error": "config"}, o["errors"])
        self.assertEqual([e["engine"] for e in o["engines"]], ["claude"])

    def test_unexpected_json_types_are_tolerated(self):
        (self.home / "state.json").write_text("[]")
        (self.home / "shared" / "claims" / "my-app.json").write_text("[]")
        (self.projects["my-app"] / ".superpowers" / "autopilot" / "decisions" / "bad.json").write_text('"x"')
        o = self.overview()
        self.assertEqual(len(o["engines"]), 2)
        self.assertIsNone(o["projects"][0]["claim"])
        self.assertEqual(o["projects"][0]["decisions"], 1)

    def test_duplicate_engine_kind_is_reported(self):
        shutil.copy(self.home / "claude.toml", self.home / "claude2.toml")
        o = self.overview()
        self.assertIn({"config": "claude2.toml", "error": "duplicate"}, o["errors"])
        self.assertEqual(len(o["engines"]), 2)

    def test_non_string_engine_kind_is_a_config_error(self):
        (self.home / "bad.toml").write_text('[engine]\nkind = ["x"]\n\n[[project]]\nname = "x"\npath = "/tmp/x"\n')
        o = self.overview()
        self.assertIn({"config": "bad.toml", "error": "config"}, o["errors"])

    def test_panel_never_queries_the_claude_quota(self):
        import engines
        def boom(*a, **k):
            raise AssertionError("probe")
        orig = engines.ClaudeEngine.probe
        engines.ClaudeEngine.probe = boom
        self.addCleanup(setattr, engines.ClaudeEngine, "probe", orig)
        self.overview()
        self.panel.live(self.home, self.homes)
        self.panel.unit_detail(self.home, "u1")


if __name__ == "__main__":
    unittest.main()
