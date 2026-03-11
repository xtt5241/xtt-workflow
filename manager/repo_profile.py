from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

MANAGER_DIR = Path(__file__).resolve().parent
WORKFLOW_ROOT = MANAGER_DIR.parent
REPO_PROFILE_DIR = WORKFLOW_ROOT / "config" / "repos"

DEFAULT_TOOL_ROUTER = {
    "read_first": [],
    "run_first": [],
    "risk_focus": [],
    "evidence_focus": [],
    "execution_order": [],
    "by_type": {},
    "by_task_kind": {},
}

DEFAULT_REPO_PROFILE = {
    "version": 1,
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
    "dependency_files": [],
    "lockfile_files": [],
    "environment_paths": [],
    "environment_files": [],
    "runtime_files": [],
    "package_manager_files": [],
    "forbidden_paths": [".git", ".env", "secrets", "node_modules", "dist", "build"],
    "needs_ui_evidence": False,
    "tool_router": DEFAULT_TOOL_ROUTER,
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


def _normalize_tool_router_layer(value) -> dict:
    payload = value if isinstance(value, dict) else {}
    return {
        "read_first": _dedupe(_normalize_list(payload.get("read_first", []))),
        "run_first": _dedupe(_normalize_list(payload.get("run_first", []))),
        "risk_focus": _dedupe(_normalize_list(payload.get("risk_focus", []))),
        "evidence_focus": _dedupe(_normalize_list(payload.get("evidence_focus", []))),
        "execution_order": _dedupe(_normalize_list(payload.get("execution_order", []))),
    }


def normalize_tool_router_profile(value) -> dict:
    payload = value if isinstance(value, dict) else {}
    normalized = _normalize_tool_router_layer(payload)

    by_type = {}
    raw_by_type = payload.get("by_type", {}) if isinstance(payload.get("by_type"), dict) else {}
    for key, item in raw_by_type.items():
        normalized_key = _normalize_string(key)
        if normalized_key:
            by_type[normalized_key] = _normalize_tool_router_layer(item)

    by_task_kind = {}
    raw_by_task_kind = payload.get("by_task_kind", {}) if isinstance(payload.get("by_task_kind"), dict) else {}
    for key, item in raw_by_task_kind.items():
        normalized_key = _normalize_string(key)
        if normalized_key:
            by_task_kind[normalized_key] = _normalize_tool_router_layer(item)

    normalized["by_type"] = by_type
    normalized["by_task_kind"] = by_task_kind
    return normalized


def repo_profile_path(repo: str) -> Path:
    return REPO_PROFILE_DIR / f"{repo}.json"


def normalize_repo_profile(repo: str, profile: dict | None = None) -> dict:
    merged = _clone(DEFAULT_REPO_PROFILE)
    if isinstance(profile, dict):
        for key, value in profile.items():
            merged[key] = _clone(value)

    normalized = {
        "version": merged.get("version", 1),
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
        "dependency_files": _dedupe(_normalize_list(merged.get("dependency_files", []))),
        "lockfile_files": _dedupe(_normalize_list(merged.get("lockfile_files", []))),
        "environment_paths": _dedupe(_normalize_list(merged.get("environment_paths", []))),
        "environment_files": _dedupe(_normalize_list(merged.get("environment_files", []))),
        "runtime_files": _dedupe(_normalize_list(merged.get("runtime_files", []))),
        "package_manager_files": _dedupe(_normalize_list(merged.get("package_manager_files", []))),
        "forbidden_paths": _dedupe(_normalize_list(merged.get("forbidden_paths", []))),
        "needs_ui_evidence": _normalize_bool(merged.get("needs_ui_evidence", False)),
        "tool_router": normalize_tool_router_profile(merged.get("tool_router", {})),
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
    tool_router = profile.get("tool_router", {}) if isinstance(profile.get("tool_router"), dict) else {}
    configured_router = any(
        tool_router.get(key)
        for key in ("read_first", "run_first", "risk_focus", "evidence_focus", "execution_order")
    ) or bool(tool_router.get("by_type")) or bool(tool_router.get("by_task_kind"))
    lines = [
        f"- version: {profile.get('version') or 1}",
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
        f"- dependency_files: {', '.join(profile.get('dependency_files') or ['(none)'])}",
        f"- lockfile_files: {', '.join(profile.get('lockfile_files') or ['(none)'])}",
        f"- environment_paths: {', '.join(profile.get('environment_paths') or ['(none)'])}",
        f"- environment_files: {', '.join(profile.get('environment_files') or ['(none)'])}",
        f"- runtime_files: {', '.join(profile.get('runtime_files') or ['(none)'])}",
        f"- package_manager_files: {', '.join(profile.get('package_manager_files') or ['(none)'])}",
        f"- forbidden_paths: {', '.join(profile.get('forbidden_paths') or ['(none)'])}",
        f"- needs_ui_evidence: {'true' if profile.get('needs_ui_evidence') else 'false'}",
        f"- tool_router: {'configured' if configured_router else 'heuristic-only'}",
    ]
    return "\n".join(lines)
