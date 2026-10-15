import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import helpers

from turno import repo_setup as setup  # noqa: E402  (helpers puts the repository root on sys.path)


class RequestFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def write(self, text):
        path = self.home / "setup" / "app.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_missing_invalid_and_wrong_shape_are_no_request(self):
        self.assertIsNone(setup.read(self.home, "app"))
        self.assertIsNone(setup.phase(self.home, "app"))
        for text in ("{nope", "[1]", '{"status": "requested"}', '{"goal": "g", "status": "later"}'):
            self.write(text)
            self.assertIsNone(setup.read(self.home, "app"), text)
            self.assertIsNone(setup.phase(self.home, "app"), text)

    def test_tampered_fields_make_the_request_invalid(self):
        good = setup.new_request("g")
        for key, value in (("feedback", 5), ("feedback", ["x"]), ("goal", None), ("requested_at", "ontem"),
                           ("done_at", [1]), ("drafted_at", True)):
            self.write(json.dumps({**good, key: value}))
            self.assertIsNone(setup.read(self.home, "app"), (key, value))
        self.write(json.dumps({**good, "feedback": "ok", "drafted_at": 3}))
        self.assertIsNotNone(setup.read(self.home, "app"))
        self.write("[" * 100000)
        self.assertIsNone(setup.read(self.home, "app"))

    def test_new_request_has_every_field(self):
        self.assertEqual(setup.new_request("um SaaS", now=10.0),
                         {"goal": "um SaaS", "status": "requested", "feedback": None, "requested_at": 10.0,
                          "drafted_at": None, "approved_at": None, "done_at": None})

    def test_update_writes_under_the_lock_and_none_writes_nothing(self):
        setup.update(self.home, "app", lambda cur: setup.new_request("g"))
        self.assertEqual(setup.phase(self.home, "app"), "requested")
        self.assertTrue((self.home / "shared" / "setup.lock").exists())
        before = (self.home / "setup" / "app.json").read_text()
        self.assertIsNone(setup.update(self.home, "app", lambda cur: None))
        self.assertEqual((self.home / "setup" / "app.json").read_text(), before)
        self.assertEqual(list((self.home / "setup").glob("*.tmp")), [])

    def test_exception_in_fn_writes_nothing(self):
        def boom(cur):
            raise RuntimeError("no")
        with self.assertRaises(RuntimeError):
            setup.update(self.home, "app", boom)
        self.assertFalse((self.home / "setup" / "app.json").exists())

    def test_concurrent_updates_all_land(self):
        setup.update(self.home, "app", lambda cur: {**setup.new_request("g"), "n": 0})
        threads = [threading.Thread(target=setup.update, args=(self.home, "app", lambda cur: {**cur, "n": cur["n"] + 1}))
                   for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(setup.read(self.home, "app")["n"], 20)

    def test_transition_only_from_the_expected_phase(self):
        setup.update(self.home, "app", lambda cur: setup.new_request("g"))
        self.assertFalse(setup.transition(self.home, "app", "review", "approved"))
        self.assertTrue(setup.transition(self.home, "app", "requested", "review"))
        r = setup.read(self.home, "app")
        self.assertEqual(r["status"], "review")
        self.assertIsInstance(r["drafted_at"], float)
        self.assertTrue(setup.transition(self.home, "app", "review", "requested", feedback="mais testes"))
        self.assertEqual(setup.read(self.home, "app")["feedback"], "mais testes")
        self.assertFalse(setup.transition(self.home, "other", "requested", "review"))
        self.assertFalse((self.home / "setup" / "other.json").exists())

    def test_cancel_keeps_an_approved_request_and_delete_removes_any(self):
        setup.update(self.home, "app", lambda cur: setup.new_request("g"))
        self.assertEqual(setup.cancel(self.home, "app"), "requested")
        self.assertIsNone(setup.read(self.home, "app"))
        self.assertIsNone(setup.cancel(self.home, "app"))
        setup.update(self.home, "app", lambda cur: {**setup.new_request("g"), "status": "approved"})
        self.assertEqual(setup.cancel(self.home, "app"), "approved")
        self.assertEqual(setup.phase(self.home, "app"), "approved")
        setup.delete(self.home, "app")
        self.assertIsNone(setup.read(self.home, "app"))
        setup.delete(self.home, "app")  # missing: no error


class DraftFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "proj"
        self.draft = self.root / setup.DRAFT
        self.draft.mkdir(parents=True)
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "notes.md").write_text("secret")

    def put(self, rel, text="x"):
        path = self.draft / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_order_is_notes_then_autopilot_then_the_rest(self):
        for rel in ("docs/superpowers/roadmap.md", "AGENTS.md", "autopilot.md", "notes.md"):
            self.put(rel)
        self.assertEqual(setup.draft_files(self.root),
                         ["notes.md", "autopilot.md", "AGENTS.md", "docs/superpowers/roadmap.md"])

    def test_missing_folder_is_empty(self):
        self.assertEqual(setup.draft_files(self.base / "nothing"), [])
        self.assertEqual(setup.read_drafts(self.base / "nothing"), [])

    def test_symlinks_are_skipped_and_never_followed(self):
        self.put("autopilot.md")
        (self.draft / "leak.md").symlink_to(self.outside / "notes.md")
        (self.draft / "dir").symlink_to(self.outside, target_is_directory=True)
        self.assertEqual(setup.draft_files(self.root), ["autopilot.md"])
        self.assertNotIn("secret", json.dumps(setup.read_drafts(self.root)))

    def test_draft_folder_that_is_a_symlink_is_refused(self):
        self.draft.rmdir()
        self.draft.symlink_to(self.outside, target_is_directory=True)
        self.assertEqual(setup.draft_files(self.root), [])
        self.assertEqual(setup.read_drafts(self.root), [])

    def test_read_drafts_text_and_size_limit(self):
        self.put("notes.md", "olá")
        self.put("big.md", "x" * (setup.MAX_FILE + 1))
        files = {f["path"]: f for f in setup.read_drafts(self.root)}
        self.assertEqual(files["notes.md"], {"path": "notes.md", "size": 4, "text": "olá"})
        self.assertEqual((files["big.md"]["size"], files["big.md"]["text"]), (setup.MAX_FILE + 1, None))

    def test_fifo_and_other_special_files_are_ignored(self):
        import os
        self.put("notes.md", "ok")
        os.mkfifo(self.draft / "pipe.md")
        files = {f["path"] for f in setup.read_drafts(self.root)}
        self.assertEqual(files, {"notes.md"})

    def test_at_most_max_files(self):
        for i in range(setup.MAX_FILES + 5):
            self.put(f"f{i:03}.md")
        self.assertEqual(len(setup.draft_files(self.root)), setup.MAX_FILES)


class ExcludeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        helpers.isolate_git(self, self._tmp.name)
        self.repo = helpers.git_repo(Path(self._tmp.name) / "repo")
        self.exclude = self.repo / ".git" / "info" / "exclude"

    def lines(self):
        return self.exclude.read_text().splitlines() if self.exclude.exists() else []

    def test_written_once_and_the_draft_does_not_dirty_the_tree(self):
        (self.repo / setup.DRAFT).mkdir(parents=True)
        (self.repo / setup.DRAFT / "notes.md").write_text("x")
        (self.repo / ".superpowers" / "sdd").mkdir()
        (self.repo / ".superpowers" / "sdd" / "ledger.md").write_text("x")
        self.assertTrue(setup.ensure_excluded(self.repo))
        self.assertFalse(setup.ensure_excluded(self.repo))
        self.assertEqual(self.lines().count(".superpowers/"), 1)
        status = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"],
                                capture_output=True, text=True).stdout
        self.assertEqual(status, "")

    def test_already_ignored_by_gitignore_leaves_exclude_alone(self):
        (self.repo / ".gitignore").write_text(".superpowers/\n")
        before = self.lines()
        self.assertFalse(setup.ensure_excluded(self.repo))
        self.assertEqual(self.lines(), before)

    def test_exclude_without_a_final_newline_gets_one(self):
        self.exclude.parent.mkdir(parents=True, exist_ok=True)
        self.exclude.write_text("*.log")
        self.assertTrue(setup.ensure_excluded(self.repo))
        self.assertEqual(self.exclude.read_text(), "*.log\n.superpowers/\n")

    def test_exclude_that_is_not_utf8_does_not_raise(self):
        self.exclude.parent.mkdir(parents=True, exist_ok=True)
        self.exclude.write_bytes(b"\xff\xfe*.log\n")
        self.assertTrue(setup.ensure_excluded(self.repo))
        self.assertTrue(self.exclude.read_bytes().endswith(b".superpowers/\n"))

    def test_relative_git_path_is_resolved_against_the_project(self):
        calls = []
        real = subprocess.run
        def fake(cmd, **kw):
            calls.append(cmd)
            if "check-ignore" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, ".git/info/exclude\n", "")
        from unittest import mock
        with mock.patch.object(subprocess, "run", fake):
            self.assertTrue(setup.ensure_excluded(self.repo))
        self.assertFalse(any("--path-format=absolute" in c for c in calls))
        self.assertIn(".superpowers/", self.exclude.read_text())

    def test_not_a_repository_does_nothing(self):
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        self.assertFalse(setup.ensure_excluded(plain))
        self.assertFalse((plain / ".git").exists())


if __name__ == "__main__":
    unittest.main()
