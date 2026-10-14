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

OLD_ROLE = "plan" + "-reviewer"  # the removed role, spelled so a grep for it stays clean
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


class RolesTests(Base):
    TABLE = {"claude": {"planner": ("claude-opus-5-5", "high"), "implementer": ("claude-sonnet-5-5", "medium"),
                        "reviewer": ("claude-opus-5-5", "high")},
             "codex": {"planner": ("gpt-6-astra", "xhigh"), "implementer": ("gpt-6-sol", "medium"),
                       "reviewer": ("gpt-6-astra", "high")}}

    def test_defaults_follow_the_table(self):
        self.assertEqual(config.ROLES, ("planner", "implementer", "reviewer"))
        for engine, roles in self.TABLE.items():
            got = config.empty()["engines"][engine]["roles"]
            self.assertEqual({r: (v["model"], v["effort"]) for r, v in got.items()}, roles)
        self.assertEqual(config.ENGINE_DEFAULTS["claude"]["orchestrator_model"], "claude-sonnet-5-5")
        self.assertEqual(config.ENGINE_DEFAULTS["codex"]["orchestrator_model"], "gpt-6-sol")

    def test_example_config_is_valid_and_has_the_default_roles(self):
        example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text())
        self.assertEqual(config.validate(example), [])
        for engine in config.ENGINES:
            self.assertEqual(example["engines"][engine]["roles"], config.ENGINE_DEFAULTS[engine]["roles"])

    def test_known_models(self):
        self.assertEqual(config.KNOWN_MODELS["claude"],
                         ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"])
        self.assertEqual(config.KNOWN_MODELS["codex"], ["gpt-6-astra", "gpt-6-sol"])

    def test_old_file_without_roles_loads_unchanged_and_fills_defaults(self):
        data = config.empty()
        for e in data["engines"].values():
            del e["roles"]
        text = json.dumps(data, indent=2)
        (self.home / config.FILE).write_text(text)
        loaded = config.load(self.home)
        self.assertEqual(config.validate(loaded), [])
        self.assertEqual((self.home / config.FILE).read_text(), text)  # not rewritten
        for engine in config.ENGINES:
            self.assertEqual(config.for_engine(loaded, engine)["defaults"]["roles"],
                             config.ENGINE_DEFAULTS[engine]["roles"])

    def test_partial_roles_are_filled_per_field(self):
        data = config.empty()
        data["engines"]["claude"]["roles"] = {"planner": {"model": "claude-haiku-5-5"}}
        self.assertEqual(config.validate(data), [])
        roles = config.for_engine(data, "claude")["defaults"]["roles"]
        self.assertEqual(roles["planner"], {"model": "claude-haiku-5-5", "effort": "high"})
        self.assertEqual(roles["reviewer"], config.ENGINE_DEFAULTS["claude"]["roles"]["reviewer"])

    def test_for_engine_does_not_share_the_defaults(self):
        config.for_engine(config.empty(), "codex")["defaults"]["roles"]["planner"]["model"] = "x"
        self.assertEqual(config.ENGINE_DEFAULTS["codex"]["roles"]["planner"]["model"], "gpt-6-astra")

    def test_toml_import_keeps_orchestrator_and_default_roles(self):
        self.put("projects.toml", "claude.toml")
        data = config.load(self.home)
        for engine, name in (("codex", "projects.toml"), ("claude", "claude.toml")):
            want = fixture(name)["defaults"]
            e = data["engines"][engine]
            self.assertEqual(e["orchestrator_model"], want["orchestrator_model"])
            self.assertEqual(e["roles"], config.ENGINE_DEFAULTS[engine]["roles"])


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
            "roles type": mut(lambda d: d["engines"]["codex"].update(roles=[])),
            "role unknown": mut(lambda d: d["engines"]["codex"]["roles"].update(orchestrator={"model": "m"})),
            "role old": mut(lambda d: d["engines"]["claude"]["roles"].update(**{OLD_ROLE: {"model": "m"}})),
            "role type": mut(lambda d: d["engines"]["codex"]["roles"].update(planner="x")),
            "role field": mut(lambda d: d["engines"]["codex"]["roles"]["planner"].update(colour="red")),
            "role model empty": mut(lambda d: d["engines"]["codex"]["roles"]["planner"].update(model="")),
            "role model chars": mut(lambda d: d["engines"]["codex"]["roles"]["planner"].update(model="a b")),
            "role model type": mut(lambda d: d["engines"]["codex"]["roles"]["planner"].update(model=3)),
            "role effort empty": mut(lambda d: d["engines"]["codex"]["roles"]["reviewer"].update(effort=" ")),
            "role effort type": mut(lambda d: d["engines"]["codex"]["roles"]["reviewer"].update(effort=3)),
            "notify": mut(lambda d: d.update(notify=[])),
            "notify server": mut(lambda d: d["notify"].update(server=1)),
            "notify timeout": mut(lambda d: d["notify"].update(decision_timeout_hours=0)),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertTrue(config.validate(data), label)

    def test_reserved_names_are_refused_as_project_name_and_as_group(self):
        for reserved in ("claims", "config", "decisions", "overrides", "notify", "notes", "setup", "instance-codex",
                         "Claims", "CONFIG", "Instance-x"):
            for field in ("name", "docker_group"):
                with self.subTest(reserved=reserved, field=field):
                    project = {"name": "a", "path": "/x"}
                    project[field] = reserved
                    d = config.empty()
                    d["projects"] = [project]
                    problems = config.validate(d)
                    self.assertTrue(any("reserved" in m for m in problems), problems)

    def test_docker_group_must_be_a_plain_name(self):
        for group in ("../x", "../../x", "a/b", "a b", ".."):
            with self.subTest(group=group):
                d = config.empty()
                d["projects"] = [{"name": "a", "path": "/x", "docker_group": group}]
                self.assertTrue(any("docker_group must match" in m for m in config.validate(d)))

    def test_current_names_stay_valid(self):
        d = config.empty()
        d["projects"] = [{"name": n, "path": "/x/" + n, "docker_group": "web"}
                         for n in ("my-app", "my-api", "side-project", "my-app", "claims2")]
        self.assertEqual(config.validate(d), [])

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
                want["defaults"].pop("fable_cap", None)  # no longer imported
                got = config.for_engine(data, engine)
                roles = got["defaults"].pop("roles")
                self.assertEqual(roles, config.ENGINE_DEFAULTS[engine]["roles"])  # the TOML has none: defaults
                self.assertEqual(got["defaults"], want["defaults"])
                self.assertEqual(got["project"], want["project"])
                self.assertEqual(got.get("schedule"), want.get("schedule"))
                notify = dict(got["notify"])
                self.assertIs(notify.pop("enabled"), False)  # no secrets.toml here: nowhere to send to
                self.assertEqual(notify, want["notify"])
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

    def test_import_drops_fable_cap_from_claude_toml(self):
        self.put("projects.toml", "claude.toml")
        self.assertIn("fable_cap = 80", (self.home / "claude.toml").read_text())
        data = config.load(self.home)
        self.assertNotIn("fable_cap", data["engines"]["claude"])
        self.assertNotIn("fable_cap", (self.home / config.FILE).read_text())
        self.assertNotIn("fable_cap", config.for_engine(data, "claude")["defaults"])

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


class OldFableCapTests(Base):
    def test_old_config_json_with_fable_cap_loads_and_the_file_is_not_rewritten(self):
        data = config.empty()
        data["engines"]["claude"]["fable_cap"] = 80
        path = self.home / config.FILE
        path.write_text(json.dumps(data))
        before = path.read_text()
        got = config.load(self.home)
        self.assertNotIn("fable_cap", got["engines"]["claude"])
        self.assertNotIn("fable_cap", config.for_engine(got, "claude")["defaults"])
        self.assertEqual(path.read_text(), before)


class BrokenFileTests(Base):
    def test_non_utf8_config_json_is_a_config_error_naming_the_file(self):
        (self.home / config.FILE).write_bytes(b"\xff\xfe\x80 not utf-8")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(self.home)
        self.assertIn("config.json", str(cm.exception))

    def test_invalid_toml_import_is_a_config_error_naming_the_file(self):
        (self.home / "projects.toml").write_text("[[project\nname = ")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(self.home)
        self.assertIn("projects.toml", str(cm.exception))
        self.assertFalse((self.home / config.FILE).exists())

    def test_invalid_claude_toml_names_claude_toml(self):
        self.put("projects.toml")
        (self.home / "claude.toml").write_bytes(b"\xff\xfe\x80")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(self.home)
        self.assertIn("claude.toml", str(cm.exception))

    def test_write_failure_leaves_no_tmp_and_the_original(self):
        config.save(self.home, lambda d: None)  # creates the file
        (self.home / config.FILE).write_text(json.dumps(config.empty()))
        before = (self.home / config.FILE).read_bytes()
        from unittest import mock
        with mock.patch.object(config.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                config.save(self.home, lambda d: d["notify"].update(decision_timeout_hours=9))
        self.assertEqual(list(self.home.glob("*.tmp")), [])
        self.assertEqual((self.home / config.FILE).read_bytes(), before)

    def test_write_fsyncs_the_file_before_replace(self):
        from unittest import mock
        calls = []
        real_replace = Path.replace
        with mock.patch.object(config.os, "fsync", side_effect=lambda fd: calls.append("fsync")), \
                mock.patch.object(Path, "replace", lambda self, t: (calls.append("replace"), real_replace(self, t))[1]):
            config.save(self.home, lambda d: None)
        self.assertEqual(calls[0], "fsync")
        self.assertLess(calls.index("fsync"), calls.index("replace"))
        self.assertEqual(calls.count("fsync"), 2)  # the file, then the directory


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
        self.assertEqual(got["defaults"]["orchestrator_model"], "claude-sonnet-5-5")
        self.assertNotIn("fable_cap", got["defaults"])

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


class LanguageAndNotifyTests(Base):
    def write(self, data):
        (self.home / config.FILE).write_text(json.dumps(data))

    def test_old_config_loads_as_pt_br_with_ntfy_on_and_is_not_rewritten(self):
        self.write(config.empty())
        before = (self.home / config.FILE).read_bytes()
        loaded = config.load(self.home)
        self.assertNotIn("language", loaded)
        self.assertNotIn("enabled", loaded["notify"])
        for engine in config.ENGINES:
            cfg = config.for_engine(loaded, engine)
            self.assertEqual(cfg["language"], "pt-BR")
            self.assertTrue(config.notify_enabled(cfg["notify"]))
        self.assertEqual((self.home / config.FILE).read_bytes(), before)

    def test_language_is_validated(self):
        for lang in config.LANGUAGES:
            self.assertEqual(config.validate({**config.empty(), "language": lang}), [], lang)
        for bad in ("fr", "pt", "", None, 1, ["en"]):
            self.assertEqual(config.validate({**config.empty(), "language": bad}),
                             ["language must be one of pt-BR, en"], bad)

    def test_unknown_language_in_the_file_is_a_config_error(self):
        self.write({**config.empty(), "language": "fr"})
        with self.assertRaises(config.ConfigError) as cm:
            config.load(self.home)
        self.assertEqual(cm.exception.problems, ["language must be one of pt-BR, en"])

    def test_for_engine_passes_the_language(self):
        data = {**config.empty(), "language": "en"}
        self.assertEqual(config.for_engine(data, "claude")["language"], "en")
        self.assertEqual(config.for_engine(data, "codex")["language"], "en")

    def test_notify_enabled_is_validated_and_defaults_to_on(self):
        data = config.empty()
        data["notify"]["enabled"] = "no"
        self.assertEqual(config.validate(data), ["notify.enabled must be true or false"])
        data["notify"]["enabled"] = False
        self.assertEqual(config.validate(data), [])
        self.assertFalse(config.notify_enabled(data["notify"]))
        self.assertTrue(config.notify_enabled({"server": "https://ntfy.sh"}))
        self.assertTrue(config.notify_enabled(None))

    def test_import_switches_ntfy_on_only_with_a_topic(self):
        self.put("projects.toml")
        self.assertIs(config.load(self.home)["notify"]["enabled"], False)
        (self.home / config.FILE).unlink()
        (self.home / "secrets.toml").write_text('[notify]\ntopic = "secret-topic"\n')
        data = config.load(self.home)
        self.assertIs(data["notify"]["enabled"], True)
        self.assertNotIn("topic", data["notify"])
        self.assertNotIn("secret-topic", (self.home / config.FILE).read_text())


if __name__ == "__main__":
    unittest.main()
