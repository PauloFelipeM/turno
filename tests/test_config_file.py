import copy
import json
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402  (sets sys.path to the repo root)

import config  # noqa: E402
import overrides  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def fixture(name):
    with open(FIX / name, "rb") as f:
        return tomllib.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def put(self, *names):
        for n in names:
            (self.home / n).write_text((FIX / n).read_text())


class ValidateTests(Base):
    def test_empty_is_valid(self):
        self.assertEqual(config.validate(config.empty()), [])

    def test_invalid_table(self):
        def mut(fn):
            d = config.empty()
            fn(d)
            return d

        project = {"name": "a", "path": "/x"}
        cases = {
            "version": mut(lambda d: d.update(version=2)),
            "projects not list": mut(lambda d: d.update(projects={})),
            "project not dict": mut(lambda d: d.update(projects=["a"])),
            "bad name": mut(lambda d: d.update(projects=[{"name": "a b", "path": "/x"}])),
            "empty path": mut(lambda d: d.update(projects=[{"name": "a", "path": ""}])),
            "duplicate": mut(lambda d: d.update(projects=[project, dict(project)])),
            "docker_group": mut(lambda d: d.update(projects=[{**project, "docker_group": ""}])),
            "cap range": mut(lambda d: d.update(projects=[{**project, "weekly_cap": {"codex": 101}}])),
            "cap bool": mut(lambda d: d.update(projects=[{**project, "weekly_cap": {"codex": True}}])),
            "cap not dict": mut(lambda d: d.update(projects=[{**project, "weekly_cap": 90}])),
            "unknown engine": mut(lambda d: d["engines"].update(gemini={})),
            "enabled": mut(lambda d: d["engines"]["codex"].update(enabled="yes")),
            "model": mut(lambda d: d["engines"]["codex"].update(orchestrator_model="")),
            "effort": mut(lambda d: d["engines"]["codex"].update(orchestrator_effort=3)),
            "safety": mut(lambda d: d["engines"]["codex"].update(safety_factor=0)),
            "primary_cap": mut(lambda d: d["engines"]["codex"].update(primary_cap=101)),
            "fable_cap": mut(lambda d: d["engines"]["claude"].update(fable_cap=-1)),
            "cost": mut(lambda d: d["engines"]["codex"].update(initial_cost_primary=-1)),
            "cooldown": mut(lambda d: d["engines"]["codex"].update(blocked_cooldown_hours="6")),
            "subagents": mut(lambda d: d["engines"]["codex"].update(max_subagents=0)),
            "subagents float": mut(lambda d: d["engines"]["codex"].update(max_subagents=1.5)),
            "schedule type": mut(lambda d: d["engines"]["claude"].update(schedule=3)),
            "schedule time": mut(lambda d: d["engines"]["claude"].update(
                schedule={"start": "xx 1", "end": "sun 03:00", "start_margin_minutes": 15})),
            "schedule same": mut(lambda d: d["engines"]["claude"].update(
                schedule={"start": "fri 19:00", "end": "fri 19:00", "start_margin_minutes": 15})),
            "schedule margin": mut(lambda d: d["engines"]["claude"].update(
                schedule={"start": "fri 19:00", "end": "sun 03:00", "start_margin_minutes": -1})),
            "notify": mut(lambda d: d.update(notify=[])),
            "notify server": mut(lambda d: d["notify"].update(server=1)),
            "notify timeout": mut(lambda d: d["notify"].update(decision_timeout_hours=0)),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertTrue(config.validate(data), label)

    def test_unknown_keys_allowed(self):
        d = config.empty()
        d["future"] = 1
        d["engines"]["codex"]["future"] = 1
        d["projects"] = [{"name": "a", "path": "/x", "future": 1, "weekly_cap": {"codex": 0}}]
        self.assertEqual(config.validate(d), [])


class LoadTests(Base):
    def test_nothing_returns_empty_without_writing(self):
        self.assertEqual(config.load(self.home), config.empty())
        self.assertFalse((self.home / config.FILE).exists())

    def test_only_projects_toml(self):
        self.put("projects.toml")
        data = config.load(self.home)
        self.assertTrue((self.home / config.FILE).exists())
        self.assertTrue(data["engines"]["codex"]["enabled"])
        self.assertFalse(data["engines"]["claude"]["enabled"])
        self.assertEqual(json.loads((self.home / config.FILE).read_text()), data)
        self.assertEqual(config.load(self.home), data)

    def test_equivalence_with_both_tomls(self):
        self.put("projects.toml", "claude.toml")
        data = config.load(self.home)
        for engine, name in (("codex", "projects.toml"), ("claude", "claude.toml")):
            with self.subTest(engine):
                want = fixture(name)
                want["notify"].pop("topic", None)
                got = config.for_engine(data, engine)
                self.assertEqual(got["defaults"], want["defaults"])
                self.assertEqual(got["project"], want["project"])
                self.assertEqual(got.get("schedule"), want.get("schedule"))
                self.assertEqual(got["notify"], want["notify"])
                self.assertEqual(got["engine"]["kind"], engine)
                self.assertEqual(got.get("instance"), want.get("instance"))
                self.assertTrue(got["enabled"])
        self.assertNotIn("instance", config.for_engine(data, "codex"))
        self.assertNotIn("schedule", config.for_engine(data, "codex"))

    def test_topic_is_not_imported(self):
        self.put("projects.toml")
        text = (self.home / "projects.toml").read_text().replace(
            "[notify]", '[notify]\ntopic = "secret-topic"')
        (self.home / "projects.toml").write_text(text)
        data = config.load(self.home)
        self.assertNotIn("topic", data["notify"])
        self.assertNotIn("secret-topic", (self.home / config.FILE).read_text())

    def test_claude_only_projects_are_appended(self):
        self.put("projects.toml", "claude.toml")
        text = (self.home / "claude.toml").read_text() + (
            '\n[[project]]\nname = "extra"\npath = "/x/extra"\nweekly_cap = 70\n')
        (self.home / "claude.toml").write_text(text)
        data = config.load(self.home)
        self.assertEqual([p["name"] for p in data["projects"]],
                         ["my-app", "my-api", "side-project", "extra"])
        self.assertEqual(data["projects"][-1]["weekly_cap"], {"claude": 70, "codex": 0})
        self.assertEqual(config.for_engine(data, "codex")["project"][-1]["weekly_cap"], 0)

    def test_codex_only_project_gets_claude_zero_only_when_claude_toml_exists(self):
        self.put("projects.toml")
        text = (self.home / "projects.toml").read_text() + (
            '\n[[project]]\nname = "extra"\npath = "/x/extra"\nweekly_cap = 70\n')
        (self.home / "projects.toml").write_text(text)
        self.assertEqual(config.import_toml(self.home)["projects"][-1]["weekly_cap"], {"codex": 70})
        self.put("claude.toml")
        self.assertEqual(config.import_toml(self.home)["projects"][-1]["weekly_cap"],
                         {"codex": 70, "claude": 0})

    def test_schedule_without_margin_gets_15(self):
        self.put("projects.toml", "claude.toml")
        text = (self.home / "claude.toml").read_text().replace(
            "start_margin_minutes = 15", "")
        (self.home / "claude.toml").write_text(text)
        data = config.load(self.home)
        self.assertEqual(data["engines"]["claude"]["schedule"]["start_margin_minutes"], 15)
        self.assertEqual(config.for_engine(data, "claude")["schedule"],
                         {"start": "fri 19:00", "end": "sun 03:00", "start_margin_minutes": 15})

    def test_overrides_folded_and_left_untouched(self):
        self.put("projects.toml", "claude.toml")
        overrides.update(self.home, lambda d: d.update(
            order=["side-project", "my-app"],
            projects={"my-api": {"paused": True, "weekly_cap": {"codex": 50}}},
            primary_cap={"claude": 70},
            next="my-app"))
        before = (self.home / overrides.FILE).read_bytes()
        data = config.load(self.home)
        self.assertEqual([p["name"] for p in data["projects"]], ["side-project", "my-app", "my-api"])
        api = data["projects"][2]
        self.assertEqual(api["weekly_cap"]["codex"], 50)
        self.assertEqual(api["weekly_cap"]["claude"], 90)
        self.assertEqual(data["engines"]["claude"]["primary_cap"], 70)
        self.assertEqual(data["engines"]["codex"]["primary_cap"], 100)
        # Loops started before the change still read these keys: the import deletes nothing.
        self.assertEqual((self.home / overrides.FILE).read_bytes(), before)
        self.assertEqual(config.validate(data), [])

    def test_for_engine_fills_keys_missing_from_the_file(self):
        data = config.empty()
        del data["engines"]["codex"]["max_subagents"]
        del data["engines"]["codex"]["safety_factor"]
        data["engines"]["codex"]["orchestrator_model"] = "mine"
        d = config.for_engine(data, "codex")["defaults"]
        self.assertEqual((d["max_subagents"], d["safety_factor"], d["orchestrator_model"]), (2, 1.5, "mine"))
        self.assertNotIn("max_subagents", config.for_engine(data, "claude")["defaults"])
        self.assertNotIn("enabled", d)
        self.assertNotIn("schedule", d)
        self.assertEqual(data["engines"]["codex"].get("max_subagents"), None)  # the input is not changed

    def test_invalid_file_raises(self):
        for text in ("{not json", "[1]", json.dumps({"version": 2})):
            (self.home / config.FILE).write_text(text)
            with self.assertRaises(config.ConfigError) as cm:
                config.load(self.home)
            self.assertTrue(cm.exception.problems)
            self.assertIsInstance(cm.exception, ValueError)


class SaveTests(Base):
    def test_valid_change_is_written(self):
        def add(d):
            d["projects"].append({"name": "a", "path": "/x"})
        data = config.save(self.home, add)
        self.assertEqual(json.loads((self.home / config.FILE).read_text()), data)
        self.assertEqual(config.load(self.home)["projects"][0]["name"], "a")
        self.assertTrue((self.home / "shared" / "config.lock").exists())

    def test_invalid_change_is_refused(self):
        config.save(self.home, lambda d: d["projects"].append({"name": "a", "path": "/x"}))
        before = (self.home / config.FILE).read_bytes()
        with self.assertRaises(config.ConfigError):
            config.save(self.home, lambda d: d["projects"].append({"name": "a", "path": "/y"}))
        self.assertEqual((self.home / config.FILE).read_bytes(), before)

    def test_concurrent_saves_both_land(self):
        config.save(self.home, lambda d: None)
        errors = []

        def add(i):
            try:
                config.save(self.home, lambda d: d["projects"].append({"name": f"p{i}", "path": "/x"}))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=add, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(config.load(self.home)["projects"]), 8)


class ForEngineTests(unittest.TestCase):
    def test_missing_engine_uses_defaults(self):
        d = config.empty()
        del d["engines"]["claude"]
        got = config.for_engine(d, "claude")
        self.assertFalse(got["enabled"])
        self.assertEqual(got["defaults"]["orchestrator_model"], "claude-opus-5-5")
        self.assertEqual(got["defaults"]["fable_cap"], 80)

    def test_defaults_and_caps(self):
        d = config.empty()
        d["projects"] = [{"name": "a", "path": "/x", "docker_group": "g", "weekly_cap": {"claude": 90}},
                         {"name": "b", "path": "/y"}]
        got = config.for_engine(d, "codex")
        self.assertEqual(got["project"], [
            {"name": "a", "path": "/x", "weekly_cap": 100, "docker_group": "g"},
            {"name": "b", "path": "/y", "weekly_cap": 100}])
        self.assertFalse(got["enabled"])
        self.assertNotIn("enabled", got["defaults"])
        self.assertNotIn("schedule", got["defaults"])
        self.assertEqual(config.for_engine(d, "claude")["project"][0]["weekly_cap"], 90)
        self.assertEqual(config.for_engine(d, "claude")["instance"], {"name": "claude"})


if __name__ == "__main__":
    unittest.main()
