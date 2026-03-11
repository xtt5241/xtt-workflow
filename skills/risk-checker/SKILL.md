---
name: risk-checker
description: Score high-risk tasks and add stricter verification actions. Use when a verify task needs risk scoring from task spec, changed files, and repo profile so high-risk work gets stronger validation rules before delivery.
---

# Risk Checker

## When to use

Use this skill during verify-stage preparation when you need to decide whether the task should get stricter validation than the default repo strategy.

Typical triggers:

- dependency, lockfile, runtime, env, migration, CI, or deploy changes
- repo `high_risk_paths` touched by the diff
- user-facing changes in repos that require UI evidence
- large or cross-layer diffs that deserve stronger review

## Workflow

1. Resolve the repo path and verify task json.
2. Run `scripts/assess_verify_risk.py --repo-path <repo> --task-json <task.json>`.
3. If the report is high risk, write it back into the task and rerun test strategy.
4. Require the verifier prompt to explicitly cover:
   - risk level
   - required validation actions
   - residual high-risk items

## Output

The script produces:

- `risk_level`
- `required_validation_actions`
- `high_risk_residuals`
- `risk_check_summary`
- `risk_check_report`

## Resources

### `scripts/assess_verify_risk.py`

Score verify-stage risk from task spec, changed files, and repo profile.

Common usage:

```bash
python3 scripts/assess_verify_risk.py --repo-path ~/xtt-workflow/workspace/my-project --task-json ~/xtt-workflow/manager/queue/pending/task-123-verify.json
python3 scripts/assess_verify_risk.py --repo-path ~/xtt-workflow/workspace/my-project --task-json ~/xtt-workflow/manager/queue/pending/task-123-verify.json --write-task
```
