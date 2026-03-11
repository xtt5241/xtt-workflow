---
name: pattern-finder
description: Find similar implementations inside a repo before coding. Use when a build task needs nearby examples, reference files, or matching tests so the builder can copy existing patterns instead of inventing new ones.
---

# Pattern Finder

## When to use

Use this skill before editing code when you need a short list of similar files, routes, components, handlers, tests, or configs.

Typical triggers:

- new feature work in an unfamiliar repo
- bugfixes where you want the nearest existing implementation
- UI / API work where consistency matters
- tasks with vague titles that still mention domain keywords or allowed paths

## Workflow

1. Resolve the repo path and task context.
2. Run `scripts/find_similar_paths.py --repo-path <repo> --task-json <task.json>`.
3. Read the top candidates first, not the whole repo.
4. Prefer existing patterns over greenfield rewrites.
5. If the candidates are weak, refine the task title / goal / allowed paths and rerun.

## Output

The script produces:

- `similar_impl_paths`: ranked candidate file paths
- `pattern_finder_summary`: short human-readable summary for prompts
- `pattern_finder`: full structured report with keywords, scores, and reasons

## Resources

### `scripts/find_similar_paths.py`

Search a local repo for similar implementations using task text, allowed paths, path tokens, and content tokens.

Common usage:

```bash
python3 scripts/find_similar_paths.py --repo-path ~/xtt-workflow/workspace/my-project --task-json ~/xtt-workflow/manager/queue/pending/task-123.json
python3 scripts/find_similar_paths.py --repo-path ~/xtt-workflow/workspace/my-project --task-json ~/xtt-workflow/manager/queue/pending/task-123.json --write-task
```
