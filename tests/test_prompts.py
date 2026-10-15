import re
import json
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

OLD_ROLE = "plan" + "-reviewer"  # the removed role, spelled so a grep for it stays clean

ROOT = helpers.PKG


class PromptsTest(unittest.TestCase):
    def test_unit_prompt_has_no_codex_only_terms(self):
        text = (ROOT / "prompts" / "unit.md").read_text() + (ROOT / "prompts" / "resume.md").read_text()
        for term in ("spawn allowlist", "list_agents"):
            self.assertNotIn(term, text)

    def test_engine_notes_exist(self):
        codex = (ROOT / "prompts" / "engine-codex.md").read_text()
        claude = (ROOT / "prompts" / "engine-claude.md").read_text()
        self.assertIn("spawn allowlist", codex)
        self.assertIn("list_agents", codex)
        self.assertIn("subagent_type", claude)
        self.assertIn("usage limit", claude)

    def test_claude_roles_have_only_description_and_prompt(self):
        roles = json.loads((ROOT / "claude-agents.json").read_text())
        self.assertEqual(set(roles), {"planner", "implementer", "reviewer"})
        for role in roles.values():
            self.assertEqual(set(role), {"description", "prompt"})
            self.assertTrue(role["description"] and role["prompt"])

    def test_no_plan_reviewer_in_the_protocol(self):
        files = [*(ROOT / "prompts").glob("*.md"), ROOT / "claude-agents.json", *(ROOT / "codex-agents").glob("*.toml")]
        for path in files:
            self.assertNotIn(OLD_ROLE, path.read_text(), path.name)

    def test_unit_checks_the_plan_itself(self):
        unit = (ROOT / "prompts" / "unit.md").read_text()
        self.assertIn("`planner`, `implementer`, `reviewer`", unit)
        self.assertIn("planner back once", " ".join(unit.split()))
        self.assertIn("exact verification commands", unit)

    def test_claude_note_blocks_instead_of_switching_model(self):
        claude = " ".join((ROOT / "prompts" / "engine-claude.md").read_text().split())
        self.assertNotIn("Fable", claude)
        self.assertNotIn('model: "opus"', claude)
        self.assertIn("its own usage limit, do not switch models", claude)
        self.assertIn("end the unit as `blocked`", claude)
        unit = " ".join((ROOT / "prompts" / "unit.md").read_text().split())
        self.assertIn("follow the engine notes", unit)

    def test_prompts_ask_for_backlog_json(self):
        unit = (ROOT / "prompts" / "unit.md").read_text()
        resume = (ROOT / "prompts" / "resume.md").read_text()
        self.assertIn(".superpowers/autopilot/backlog.json", unit)
        self.assertIn("backlog.json", resume)
        for state in ('"todo"', '"spec"', '"plan"', '"doing"', '"done"'):
            self.assertIn(state, unit)

    def test_setup_prompt_has_both_modes(self):
        text = (ROOT / "prompts" / "setup.md").read_text()
        flat = " ".join(text.split())
        self.assertTrue(text.startswith("Autopilot setup unit — project {project}."))
        for s in ("## Draft unit", "## Apply unit", "`notes.md`", "at most 15 lines", "**Verification**",
                  "**What this file overrides**", "`autopilot.md` → `docs/autopilot.md`", "`docs: prepare for turno`",
                  "Do not push", "`.superpowers/`", "last-unit.json", "`waiting`", "`blocked`", "`planner`",
                  "Do not write `backlog.json`"):
            self.assertIn(s, flat)
        for term in ("spawn allowlist", "list_agents"):
            self.assertNotIn(term, text)

    def test_resume_points_setup_units_to_their_instructions(self):
        resume = " ".join((ROOT / "prompts" / "resume.md").read_text().split())
        self.assertIn('"Setup unit" section', resume)
        self.assertIn("not steps 3 to 8", resume)

    def test_setup_apply_survives_a_new_session(self):
        flat = " ".join((ROOT / "prompts" / "setup.md").read_text().split())
        for s in ("is a continuation", "same content as its draft file", "`.superpowers/` line in `.gitignore`",
                  "`docs: prepare for turno` and `git status --porcelain` lists nothing but `.superpowers/`",
                  "only delete `.superpowers/autopilot/setup/` and finish as `worked`", "say which files"):
            self.assertIn(s, flat)

    def test_setup_draft_overrides_the_role_and_the_engine_notes(self):
        flat = " ".join((ROOT / "prompts" / "setup.md").read_text().split())
        for s in ("override the planner role's own instructions and the engine notes", "does not exist yet",
                  "make no commit", "write no decision file", "go into `notes.md`", "run no command that writes in the repository",
                  "write only in `.superpowers/autopilot/setup/`", "you pass them on in every request to the planner"):
            self.assertIn(s, flat)


class OwnerLanguageTest(unittest.TestCase):
    ECHO = [sys.executable, "-c", "import sys; print(sys.stdin.read())"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.project = {"name": "my-app", "path": str(self.tmp / "my-app"), "weekly_cap": 90}
        Path(self.project["path"]).mkdir()
        self.cfg = {"defaults": {"initial_cost_primary": 8, "initial_cost_secondary": 1.5},
                    "project": [self.project]}
        self.state = self.ap.load_state(self.cfg)

    def prompt(self, lang=None):
        if lang:
            self.cfg["language"] = lang
        engine = helpers.FakeEngine(self.ECHO, name="codex")
        out, _, _, _ = self.ap.run_unit(self.cfg, engine, self.state, self.project)
        return Path(out).read_text()

    def test_the_prompts_name_no_fixed_language(self):
        for name in ("unit.md", "resume.md", "setup.md"):
            self.assertIn("{owner_language}", (ROOT / "prompts" / name).read_text(), name)
        for path in [*(ROOT / "prompts").glob("*.md"), ROOT / "claude-agents.json", *(ROOT / "codex-agents").glob("*.toml")]:
            text = path.read_text()
            for word in ("pt-BR", "Portuguese", "Spec do chat", "Chat spec approved", "Stock integrated"):
                self.assertNotIn(word, text, path.name)
        # No literal example of a milestone in any language: it would pull the owner's text towards it.
        unit = (ROOT / "prompts" / "unit.md").read_text()
        self.assertIsNone(re.search(r'milestone`\s*\(a few words,\s*e\.g\.', unit))
        self.assertNotRegex(unit, r'e\.g\.\s*"[^"<]')

    def test_english(self):
        prompt = self.prompt("en")
        self.assertIn("everything the owner reads is written in English (en)", prompt)
        self.assertNotIn("{owner_language}", prompt)

    def test_no_language_is_brazilian_portuguese(self):
        prompt = self.prompt()
        self.assertIn("everything the owner reads is written in Brazilian Portuguese (pt-BR)", prompt)
        self.assertNotIn("{owner_language}", prompt)

    def test_owner_messages_are_not_rewritten(self):
        self.ap.coord.add_note("my-app", "keep {owner_language} as typed")
        prompt = self.prompt("en")
        self.assertIn("keep {owner_language} as typed", prompt)
        self.assertIn("they may be in any language", prompt)


if __name__ == "__main__":
    unittest.main()
