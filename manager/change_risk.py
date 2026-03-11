from __future__ import annotations

from pathlib import Path
import json
import sys

from repo_profile import load_repo_profile, normalize_repo_profile
from task_boundary import DEPENDENCY_FILES, MIGRATION_PREFIXES, path_matches_prefix, staged_paths

LOCKFILE_FILES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
}
ENV_PATH_PREFIXES = ["env/", "config/env/", "config/runtime/"]
ENV_PATH_FILES = {
    ".env.example",
    ".env.sample",
    ".env.local.example",
    "env.example",
    "env.sample",
    "sample.env",
    "example.env",
}
RUNTIME_FILES = {
    ".python-version",
    ".nvmrc",
    ".node-version",
    ".ruby-version",
    ".tool-versions",
    "runtime.txt",
}
PACKAGE_MANAGER_FILES = {
    "package.json",
    "pnpm-workspace.yaml",
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnpmfile.cjs",
    "poetry.toml",
}
MANUAL_REVIEW_CATEGORIES = {"environment", "runtime", "package_manager"}


def dedupe(items) -> list[str]:
    result = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def read_task(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validation_commands(categories: list[str], profile: dict) -> list[str]:
    commands = []
    if any(category in categories for category in {"dependency", "lockfile", "package_manager"}):
        commands.extend(
            [
                profile.get("install_cmd", ""),
                profile.get("lint_cmd", ""),
                profile.get("test_cmd", ""),
                profile.get("build_cmd", ""),
            ]
        )
    if any(category in categories for category in {"environment", "runtime", "migration"}):
        commands.extend(
            [
                profile.get("smoke_test_cmd", ""),
                profile.get("test_cmd", ""),
                profile.get("build_cmd", ""),
            ]
        )
    return dedupe(commands)


def detect_change_risk(task: dict, repo_path: Path) -> dict:
    repo = str(task.get("repo", "repo-main")).strip() or "repo-main"
    profile = normalize_repo_profile(repo, task.get("repo_profile") or load_repo_profile(repo))
    changed = staged_paths(repo_path)
    if not changed:
        return {}

    dependency_files = {str(item).strip() for item in DEPENDENCY_FILES}
    dependency_files.update(str(item).strip() for item in profile.get("dependency_files", []) if str(item).strip())
    lockfile_files = set(LOCKFILE_FILES)
    lockfile_files.update(str(item).strip() for item in profile.get("lockfile_files", []) if str(item).strip())
    environment_prefixes = dedupe(ENV_PATH_PREFIXES + list(profile.get("environment_paths", []) or []))
    environment_files = set(ENV_PATH_FILES)
    environment_files.update(str(item).strip() for item in profile.get("environment_files", []) if str(item).strip())
    runtime_files = set(RUNTIME_FILES)
    runtime_files.update(str(item).strip() for item in profile.get("runtime_files", []) if str(item).strip())
    package_manager_files = set(PACKAGE_MANAGER_FILES)
    package_manager_files.update(str(item).strip() for item in profile.get("package_manager_files", []) if str(item).strip())

    signals = []

    dependency_hits = [path for path in changed if Path(path).name in dependency_files]
    if dependency_hits:
        signals.append({"category": "dependency", "items": dependency_hits, "requires_human": False})

    lockfile_hits = [path for path in changed if Path(path).name in lockfile_files]
    if lockfile_hits:
        signals.append({"category": "lockfile", "items": lockfile_hits, "requires_human": False})

    migration_hits = [path for path in changed if path_matches_prefix(path, MIGRATION_PREFIXES)]
    if migration_hits:
        signals.append({"category": "migration", "items": migration_hits, "requires_human": False})

    environment_hits = [
        path
        for path in changed
        if path_matches_prefix(path, environment_prefixes) or Path(path).name in environment_files
    ]
    if environment_hits:
        signals.append({"category": "environment", "items": environment_hits, "requires_human": True})

    runtime_hits = [path for path in changed if Path(path).name in runtime_files]
    if runtime_hits:
        signals.append({"category": "runtime", "items": runtime_hits, "requires_human": True})

    package_manager_hits = [path for path in changed if Path(path).name in package_manager_files]
    if package_manager_hits:
        signals.append({"category": "package_manager", "items": package_manager_hits, "requires_human": True})

    if not signals:
        return {}

    categories = [item["category"] for item in signals]
    requires_human = any(item["category"] in MANUAL_REVIEW_CATEGORIES for item in signals)
    tail = "requires human review before the pipeline continues" if requires_human else "requires extended validation before delivery"
    summary = f"change-risk detected: {', '.join(categories)}; {tail}"
    required_evidence = ["changed files", "summary", "verification", "risks", "rollback note"]
    if requires_human:
        required_evidence.append("manual steps")

    return {
        "report_type": "change-risk",
        "summary": summary,
        "risk_level": "high",
        "categories": categories,
        "signals": signals,
        "changed_files": changed,
        "requires_human": requires_human,
        "recommended_gate": "change-risk" if requires_human else "",
        "recommended_test_strategy": "extended",
        "validation_commands": validation_commands(categories, profile),
        "required_evidence": dedupe(required_evidence),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] != "check-staged":
        print("usage: change_risk.py check-staged <task.json> <repo_path> <report_path>", file=sys.stderr)
        return 2

    task_path = Path(argv[2])
    repo_path = Path(argv[3]).resolve()
    report_path = Path(argv[4])
    task = read_task(task_path)
    report = detect_change_risk(task, repo_path)
    if not report:
        report_path.unlink(missing_ok=True)
        print("change risk: no elevated signals detected")
        return 0

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 3 if report.get("requires_human") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
