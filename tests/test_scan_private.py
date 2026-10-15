"""scripts/scan_private.py on throwaway repositories: planted private data is found, a clean repo passes."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

SCRIPT = helpers.ROOT / "scripts" / "scan_private.py"
# Built from pieces so this file itself never holds a planted value.
NAME = "acme" + "-secret-app"
HOME_PATH = "/home/" + "alice/projects"
TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
TOPIC_LINE = "to" + 'pic = "my-real-topic-123"'
MAIL = "someone@" + "gmail.com"
LINK = "https://ntfy" + ".sh/my-real-topic-123"


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        helpers.isolate_git(self, self.tmp)
        self.repo = helpers.git_repo(self.tmp / "repo")
        self.patterns = self.tmp / "private-patterns.txt"
        self.patterns.write_text(f"# the owner's list\n{NAME}\nre:Bob\\s+Example\n")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, check=True)

    def commit(self, files, message="change"):
        for rel, text in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if text is None:
                path.unlink()
            else:
                path.write_text(text)
        self.git("add", "-A")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)

    def scan(self, *flags, patterns=None):
        r = subprocess.run([sys.executable, str(SCRIPT), "--patterns", str(patterns or self.patterns), *flags, str(self.repo)],
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def test_a_clean_repository_passes(self):
        self.commit({"app.py": 'PATH = "/home/me/projects/my-app"\n', "secrets.example.toml": 'topic = "replace-with-a-random-name"\n'})
        self.assertEqual(self.scan(), (0, "clean: nothing found\n"))

    def test_a_planted_name_in_the_tree_and_in_the_history(self):
        self.commit({"docs/notes.md": f"Run it on {NAME.upper()} first.\n"})
        code, out = self.scan("--tree")
        self.assertEqual(code, 1)
        self.assertIn("tree file docs/notes.md: patterns line 2", out)
        code, out = self.scan("--history")
        self.assertEqual(code, 1)
        self.assertIn("(docs/notes.md): patterns line 2", out)
        self.assertNotIn(NAME, out.lower())  # the output never repeats the private text

    def test_history_finds_what_the_tree_no_longer_has(self):
        self.commit({"old.txt": f"see {HOME_PATH}\n"})
        self.commit({"old.txt": None}, "remove it")
        self.assertEqual(self.scan("--tree")[0], 0)
        code, out = self.scan("--history")
        self.assertEqual(code, 1)
        self.assertIn("home-path", out)

    def test_commit_messages_and_file_names(self):
        self.commit({"a.txt": "x\n"}, message="fix: thanks Bob  Example")
        self.commit({f"{NAME}.md": "x\n"})
        code, out = self.scan("--history")
        self.assertEqual(code, 1)
        self.assertIn("commit message", out)
        self.assertIn("history path ***.md: patterns line 2", out)  # the path is printed masked
        self.assertNotIn(NAME, out.lower())
        self.assertNotIn("Bob", out)

    def test_built_in_rules(self):
        self.commit({"a.toml": f"{TOPIC_LINE}\n", "b.md": f"mail me at {MAIL}\n",
                     "c.txt": f"{TOKEN}\n", "d.md": f"subscribe: {LINK}\n"})
        code, out = self.scan("--tree")
        self.assertEqual(code, 1)
        for rule in ("ntfy-topic", "gmail-address", "github-token", "ntfy-link"):
            self.assertIn(rule, out)

    def test_placeholders_pass(self):
        self.commit({"t.py": "write('[notify]\\ntopic = \"segredo-xyz\"\\n')\nlink = f'https://ntfy.sh/{topic}'\n"
                             "def task-free(): pass\n"})
        self.assertEqual(self.scan()[0], 0)

    def test_a_tracked_patterns_file_is_refused(self):
        inside = self.repo / "patterns.txt"
        inside.write_text(f"{NAME}\n")
        self.commit({})
        code, out = self.scan(patterns=inside)
        self.assertEqual(code, 2)
        self.assertIn("never be committed", out)

    def test_an_empty_patterns_file_is_refused(self):
        self.patterns.write_text("# nothing\n\n")
        self.assertEqual(self.scan()[0], 2)

    def test_untracked_files_that_git_would_publish_count(self):
        (self.repo / "new.md").write_text(f"{NAME}\n")
        self.assertEqual(self.scan("--tree")[0], 1)
        (self.repo / ".gitignore").write_text("new.md\n")
        self.assertEqual(self.scan("--tree")[0], 0)


if __name__ == "__main__":
    unittest.main()
