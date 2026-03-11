from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

MANAGER_DIR = Path(__file__).resolve().parent
WORKFLOW_ROOT = MANAGER_DIR.parent
REPO_PROFILE_DIR = WORKFLOW_ROOT / "config" / "repos"

DEFAULT_REPO_PROFILE = {
    "stack": "generic",
    "default_branch": "main",
    "install_cmd": "",
    "lint_cmd": "",
    "test_cmd": "",
    "build_cmd": "",
    "smoke_test_cmd": "",
    "targeted_test_cmd": "",
    "extended_test_cmd": "",
    "high_risk_paths": [],
    "forbidden_paths": [".git", ".env", "secrets", "node_modules", "dist", "build"],
    "needs_ui_evidence": False,
}


def _clone(value):
    return deepcopy(value)


def _normalize_string(value, fallback="") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return fallback
    return str(value).strip()


def _normalize_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    return []


def _normalize_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _dedupe(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def repo_profile_path(repo: str) -> Path:
    return REPO_PROFILE_DIR / f"{repo}.json"


def normalize_repo_profile(repo: str, profile: dict | None = None) -> dict:
    merged = _clone(DEFAULT_REPO_PROFILE)
    if isinstance(profile, dict):
        for key, value in profile.items():
            merged[key] = _clone(value)

    normalized = {
        "stack": _normalize_string(merged.get("stack"), "generic") or "generic",
        "default_branch": _normalize_string(merged.get("default_branch"), "main") or "main",
        "install_cmd": _normalize_string(merged.get("install_cmd")),
        "lint_cmd": _normalize_string(merged.get("lint_cmd")),
        "test_cmd": _normalize_string(merged.get("test_cmd")),
        "build_cmd": _normalize_string(merged.get("build_cmd")),
        "smoke_test_cmd": _normalize_string(merged.get("smoke_test_cmd")),
        "targeted_test_cmd": _normalize_string(merged.get("targeted_test_cmd")),
        "extended_test_cmd": _normalize_string(merged.get("extended_test_cmd")),
        "high_risk_paths": _dedupe(_normalize_list(merged.get("high_risk_paths", []))),
        "forbidden_paths": _dedupe(_normalize_list(merged.get("forbidden_paths", []))),
        "needs_ui_evidence": _normalize_bool(merged.get("needs_ui_evidence", False)),
    }
    normalized["repo"] = repo
    return normalized


def load_repo_profile(repo: str) -> dict:
    path = repo_profile_path(repo)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    return normalize_repo_profile(repo, payload)


def load_repo_profiles(repos: list[str]) -> dict[str, dict]:
    return {repo: load_repo_profile(repo) for repo in repos}


def repo_default_branch(profile: dict, remote_branches: list[str]) -> str:
    preferred = _normalize_string(profile.get("default_branch"), "main") or "main"
    if preferred in remote_branches:
        return preferred
    if remote_branches:
        return remote_branches[0]
    return preferred


def order_remote_branches(profile: dict, remote_branches: list[str]) -> list[str]:
    preferred = repo_default_branch(profile, remote_branches)
    ordered = []
    if preferred:
        ordered.append(preferred)
    ordered.extend(branch for branch in remote_branches if branch != preferred)
    return ordered or [preferred]


def repo_profile_summary(profile: dict) -> str:
    lines = [
        f"- stack: {profile.get('stack') or 'generic'}",
        f"- default_branch: {profile.get('default_branch') or 'main'}",
        f"- install_cmd: {profile.get('install_cmd') or '(none)'}",
        f"- lint_cmd: {profile.get('lint_cmd') or '(none)'}",
        f"- test_cmd: {profile.get('test_cmd') or '(none)'}",
        f"- build_cmd: {profile.get('build_cmd') or '(none)'}",
        f"- smoke_test_cmd: {profile.get('smoke_test_cmd') or '(none)'}",
        f"- targeted_test_cmd: {profile.get('targeted_test_cmd') or '(none)'}",
        f"- extended_test_cmd: {profile.get('extended_test_cmd') or '(none)'}",
        f"- high_risk_paths: {', '.join(profile.get('high_risk_paths') or ['(none)'])}",
        f"- forbidden_paths: {', '.join(profile.get('forbidden_paths') or ['(none)'])}",
        f"- needs_ui_evidence: {'true' if profile.get('needs_ui_evidence') else 'false'}",
    ]
    return "\n".join(lines)
