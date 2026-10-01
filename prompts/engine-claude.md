Engine notes (Claude Code):
- Dispatch each role with the Agent tool, `subagent_type` equal to the role name: `planner`,
  `plan-reviewer`, `implementer`, `reviewer`. The scheduler defines them for this session; do not
  use other agent types for this work.
- Never use AskUserQuestion, plan mode or any tool that waits for a person. Owner decisions go
  through the decisions file (step 6).
- `planner` and `plan-reviewer` run on Fable, which has its own usage limit. If a call fails because
  that limit is reached, call the same role again with `model: "opus"` and record the substitution
  in the ledger.
- After a resume, the subagents from before the interruption are gone: reconcile from git and the
  ledger, then dispatch again.
- The session may be cut at the end of the weekend window. Commit work as soon as it is green and
  keep the ledger current, so a cut loses as little as possible.
