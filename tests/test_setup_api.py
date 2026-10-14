import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import helpers

CLI = {"found": False, "path": None, "version": None}


class SetupApiTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        helpers.isolate_git(self, self.home)
        self.ap = helpers.load(self.home)
        import engines
        import panel
        import server
        import repo_setup as setup
        self.panel, self.setup = panel, setup
        self.projects = helpers.make_home(self.home)
        helpers.git_repo(self.projects["side-project"])
        helpers.git_repo(self.projects["my-api"])
        patch = mock.patch.object(engines, "cli_status", lambda name, max_age=60: dict(CLI))  # never run a real CLI
        patch.start()
        self.addCleanup(patch.stop)
        self.homes = {"codex": self.home / "cx", "claude": self.home / "cl"}
        self.hub = server.Hub(self.home, self.homes, interval=0.1)
        self.srv = server.Server(self.hub, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.draft = self.projects["side-project"] / setup.DRAFT

    def call(self, method, path, body=None, turno="1"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if turno is not None:
            h["X-Turno"] = turno
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        out = r.read()
        conn.close()
        return r.status, json.loads(out) if out else None

    def phase(self, name="side-project"):
        return self.setup.phase(self.home, name)

    def put_review(self, name="side-project"):
        self.setup.update(self.home, name, lambda cur: {**self.setup.new_request("g"), "status": "review"})

    def put_draft(self, rel, text="x"):
        path = self.draft / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    # create

    def test_create_a_request(self):
        status, body = self.call("POST", "/api/projects/side-project/setup", {"goal": "  Um SaaS de agenda  "})
        self.assertEqual((status, body), (200, {"ok": True, "setup": "requested"}))
        r = self.setup.read(self.home, "side-project")
        self.assertEqual((r["status"], r["goal"], r["feedback"]), ("requested", "Um SaaS de agenda", None))
        import overrides
        self.assertIn("poke", overrides.read(self.home))

    def test_create_refusals_write_nothing(self):
        cases = [("/api/projects/nope/setup", {"goal": "g"}, 404, "not_found"),
                 ("/api/projects/side-project/setup", {"goal": ""}, 400, "bad_request"),
                 ("/api/projects/side-project/setup", {"goal": 5}, 400, "bad_request"),
                 ("/api/projects/side-project/setup", {"goal": "x" * 4001}, 400, "bad_request"),
                 ("/api/projects/side-project/setup", {"goal": "g", "other": 1}, 400, "bad_request"),
                 ("/api/projects/my-app/setup", {"goal": "g"}, 400, "not_git")]
        for path, body, code, error in cases:
            status, out = self.call("POST", path, body)
            self.assertEqual((status, out["error"]), (code, error), body)
        self.assertFalse((self.home / "setup").exists())

    def test_create_refused_when_prepared_or_open(self):
        (self.projects["my-api"] / "docs").mkdir()
        (self.projects["my-api"] / "docs" / "autopilot.md").write_text("x")
        status, out = self.call("POST", "/api/projects/my-api/setup", {"goal": "g"})
        self.assertEqual((status, out["error"]), (409, "prepared"))
        self.assertIsNone(self.phase("my-api"))
        for phase in ("requested", "review", "approved"):
            self.setup.update(self.home, "side-project", lambda cur, ph=phase: {**self.setup.new_request("old"), "status": ph})
            status, out = self.call("POST", "/api/projects/side-project/setup", {"goal": "new"})
            self.assertEqual((status, out["error"]), (409, "setup_open"), phase)
            self.assertEqual(self.setup.read(self.home, "side-project")["goal"], "old")
        self.setup.update(self.home, "side-project", lambda cur: {**cur, "status": "done"})
        self.assertEqual(self.call("POST", "/api/projects/side-project/setup", {"goal": "new"})[0], 200)

    # read

    def test_get_setup_has_the_request_and_the_draft_files(self):
        self.put_review()
        for rel, text in (("autopilot.md", "# A"), ("notes.md", "olá"), ("docs/superpowers/roadmap.md", "# R"),
                          ("big.md", "x" * (self.setup.MAX_FILE + 1))):
            self.put_draft(rel, text)
        status, body = self.call("GET", "/api/projects/side-project/setup")
        self.assertEqual(status, 200)
        self.assertEqual(body["request"]["status"], "review")
        self.assertEqual([f["path"] for f in body["files"]],
                         ["notes.md", "autopilot.md", "big.md", "docs/superpowers/roadmap.md"])
        self.assertEqual(body["files"][0]["text"], "olá")
        self.assertIsNone(body["files"][2]["text"])
        self.assertEqual(self.call("GET", "/api/projects/nope/setup")[0], 404)
        self.assertEqual(self.call("GET", "/api/projects/my-api/setup")[1], {"request": None, "files": []})

    def test_get_setup_never_reads_through_a_symlink(self):
        secret = self.home / "secret.md"
        secret.write_text("SECRET")
        self.put_draft("notes.md", "ok")
        (self.draft / "leak.md").symlink_to(secret)
        body = self.call("GET", "/api/projects/side-project/setup")[1]
        self.assertEqual([f["path"] for f in body["files"]], ["notes.md"])
        outside = self.home / "outside"
        outside.mkdir()
        (outside / "notes.md").write_text("SECRET")
        other = self.projects["my-api"] / ".superpowers" / "autopilot" / "setup"
        other.symlink_to(outside, target_is_directory=True)
        body = self.call("GET", "/api/projects/my-api/setup")[1]
        self.assertEqual(body["files"], [])
        self.assertNotIn("SECRET", json.dumps(body))

    # approve, changes, cancel

    def test_approve_only_from_review(self):
        self.setup.update(self.home, "side-project", lambda cur: self.setup.new_request("g"))
        status, out = self.call("POST", "/api/projects/side-project/setup/approve", {})
        self.assertEqual((status, out["error"]), (409, "wrong_phase"))
        self.put_review()
        self.assertEqual(self.call("POST", "/api/projects/side-project/setup/approve", {}),
                         (200, {"ok": True, "setup": "approved"}))
        self.assertIsInstance(self.setup.read(self.home, "side-project")["approved_at"], float)
        self.assertEqual(self.call("POST", "/api/projects/side-project/setup/approve", {})[1]["error"], "wrong_phase")
        self.assertEqual(self.call("POST", "/api/projects/nope/setup/approve", {})[0], 404)

    def test_changes_go_back_to_requested_with_the_feedback(self):
        self.put_review()
        self.assertEqual(self.call("POST", "/api/projects/side-project/setup/changes", {})[0], 400)
        self.assertEqual(self.call("POST", "/api/projects/side-project/setup/changes", {"feedback": " "})[0], 400)
        self.assertEqual(self.call("POST", "/api/projects/side-project/setup/changes", {"feedback": "use pnpm"}),
                         (200, {"ok": True, "setup": "requested"}))
        r = self.setup.read(self.home, "side-project")
        self.assertEqual((r["status"], r["feedback"]), ("requested", "use pnpm"))
        self.assertEqual(self.call("POST", "/api/projects/side-project/setup/changes", {"feedback": "x"})[1]["error"],
                         "wrong_phase")

    def test_cancel_keeps_the_draft_and_refuses_an_approved_request(self):
        self.put_draft("notes.md")
        self.assertEqual(self.call("DELETE", "/api/projects/side-project/setup")[1]["error"], "not_found")
        self.setup.update(self.home, "side-project", lambda cur: self.setup.new_request("g"))
        self.assertEqual(self.call("DELETE", "/api/projects/side-project/setup"), (200, {"ok": True}))
        self.assertIsNone(self.phase())
        self.assertTrue((self.draft / "notes.md").exists())
        self.setup.update(self.home, "side-project", lambda cur: {**self.setup.new_request("g"), "status": "approved"})
        status, out = self.call("DELETE", "/api/projects/side-project/setup")
        self.assertEqual((status, out["error"]), (409, "wrong_phase"))
        self.assertEqual(self.phase(), "approved")

    def test_writes_need_the_guard(self):
        self.put_review()
        before = (self.home / "setup" / "side-project.json").read_bytes()
        for method, path, body in (("POST", "/api/projects/my-api/setup", {"goal": "g"}),
                                   ("POST", "/api/projects/side-project/setup/approve", {}),
                                   ("POST", "/api/projects/side-project/setup/changes", {"feedback": "f"}),
                                   ("DELETE", "/api/projects/side-project/setup", None)):
            self.assertEqual(self.call(method, path, body, turno=None)[0], 403, path)
        self.assertEqual((self.home / "setup" / "side-project.json").read_bytes(), before)
        self.assertIsNone(self.phase("my-api"))

    def test_removing_a_project_deletes_its_request_only(self):
        self.put_review()
        self.put_draft("notes.md")
        self.assertEqual(self.call("DELETE", "/api/projects/side-project")[0], 200)
        self.assertFalse((self.home / "setup" / "side-project.json").exists())
        self.assertTrue((self.draft / "notes.md").exists())

    # views

    def test_overview_and_config_carry_the_phase(self):
        self.put_review()
        o = {p["name"]: p for p in self.panel.overview(self.home, {}, self.homes)["projects"]}
        self.assertEqual((o["side-project"]["setup"], o["my-api"]["setup"]), ("review", None))
        c = {p["name"]: p for p in self.call("GET", "/api/config")[1]["projects"]}
        self.assertEqual((c["side-project"]["setup"], c["my-api"]["setup"]), ("review", None))
        self.assertIn(self.home / "setup" / "side-project.json", self.panel.watched_files(self.home))


if __name__ == "__main__":
    unittest.main()
