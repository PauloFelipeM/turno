import tempfile
import unittest
from pathlib import Path

import helpers

FIXTURES = Path(__file__).parent / "fixtures"


def strip(items):
    return [{k: v for k, v in i.items() if k != "n"} for i in items]


class TranslateTest(unittest.TestCase):
    def setUp(self):
        helpers.load(Path(tempfile.mkdtemp()))
        import feed
        self.feed = feed

    def test_non_text_file_path_does_not_raise(self):
        event = {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": 3}]}}
        self.assertIsInstance(self.feed.translate("codex", event, root="/tmp/proj"), list)

    def test_codex_run(self):
        items = strip(self.feed.read_run("codex", FIXTURES / "codex-run.jsonl", root="/tmp/proj"))
        self.assertEqual(items, [
            {"kind": "message", "text": "Vou retomar a tarefa 4."},
            {"kind": "command", "text": "php artisan test --filter=Clinic"},
            {"kind": "command_done", "text": "php artisan test --filter=Clinic", "exit_code": 0,
             "output": "PASS  Tests\\Feature\\ClinicTest\n\nTests:  12 passed"},
            {"kind": "wait", "text": ""},
            {"kind": "file", "text": "app/Policies/ClinicPolicy.php"},
            {"kind": "unknown", "text": "web_search"},
            {"kind": "error", "text": "Falling back from WebSockets to HTTPS transport."},
        ])

    def test_claude_run(self):
        items = strip(self.feed.read_run("claude", FIXTURES / "claude-run.jsonl", root="/tmp/proj"))
        self.assertEqual(items, [
            {"kind": "message", "text": "Vou ler o ledger."},
            {"kind": "command", "text": "git status"},
            {"kind": "role", "role": "planner", "text": "Escrever a spec"},
            {"kind": "file", "text": "docs/spec.md"},
            {"kind": "unknown", "text": "mystery"},
            {"kind": "error", "text": "Claude AI usage limit reached"},
        ])

    def test_items_carry_the_event_number(self):
        items = self.feed.read_run("codex", FIXTURES / "codex-run.jsonl")
        self.assertEqual(items[0]["n"], 2)  # the first two events produce no item

    def test_missing_run_is_none(self):
        self.assertIsNone(self.feed.read_run("codex", "/nonexistent/x.jsonl"))

    def test_unknown_engine(self):
        self.assertEqual(self.feed.translate("gemini", {"type": "x"}), [{"kind": "unknown", "text": "x"}])

    def test_long_output_keeps_the_end(self):
        import engines
        out = engines.tail_output("\n".join(f"linha {i}" for i in range(100)))
        self.assertEqual(out.splitlines()[0], "linha 80")
        self.assertEqual(len(engines.tail_output("x" * 5000)), 2000)

    def test_malformed_events_become_unknown(self):
        cases = [
            ("codex", {"type": "error", "error": "texto"}),
            ("codex", {"type": "item.completed", "item": "x"}),
            ("codex", {"type": "item.completed", "item": {"type": "file_change", "changes": ["a"]}}),
            ("claude", {"type": "assistant", "message": ["x"]}),
            ("claude", {"type": "assistant", "message": {"content": [{"type": "text", "text": 3}]}}),
            ("claude", {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": "x"}]}}),
        ]
        import engines
        for name, ev in cases:
            with self.assertRaises(Exception, msg=ev):
                engines.ENGINES[name].feed_items(ev)
            self.assertEqual(self.feed.translate(name, ev), [{"kind": "unknown", "text": ev["type"]}], ev)


class TailTest(unittest.TestCase):
    def setUp(self):
        helpers.load(Path(tempfile.mkdtemp()))
        import feed
        self.feed = feed
        self.path = Path(tempfile.mkdtemp()) / "run.jsonl"

    def test_reads_only_complete_lines_once(self):
        tail = self.feed.Tail(self.path)
        self.assertEqual(tail.read(), [])  # the file does not exist yet
        self.path.write_bytes(b'{"a": 1}\n{"b": ')
        self.assertEqual(tail.read(), [(0, {"a": 1})])
        with self.path.open("ab") as f:
            f.write(b'2}\n')
        self.assertEqual(tail.read(), [(1, {"b": 2})])
        self.assertEqual(tail.read(), [])

    def test_bad_bytes_and_bad_json_are_skipped(self):
        self.path.write_bytes(b'\xff\xfe\n{"ok": "\xc3\xa9"}\nnot json\n')
        self.assertEqual(self.feed.Tail(self.path).read(), [(1, {"ok": "é"})])

    def test_flush_reads_a_last_line_without_newline(self):
        self.path.write_bytes(b'{"a": 1}\n{"b": 2}')
        tail = self.feed.Tail(self.path)
        self.assertEqual(tail.read(), [(0, {"a": 1})])
        self.assertEqual(tail.flush(), [(1, {"b": 2})])


if __name__ == "__main__":
    unittest.main()
