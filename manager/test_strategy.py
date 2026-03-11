from __future__ import annotations

from pathlib import Path
import json
import sys

from repo_profile import load_repo_profile, normalize_repo_profile

LEVELS = ["smoke", "targeted", "repo_default", "extended"]


def dedupe(items: list[str]) -> list[str]:
    result = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def strategy_commands(profile: dict) -> dict[str, list[str]]:
    lint_cmd = str(profile.get("lint_cmd", "")).strip()
    test_cmd = str(profile.get("test_cmd", "")).strip()
    build_cmd = str(profile.get("build_cmd", "")).strip()
    smoke_cmd = str(profile.get("smoke_test_cmd", "")).strip()
    targeted_cmd = str(profile.get("targeted_test_cmd", "")).strip()
    extended_cmd = str(profile.get("extended_test_cmd", "")).strip()

    commands = {
        "smoke": dedupe([smoke_cmd or test_cmd]),
        "targeted": dedupe([targeted_cmd or smoke_cmd or test_cmd]),
        "repo_default": dedupe([test_cmd, build_cmd]),
        "extended": dedupe([extended_cmd, lint_cmd, test_cmd, build_cmd, smoke_cmd]),
    }

    fallback = dedupe(commands["repo_default"] + commands["targeted"] + commands["smoke"])
    for level in LEVELS:
        if not commands[level]:
            commands[level] = fallback
    return commands


def task_risk_signals(task: dict) -> list[str]:
    return dedupe(str(item).strip() for item in task.get("risk_signals", []) if str(item).strip())


def is_high_risk(task: dict, profile: dict) -> bool:
    if str(task.get("risk_level", "")).strip() == "high":
        return True
    if task_risk_signals(task):
        return True
    if any(
        bool(task.get(flag))
        for flag in [
            "allow_dependency_changes",
            "allow_migration",
            "allow_ci_changes",
            "allow_deploy_changes",
            "allow_cross_layer_refactor",
        ]
    ):
        return True

    allowed_paths = [str(item).strip() for item in task.get("allowed_paths", []) if str(item).strip()]
    high_risk_paths = [str(item).strip() for item in profile.get("high_risk_paths", []) if str(item).strip()]
    if high_risk_paths and any(path.startswith(tuple(high_risk_paths)) for path in allowed_paths):
        return True
    return False


def choose_level(task: dict, profile: dict) -> tuple[str, str]:
    task_type = str(task.get("type", "build")).strip() or "build"
    task_kind = str(task.get("task_kind", "feature")).strip() or "feature"
    allowed_paths = [str(item).strip() for item in task.get("allowed_paths", []) if str(item).strip()]
    risk_signals = task_risk_signals(task)

    if risk_signals:
        return "extended", f"change-risk signals detected: {', '.join(risk_signals)}"

    if is_high_risk(task, profile):
        return "extended", "high risk flags or repo profile risk path detected"

    if task_type == "verify":
        if task_kind in {"feature", "infra"}:
            return "repo_default", "verify for feature/infra defaults to repo baseline validation"
        if allowed_paths:
            return "targeted", "verify scope is narrow so targeted validation is preferred"
        return "repo_default", "verify falls back to repo default validation"

    if task_type == "build":
        if task_kind in {"bugfix", "refactor"} and allowed_paths:
            return "targeted", "build scope is limited so targeted validation is sufficient"
        if task_kind in {"feature", "infra"}:
            return "repo_default", "feature/infra build should run repo default validation"
        return "smoke", "defaulting to smoke validation for low-signal build tasks"

    return "repo_default", "default repo validation"


def apply_test_strategy(task: dict, profile: dict | None = None) -> dict:
    profile = normalize_repo_profile(task.get("repo", "repo-main"), profile or task.get("repo_profile") or load_repo_profile(task.get("repo", "repo-main")))
    commands_by_level = strategy_commands(profile)
    level, reason = choose_level(task, profile)
    commands = commands_by_level[level] or commands_by_level["repo_default"]
    task["test_strategy_level"] = level
    task["test_strategy_reason"] = reason
    task["test_strategy_commands"] = commands
    task["test_strategy_summary"] = "\n".join(
        [
            f"- level: {level}",
            f"- reason: {reason}",
            f"- commands: {', '.join(commands) if commands else '(none)'}",
        ]
    )
    return task


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "apply":
        print("usage: test_strategy.py apply <task.json> [--in-place]", file=sys.stderr)
        return 1

    path = Path(argv[2])
    task = json.loads(path.read_text(encoding="utf-8"))
    task = apply_test_strategy(task)
    if "--in-place" in argv[3:]:
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(task, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
