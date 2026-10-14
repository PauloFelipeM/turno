import json
import unittest

import helpers

ROOT = helpers.ROOT


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
        self.assertIn('model: "opus"', claude)

    def test_claude_roles_have_only_description_and_prompt(self):
        roles = json.loads((ROOT / "claude-agents.json").read_text())
        self.assertEqual(set(roles), {"planner", "implementer", "reviewer"})
        for role in roles.values():
            self.assertEqual(set(role), {"description", "prompt"})
            self.assertTrue(role["description"] and role["prompt"])

    def test_no_plan_reviewer_in_the_protocol(self):
        files = [*(ROOT / "prompts").glob("*.md"), ROOT / "claude-agents.json", *(ROOT / "codex-agents").glob("*.toml")]
        for path in files:
            self.assertNotIn("plan-reviewer", path.read_text(), path.name)

    def test_unit_checks_the_plan_itself(self):
        unit = (ROOT / "prompts" / "unit.md").read_text()
        self.assertIn("`planner`, `implementer`, `reviewer`", unit)
        self.assertIn("planner back once", " ".join(unit.split()))
        self.assertIn("exact verification commands", unit)

    def test_claude_note_generic_model_fallback(self):
        claude = (ROOT / "prompts" / "engine-claude.md").read_text()
        self.assertNotIn("Fable", claude)
        self.assertIn("its own usage limit", claude)

    def test_prompts_ask_for_backlog_json(self):
        unit = (ROOT / "prompts" / "unit.md").read_text()
        resume = (ROOT / "prompts" / "resume.md").read_text()
        self.assertIn(".superpowers/autopilot/backlog.json", unit)
        self.assertIn("backlog.json", resume)
        for state in ('"todo"', '"spec"', '"plan"', '"doing"', '"done"'):
            self.assertIn(state, unit)


if __name__ == "__main__":
    unittest.main()
