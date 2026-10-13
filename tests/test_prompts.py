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

    def test_claude_roles(self):
        roles = json.loads((ROOT / "claude-agents.json").read_text())
        self.assertEqual(set(roles), {"planner", "plan-reviewer", "implementer", "reviewer"})
        for name in ("planner", "plan-reviewer"):
            self.assertEqual(roles[name]["model"], "claude-fable-5-1")
            self.assertEqual(roles[name]["fallback_model"], "claude-opus-5-5")
        for name in ("implementer", "reviewer"):
            self.assertEqual(roles[name]["model"], "claude-opus-5-5")
            self.assertNotIn("fallback_model", roles[name])
        for role in roles.values():
            self.assertTrue(role["description"] and role["prompt"])

    def test_prompts_ask_for_backlog_json(self):
        unit = (ROOT / "prompts" / "unit.md").read_text()
        resume = (ROOT / "prompts" / "resume.md").read_text()
        self.assertIn(".superpowers/autopilot/backlog.json", unit)
        self.assertIn("backlog.json", resume)
        for state in ('"todo"', '"spec"', '"plan"', '"doing"', '"done"'):
            self.assertIn(state, unit)


if __name__ == "__main__":
    unittest.main()
