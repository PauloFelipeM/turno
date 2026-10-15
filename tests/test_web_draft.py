"""The settings dialog's save logic (ui/src/components/settings/draft.ts), run by node as it ships: only what
changed is sent, an invalid field sends nothing, and the window goes out as the text schedule.py reads."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"

CFG = {
    "projects": [
        {"name": "my-app", "path": "/home/me/my-app", "docker_group": None, "weekly_cap": {"codex": 90, "claude": 80},
         "checks": [], "removable": True, "blocked_by": None, "setup": None},
        {"name": "my-api", "path": "/home/me/my-api", "docker_group": "web", "weekly_cap": {"codex": 60},
         "checks": [], "removable": True, "blocked_by": None, "setup": None}],
    "engines": {
        "codex": {"enabled": True, "autostart": True, "orchestrator_model": "gpt-6-sol", "orchestrator_effort": "medium",
                  "primary_cap": 100, "schedule": None, "known_models": ["gpt-6-sol"], "cli": {"found": False},
                  "roles": {"planner": {"model": "gpt-6-astra", "effort": "xhigh"},
                            "implementer": {"model": "gpt-6-sol", "effort": "medium"},
                            "reviewer": {"model": "gpt-6-astra", "effort": "high"}}},
        "claude": {"enabled": True, "autostart": False, "orchestrator_model": "claude-sonnet-5-5", "orchestrator_effort": "medium",
                   "primary_cap": 90, "schedule": {"start": "fri 19:00", "end": "sun 3:00", "start_margin_minutes": 15},
                   "known_models": [], "cli": {"found": True, "path": "/usr/bin/claude", "version": "1"},
                   "roles": {"planner": {"model": "claude-opus-5-5", "effort": "high"},
                             "implementer": {"model": "claude-sonnet-5-5", "effort": "medium"},
                             "reviewer": {"model": "claude-opus-5-5", "effort": "high"}}}},
    "notify": {"enabled": True, "server": "https://ntfy.sh", "decision_timeout_hours": 4, "has_topic": True},
    "language": "en", "languages": ["pt-BR", "en"], "warnings": []}

# Each case: a JS statement that edits the draft `d`, and the steps saveSteps must return (None = invalid).
CASES = {
    "nothing changed": ("", []),
    "the existing window is parsed back and unchanged": ("d.eng.claude.margin = '15';", []),
    "a new window": (
        "d.eng.codex.noWindow = false; d.eng.codex.start = { day: 'sat', time: '08:30' };",
        [{"url": "/api/engines/codex",
          "body": {"schedule": {"start": "sat 08:30", "end": "sun 03:00", "start_margin_minutes": 15}}}]),
    "window off": ("d.eng.claude.noWindow = true;", [{"url": "/api/engines/claude", "body": {"schedule": None}}]),
    "only the end time moved": (
        "d.eng.claude.end = { day: 'sun', time: '05:00' };",
        [{"url": "/api/engines/claude",
          "body": {"schedule": {"start": "fri 19:00", "end": "sun 05:00", "start_margin_minutes": 15}}}]),
    "start equal to end": ("d.eng.claude.end = { day: 'fri', time: '19:00' };", None),
    "an empty time": ("d.eng.claude.start = { day: 'fri', time: '' };", None),
    "a margin that is not a whole number": ("d.eng.claude.margin = '2.5';", None),
    "a role model and effort": (
        "d.eng.codex.roles.planner.model = ' gpt-6-sol '; d.eng.codex.roles.reviewer.effort = 'xhigh';",
        [{"url": "/api/engines/codex",
          "body": {"roles": {"planner": {"model": "gpt-6-sol"}, "reviewer": {"effort": "xhigh"}}}}]),
    "an empty role model": ("d.eng.codex.roles.implementer.model = '  ';", None),
    "a cap out of range": ("d.proj['my-app'].caps.codex = '101';", None),
    "an emptied cap is left alone": ("d.proj['my-app'].caps.codex = '';", []),
    "order and a cap": (
        "d.order = ['my-api', 'my-app']; d.proj['my-api'].caps.codex = '70';",
        [{"url": "/api/projects/my-api", "body": {"weekly_cap": {"codex": 70}}},
         {"url": "/api/settings", "body": {"order": ["my-api", "my-app"]}}]),
    "language and ntfy": (
        "d.gen.language = 'pt-BR'; d.gen.enabled = false; d.gen.timeout = '2';",
        [{"url": "/api/settings/general",
          "body": {"language": "pt-BR", "notify": {"enabled": False, "decision_timeout_hours": 2}}}]),
    "a server without http": ("d.gen.server = 'ntfy.sh';", None),
}


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class SaveStepsTest(unittest.TestCase):
    def test_save_steps(self):
        script = ["import { buildDraft, saveSteps } from './src/components/settings/draft.ts';",
                  f"const cfg = {json.dumps(CFG)};", "const out = {};"]
        for name, (edit, _) in CASES.items():
            script.append(f"{{ const d = buildDraft(cfg); {edit} out[{json.dumps(name)}] = saveSteps(d); }}")
        script.append("console.log(JSON.stringify(out));")
        r = subprocess.run(["node", "--input-type=module", "-e", "\n".join(script)], cwd=UI,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        for name, (_, expected) in CASES.items():
            self.assertEqual(got[name], expected, name)


if __name__ == "__main__":
    unittest.main()
