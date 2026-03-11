---
name: repo-understand
description: Inspect an unfamiliar Git repo and suggest an initial xtt-workflow repo profile. Use when onboarding a new repo under `workspace/`, creating or refreshing a file in `config/repos/`, or when the stack, commands, directories, and risk paths are still unclear.
---

# Repo Understand

## When to use

Use this skill when a repo is new to the workflow and you need a first-pass profile for:

- stack / framework detection
- install / lint / test / build command guesses
- runtime, environment, dependency, and deploy risk paths
- `needs_ui_evidence` and initial `tool_router` defaults

## Workflow

1. Resolve the repo path and repo name.
   - Typical path: `~/xtt-workflow/workspace/<repo>`
2. Run `scripts/suggest_repo_profile.py <repo_path> --repo-name <repo>`.
3. Review the output against the repo's real manifests and entrypoints:
   - `package.json`, lockfiles, `pyproject.toml`, `requirements*.txt`, `go.mod`, `Cargo.toml`
   - `Makefile`, `.github/workflows/`, `docker/`, `deploy/`, `infra/`, `k8s/`, `migrations/`
4. If the suggestion is good enough, write it into `config/repos/<repo>.json`.
   - In `xtt-workflow`, the fastest path is `scripts/onboard_repo_profile.sh <repo>`
   - Or use `--write-profile <path>` directly
5. Prefer blank commands over fake certainty.
   - If the repo has no obvious lint / build / smoke command, leave it empty and note the uncertainty.
6. Tighten the generated profile after inspection.
   - prune noisy `high_risk_paths`
   - confirm `needs_ui_evidence`
   - adjust `tool_router` if the repo has custom workflow order

## Validation

Before accepting the profile:

- Confirm the detected default branch exists on `origin/*`
- Confirm install / test / build commands are actually runnable in this repo
- Confirm dependency and lockfile paths match the real package manager
- Confirm frontend repos really require UI evidence
- Confirm deploy / CI / migration paths are captured as high-risk only when they exist

## Resources

### `scripts/suggest_repo_profile.py`

Generate a structured repo profile suggestion from a local repo path.

Common usage:

```bash
python3 scripts/suggest_repo_profile.py ~/xtt-workflow/workspace/my-project --repo-name my-project
python3 scripts/suggest_repo_profile.py ~/xtt-workflow/workspace/my-project --repo-name my-project --write-profile ~/xtt-workflow/config/repos/my-project.json
```
