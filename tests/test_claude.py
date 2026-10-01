import json
import stat
import tempfile
import time
import unittest
from pathlib import Path

import helpers

FIXTURE = Path(__file__).parent / "fixtures" / "claude-fable-probe.jsonl"
ROLES = {"planner": {"description": "d", "prompt": "p", "model": "claude-fable-5-1",
                     "fallback_model": "claude-opus-5-5"},
         "implementer": {"description": "d", "prompt": "p", "model": "claude-opus-5-5"}}


def rate_event(status="allowed", five=0.2, week=0.4, extra=None, resets=None):
    resets = resets or int(time.time()) + 3600
    windows = {"five_hour": {"utilization": five, "resetsAt": resets},
               "seven_day": {"utilization": week, "resetsAt": resets + 86400}}
    windows.update(extra or {})
    return {"type": "rate_limit_event", "rate_limit_info": {"status": status, "resetsAt": resets,
                                                             "unifiedWindows": windows}}


class ClaudeEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = helpers.load(self.tmp)
        self.engines = self.ap.engines
        roles = self.tmp / "roles.json"
        roles.write_text(json.dumps(ROLES))
        self.cfg = {"defaults": {"orchestrator_model": "claude-opus-5-5", "orchestrator_effort": "medium"}}
        self.engine = self.engines.ClaudeEngine(self.cfg, home=self.tmp / "claude-home", roles=roles)
        self.engine.binary = "claude"
        self.state = {}

    def write_run(self, *events, err=""):
        out, errf = self.tmp / "run.jsonl", self.tmp / "run.err"
        out.write_text("".join(json.dumps(e) + "\n" for e in events))
        errf.write_text(err)
        return str(out), str(errf)

    def test_real_probe_output_is_understood(self):
        infos = self.engines.rate_infos(self.engines.read_events(FIXTURE))
        self.engine.record(self.state, infos)
        limits = self.state["_engine"]["limits"]
        self.assertGreater(limits["secondary"]["used_percent"], 0)
        self.assertIn("seven_day_overage_included", limits["extra"])
        self.assertTrue(self.engine.fable_available(self.state))  # 19%, longe de acabar

    def test_to_limits_converts_to_percent(self):
        limits = self.engines.to_limits(rate_event(five=0.16, week=0.36)["rate_limit_info"])
        self.assertEqual((limits["primary"]["used_percent"], limits["secondary"]["used_percent"]), (16.0, 36.0))

    def test_exhausted_fable_window_switches_planner_to_opus(self):
        resets = int(time.time()) + 7200
        self.engine.record(self.state, [rate_event(extra={"seven_day_overage_included":
                                                          {"utilization": 0.99, "resetsAt": resets}})["rate_limit_info"]])
        self.assertFalse(self.engine.fable_available(self.state))
        path = self.tmp / "agents.json"
        self.engine.write_agents(self.state, path)
        agents = json.loads(path.read_text())
        self.assertEqual(agents["planner"]["model"], "claude-opus-5-5")
        self.assertNotIn("fallback_model", agents["planner"])

    def test_fresh_agents_keep_fable_and_drop_fallback_key(self):
        path = self.tmp / "agents.json"
        self.engine.write_agents(self.state, path)
        agents = json.loads(path.read_text())
        self.assertEqual(agents["planner"]["model"], "claude-fable-5-1")
        self.assertNotIn("fallback_model", agents["planner"])

    def test_fable_limit_in_a_subagent_result_marks_it_exhausted(self):
        tool_result = {"type": "user", "message": {"content": [{"type": "tool_result", "content": [
            {"type": "text", "text": "Agent failed: claude-fable-5-1 usage limit reached"}]}]}}
        out, _ = self.write_run(rate_event(), tool_result)
        self.engine.after_run(self.state, {"unit_start_limits": None}, out, False)
        self.assertFalse(self.engine.fable_available(self.state))

    def test_orchestrator_text_about_fable_does_not_count(self):
        said = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "If the Fable usage limit is reached I will use opus."}]}}
        out, _ = self.write_run(rate_event(), said)
        self.engine.after_run(self.state, {"unit_start_limits": None}, out, False)
        self.assertTrue(self.engine.fable_available(self.state))

    def test_first_event_of_the_unit_is_its_starting_point(self):
        s = {"unit_start_limits": None}
        out, _ = self.write_run(rate_event(week=0.40))
        self.engine.after_run(self.state, s, out, False)
        out, _ = self.write_run(rate_event(week=0.55))
        self.engine.after_run(self.state, s, out, True)
        self.assertEqual(s["unit_start_limits"]["secondary"]["used_percent"], 40.0)

    def test_rejected_without_result_is_usage_limit(self):
        resets = int(time.time()) + 1800
        out, err = self.write_run(rate_event(status="rejected", five=1.0, resets=resets))
        self.assertEqual(self.engine.classify(out, err, 1), ("usage_limit", resets, True))

    def test_rejection_of_another_model_on_a_failed_unit_is_an_error(self):
        out, err = self.write_run(rate_event(status="rejected", five=0.3, week=0.5),
                                  {"type": "result", "is_error": True, "result": "boom"})
        self.assertEqual(self.engine.classify(out, err, 1)[0], "error")

    def test_failed_probe_does_not_return_a_stale_reading(self):
        self.engine.record(self.state, [rate_event(week=0.89)["rate_limit_info"]])
        self.state["_engine"]["limits_at"] = time.time() - 7 * 86400
        self.engine.binary = str(self.tmp / "does-not-exist")
        self.assertEqual(self.engine.limits(self.state, max_age=1800), {})
        self.assertIn("secondary", self.engine.limits(self.state, max_age=float("inf")))

    def test_unit_cost_is_unknown_when_the_closing_probe_fails(self):
        start = self.engines.to_limits(rate_event(five=0.10, week=0.40, resets=2000000000)["rate_limit_info"])
        self.engine.record(self.state, [rate_event(five=0.10, week=0.40, resets=2000000000)["rate_limit_info"]])
        self.engine.probe = lambda state: None  # falhou: nada novo
        self.assertIsNone(self.engine.unit_cost(self.state, {"unit_start_limits": start}))

    def test_limit_message_with_epoch(self):
        out, err = self.write_run({"type": "result", "is_error": True,
                                   "result": "Claude AI usage limit reached|1791097200"})
        self.assertEqual(self.engine.classify(out, err, 1), ("usage_limit", 1791097200, True))

    def test_finished_unit_is_ok_even_with_rejected_event(self):
        out, err = self.write_run(rate_event(status="rejected"),
                                  {"type": "result", "subtype": "success", "is_error": False, "result": "done"})
        self.assertEqual(self.engine.classify(out, err, 0)[0], "ok")

    def test_error_result_is_error(self):
        out, err = self.write_run({"type": "result", "subtype": "error_during_execution", "is_error": True,
                                   "result": "boom"})
        self.assertEqual(self.engine.classify(out, err, 1)[0], "error")

    def test_missing_session_file_is_not_resumable(self):
        s = {"session": "11111111-1111-1111-1111-111111111111"}
        self.assertFalse(self.engine.resumable(s))
        folder = self.tmp / "claude-home" / "projects" / "-home-x"
        folder.mkdir(parents=True)
        (folder / f"{s['session']}.jsonl").write_text("{}\n")
        self.assertTrue(self.engine.resumable(s))

    def test_new_unit_gets_a_session_id_before_starting(self):
        s = {"session": "stale"}
        self.engine.begin(s, False)
        self.assertNotEqual(s["session"], "stale")
        cmd, _ = self.engine.command(self.state, {"name": "x", "path": "/tmp"}, s, False, self.tmp / "stem")
        self.assertEqual(cmd[cmd.index("--session-id") + 1], s["session"])
        self.assertIn("--dangerously-skip-permissions", cmd)
        self.assertTrue(Path(cmd[cmd.index("--agents") + 1]).exists())

    def test_resume_command(self):
        s = {"session": "abc"}
        cmd, _ = self.engine.command(self.state, {"name": "x", "path": "/tmp"}, s, True, self.tmp / "stem")
        self.assertEqual(cmd[cmd.index("--resume") + 1], "abc")

    def test_unit_cost_from_start_to_probe(self):
        s = {"unit_start_limits": self.engines.to_limits(rate_event(five=0.10, week=0.40, resets=2000000000)["rate_limit_info"])}
        self.engine.probe = lambda state: self.engine.record(
            state, [rate_event(five=0.25, week=0.43, resets=2000000000)["rate_limit_info"]]) or True
        cost = self.engine.unit_cost(self.state, s)
        self.assertAlmostEqual(cost["primary"], 15.0)
        self.assertAlmostEqual(cost["secondary"], 3.0)

    def test_probe_runs_the_cli_quietly(self):
        fake = self.tmp / "fake-claude"
        fake.write_text(f"#!/bin/sh\ncat {FIXTURE}\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.engine.binary = str(fake)
        limits = self.engine.limits(self.state, max_age=0)
        self.assertIn("secondary", limits)

    def test_failed_probe_leaves_no_limits(self):
        self.engine.binary = str(self.tmp / "does-not-exist")
        self.assertEqual(self.engine.limits(self.state, max_age=0), {})

    def test_cached_limits_are_reused(self):
        self.engine.record(self.state, [rate_event()["rate_limit_info"]])
        self.engine.probe = lambda state: self.fail("não devia consultar")
        self.assertIn("primary", self.engine.limits(self.state, max_age=1800))

    def test_make_engine(self):
        engine = self.engines.make_engine({"engine": {"kind": "claude"}, "defaults": {}})
        self.assertEqual(engine.name, "claude")
        self.assertTrue(engine.needs_limits)


if __name__ == "__main__":
    unittest.main()
