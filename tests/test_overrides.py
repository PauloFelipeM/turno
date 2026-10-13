import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402  (sets sys.path to the repo root)

import overrides  # noqa: E402


class FileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_read_missing_invalid_and_non_dict(self):
        self.assertEqual(overrides.read(self.home), {})
        (self.home / overrides.FILE).write_text("{not json")
        self.assertEqual(overrides.read(self.home), {})
        (self.home / overrides.FILE).write_text("[1, 2]")
        self.assertEqual(overrides.read(self.home), {})

    def test_update_creates_file_keeps_keys_and_sets_poke(self):
        overrides.update(self.home, lambda d: d.update(next="a"))
        data = overrides.update(self.home, lambda d: d.update(order=["b"]))
        on_disk = json.loads((self.home / overrides.FILE).read_text())
        self.assertEqual(on_disk, data)
        self.assertEqual((data["next"], data["order"]), ("a", ["b"]))
        self.assertIsInstance(data["poke"], float)
        self.assertTrue((self.home / "shared" / "overrides.lock").exists())

    def test_concurrent_updates_both_land(self):
        overrides.update(self.home, lambda d: d.update(items=[]))
        threads = [
            threading.Thread(target=overrides.update, args=(self.home, lambda d, i=i: d["items"].append(i)))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(overrides.read(self.home)["items"]), list(range(20)))

    def test_take_next_removes_only_matching(self):
        overrides.update(self.home, lambda d: d.update(next="a"))
        poke = overrides.read(self.home)["poke"]
        self.assertFalse(overrides.take_next(self.home, "b"))
        self.assertEqual(overrides.read(self.home)["next"], "a")
        self.assertTrue(overrides.take_next(self.home, "a"))
        ov = overrides.read(self.home)
        self.assertNotIn("next", ov)
        self.assertEqual(ov["poke"], poke)
        self.assertFalse(overrides.take_next(self.home, "a"))

    def test_resets_filters_non_numbers(self):
        ov = {"reset": {"a": 1.5, "b": "x", "c": True, "d": 3, "e": None}}
        self.assertEqual(overrides.resets(ov), {"a": 1.5, "d": 3})
        self.assertEqual(overrides.resets({}), {})
        self.assertEqual(overrides.resets({"reset": [1]}), {})


class ApplyTests(unittest.TestCase):
    def cfg(self):
        return {
            "defaults": {"primary_cap": 80},
            "project": [
                {"name": "a", "weekly_cap": 95},
                {"name": "b", "weekly_cap": 85},
                {"name": "c", "weekly_cap": 60},
            ],
        }

    def names(self, cfg):
        return [p["name"] for p in cfg["project"]]

    def test_order_with_unknown_and_missing(self):
        out = overrides.apply(self.cfg(), "codex", {"order": ["c", "zzz", "a"]})
        self.assertEqual(self.names(out), ["c", "a", "b"])

    def test_bad_order_ignored(self):
        self.assertEqual(self.names(overrides.apply(self.cfg(), "codex", {"order": "c"})), ["a", "b", "c"])

    def test_caps_per_engine(self):
        ov = {"projects": {"a": {"weekly_cap": {"codex": 50, "claude": 70.5}}}, "primary_cap": {"codex": 100}}
        codex = overrides.apply(self.cfg(), "codex", ov)
        claude = overrides.apply(self.cfg(), "claude", ov)
        self.assertEqual(codex["project"][0]["weekly_cap"], 50)
        self.assertEqual(claude["project"][0]["weekly_cap"], 70.5)
        self.assertEqual(codex["defaults"]["primary_cap"], 100)
        self.assertEqual(claude["defaults"]["primary_cap"], 80)

    def test_invalid_caps_ignored(self):
        for bad in ("50", True, 150, -1, None, [1]):
            ov = {"projects": {"a": {"weekly_cap": {"codex": bad}}}, "primary_cap": {"codex": bad}}
            out = overrides.apply(self.cfg(), "codex", ov)
            self.assertEqual(out["project"][0]["weekly_cap"], 95, bad)
            self.assertEqual(out["defaults"]["primary_cap"], 80, bad)

    def test_malformed_parts_do_not_raise(self):
        for ov in ({"projects": [1]}, {"projects": {"a": 3}}, {"projects": {"a": {"weekly_cap": 5}}},
                   {"primary_cap": 7}, {"order": [1, None, ["x"]]}):
            out = overrides.apply(self.cfg(), "codex", ov)
            self.assertEqual(self.names(out), ["a", "b", "c"])

    def test_paused_default_false(self):
        out = overrides.apply(self.cfg(), "codex", {"projects": {"b": {"paused": True}}})
        self.assertEqual([p["paused"] for p in out["project"]], [False, True, False])

    def test_cfg_not_mutated(self):
        cfg = self.cfg()
        before = copy.deepcopy(cfg)
        overrides.apply(cfg, "codex", {"order": ["c"], "projects": {"a": {"paused": True, "weekly_cap": {"codex": 1}}}})
        self.assertEqual(cfg, before)


if __name__ == "__main__":
    unittest.main()
