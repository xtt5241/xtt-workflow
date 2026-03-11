from __future__ import annotations

from copy import deepcopy
import json
import sys

from repo_profile import load_repo_profile, normalize_repo_profile, repo_profile_summary
from task_schema import ensure_valid_task, load_task_schema, order_task_fields

ROUTER_VERSION = 1
ROUTER_KEYS = ("read_first", "run_first", "risk_focus", "evidence_focus", "execution_order")


def _clone(value):
    return deepcopy(value)


def _normalize_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    return []


def _dedupe(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _merge_lists(*groups) -> list[str]:
    merged = []
    for group in groups:
        merged.extend(_normalize_list(group))
    return _dedupe(merged)


def _router_layer(value) -> dict:
    payload = value if isinstance(value, dict) else {}
    return {key: _normalize_list(payload.get(key, [])) for key in ROUTER_KEYS}


def _is_high_risk(task: dict, profile: dict) -> bool:
    env_summary = str(task.get("env_risk_summary", "")).strip().lower()
    return any(
        [
            str(task.get("risk_level", "")).strip().lower() == "high",
            bool(task.get("allow_dependency_changes")),
            bool(task.get("allow_migration")),
            bool(task.get("allow_ci_changes")),
            bool(task.get("allow_deploy_changes")),
            bool(task.get("allow_cross_layer_refactor")),
            bool(profile.get("needs_ui_evidence")),
            env_summary not in {"", "none detected", "no risk detected"},
        ]
    )


def _allowed_scope_hint(task: dict) -> str | None:
    allowed = _normalize_list(task.get("allowed_paths", []))
    if not allowed:
        return None
    preview = ", ".join(allowed[:5])
    return f"Allowed scope first: {preview}"


def _joined_hint(label: str, values) -> str | None:
    items = _normalize_list(values)
    if not items:
        return None
    return f"{label}: {', '.join(items[:6])}"


def _heuristic_read_first(task: dict, profile: dict) -> list[str]:
    task_type = str(task.get("type", "")).strip()
    base_branch = str(task.get("base_branch", "main")).strip() or "main"
    items: list[str] = []

    if task_type in {"review", "verify"}:
        items.extend(
            [
                f"Inspect real diff stats: git diff --stat origin/{base_branch}...HEAD",
                f"Inspect real diff patch: git diff origin/{base_branch}...HEAD",
            ]
        )
    elif task_type == "plan":
        items.append("Read current implementation entrypoints and nearby modules")
    else:
        items.append("Read task-related implementation files and nearby tests first")

    for hint in (
        _allowed_scope_hint(task),
        _joined_hint("High-risk paths", profile.get("high_risk_paths", [])),
        _joined_hint(
            "Dependency manifests",
            _merge_lists(profile.get("dependency_files", []), profile.get("lockfile_files", [])),
        ) if task.get("allow_dependency_changes") or profile.get("dependency_files") or profile.get("lockfile_files") else None,
        _joined_hint(
            "Runtime / env files",
            _merge_lists(profile.get("environment_paths", []), profile.get("environment_files", []), profile.get("runtime_files", [])),
        ) if profile.get("environment_paths") or profile.get("environment_files") or profile.get("runtime_files") else None,
    ):
        if hint:
            items.append(hint)

    if profile.get("needs_ui_evidence"):
        items.append("Read user-facing templates / routes before coding or verification")

    return _dedupe(items)


def _command_label(label: str, command: str) -> str | None:
    command = str(command or "").strip()
    return f"{label}: {command}" if command else None


def _heuristic_run_first(task: dict, profile: dict) -> list[str]:
    task_type = str(task.get("type", "")).strip()
    high_risk = _is_high_risk(task, profile)
    items: list[str] = []

    if task_type == "build" and str(profile.get("lint_cmd", "")).strip():
        items.append(_command_label("Lint", profile.get("lint_cmd", "")))
    if task_type == "build" and (task.get("allow_dependency_changes") or task.get("allow_migration")):
        install_hint = _command_label("Install", profile.get("install_cmd", ""))
        if install_hint:
            items.append(install_hint)

    if task_type == "verify":
        candidates = [
            _command_label("Targeted verify", profile.get("targeted_test_cmd", "")),
            _command_label("Smoke verify", profile.get("smoke_test_cmd", "")),
            _command_label("Project test", profile.get("test_cmd", "")),
            _command_label("Build", profile.get("build_cmd", "")),
        ]
    elif task_type == "review":
        candidates = [
            _command_label("Optional targeted validation", profile.get("targeted_test_cmd", "")),
            _command_label("Optional smoke validation", profile.get("smoke_test_cmd", "")),
        ]
    elif task_type == "plan":
        candidates = [
            _command_label("Reference targeted validation", profile.get("targeted_test_cmd", "")),
            _command_label("Reference project test", profile.get("test_cmd", "")),
        ]
    else:
        candidates = [
            _command_label("Targeted validation", profile.get("targeted_test_cmd", "")),
            _command_label("Project test", profile.get("test_cmd", "")),
            _command_label("Build", profile.get("build_cmd", "")),
            _command_label("Smoke test", profile.get("smoke_test_cmd", "")),
        ]

    items.extend([item for item in candidates if item])

    if high_risk:
        extra = _command_label("Escalated validation", profile.get("extended_test_cmd", ""))
        if extra:
            items.append(extra)

    return _dedupe(items)


def _heuristic_risk_focus(task: dict, profile: dict) -> list[str]:
    items: list[str] = []
    for hint in (
        _joined_hint("Watch high-risk paths", profile.get("high_risk_paths", [])),
        _joined_hint(
            "Watch dependency / lockfile drift",
            _merge_lists(profile.get("dependency_files", []), profile.get("lockfile_files", [])),
        ) if profile.get("dependency_files") or profile.get("lockfile_files") else None,
        _joined_hint(
            "Watch runtime / env drift",
            _merge_lists(profile.get("environment_paths", []), profile.get("environment_files", []), profile.get("runtime_files", [])),
        ) if profile.get("environment_paths") or profile.get("environment_files") or profile.get("runtime_files") else None,
        _joined_hint("Forbidden paths must stay untouched", task.get("forbidden_paths", [])),
    ):
        if hint:
            items.append(hint)

    if task.get("allow_migration") is False:
        items.append("Migration changes still require explicit human review")
    if task.get("allow_ci_changes") is False:
        items.append("CI changes remain out of scope unless task explicitly allows them")
    if task.get("allow_deploy_changes") is False:
        items.append("Deploy changes remain out of scope unless task explicitly allows them")
    if task.get("allow_cross_layer_refactor") is False:
        items.append("Cross-layer refactors should be avoided unless the task requires them")
    if profile.get("needs_ui_evidence"):
        items.append("UI regressions need manual evidence, not only code review")

    return _dedupe(items)


def _heuristic_evidence_focus(task: dict, profile: dict) -> list[str]:
    task_type = str(task.get("type", "")).strip()
    base_branch = str(task.get("base_branch", "main")).strip() or "main"
    items = _normalize_list(task.get("evidence_required", []))

    if task_type in {"review", "verify"}:
        items.append(f"Real diff evidence against origin/{base_branch}...HEAD")
    if task_type == "build":
        items.append("Structured validation commands and outcomes")
    if task_type == "verify":
        items.append("Explicit merge decision and residual risks")
    if profile.get("needs_ui_evidence"):
        items.append("UI evidence or reproducible manual interaction steps")
    if profile.get("high_risk_paths"):
        items.append("Call out touches under high-risk paths")
    if task.get("allow_dependency_changes") or profile.get("dependency_files") or profile.get("lockfile_files"):
        items.append("Dependency / lockfile diff evidence")
    if profile.get("environment_paths") or profile.get("environment_files") or profile.get("runtime_files"):
        items.append("Runtime / environment validation evidence")

    return _dedupe(items)


def _heuristic_execution_order(task: dict, profile: dict) -> list[str]:
    task_type = str(task.get("type", "")).strip()
    high_risk = _is_high_risk(task, profile)

    if task_type == "review":
        steps = [
            "inspect-real-diff",
            "read-risk-files-and-dod",
            "check-boundaries-and-tests",
            "report-findings-only",
        ]
    elif task_type == "verify":
        steps = [
            "inspect-real-diff",
            "run-targeted-or-smoke-validation",
            "verify-dod-and-delivery-readiness",
            "write-merge-decision-and-residual-risks",
        ]
    elif task_type == "plan":
        steps = [
            "read-current-implementation",
            "map-affected-files",
            "propose-minimal-steps",
            "define-test-plan-and-risks",
        ]
    else:
        steps = [
            "read-scope-and-nearby-tests",
            "inspect-risk-files",
            "implement-minimal-change",
            "run-targeted-validation",
            "collect-structured-evidence",
        ]

    if high_risk and "escalate-high-risk-checks" not in steps:
        insert_at = 2 if len(steps) > 2 else len(steps)
        steps.insert(insert_at, "escalate-high-risk-checks")
    if profile.get("needs_ui_evidence") and "capture-ui-evidence" not in steps:
        steps.append("capture-ui-evidence")
    return _dedupe(steps)


def _router_reasons(task: dict, profile: dict) -> list[str]:
    reasons = [
        f"task_type={str(task.get('type', '')).strip() or 'unknown'}",
        f"task_kind={str(task.get('task_kind', '')).strip() or 'unknown'}",
        f"stack={str(profile.get('stack', '')).strip() or 'generic'}",
    ]
    if profile.get("needs_ui_evidence"):
        reasons.append("repo profile requires UI evidence")
    if profile.get("high_risk_paths"):
        reasons.append("repo profile defines high-risk paths")
    if task.get("allow_dependency_changes"):
        reasons.append("task allows dependency changes")
    if task.get("allow_migration") or task.get("allow_ci_changes") or task.get("allow_deploy_changes"):
        reasons.append("task touches operational risk areas")
    return _dedupe(reasons)


def build_router(task: dict, profile: dict) -> dict:
    task_type = str(task.get("type", "")).strip()
    task_kind = str(task.get("task_kind", "")).strip()
    router_profile = profile.get("tool_router", {}) if isinstance(profile.get("tool_router"), dict) else {}
    default_layer = _router_layer(router_profile)
    type_layer = _router_layer(router_profile.get("by_type", {}).get(task_type, {}))
    kind_layer = _router_layer(router_profile.get("by_task_kind", {}).get(task_kind, {}))

    read_first = _merge_lists(default_layer.get("read_first"), type_layer.get("read_first"), kind_layer.get("read_first"), _heuristic_read_first(task, profile))
    run_first = _merge_lists(default_layer.get("run_first"), type_layer.get("run_first"), kind_layer.get("run_first"), _heuristic_run_first(task, profile))
    risk_focus = _merge_lists(default_layer.get("risk_focus"), type_layer.get("risk_focus"), kind_layer.get("risk_focus"), _heuristic_risk_focus(task, profile))
    evidence_focus = _merge_lists(default_layer.get("evidence_focus"), type_layer.get("evidence_focus"), kind_layer.get("evidence_focus"), _heuristic_evidence_focus(task, profile))
    execution_order = _merge_lists(default_layer.get("execution_order"), type_layer.get("execution_order"), kind_layer.get("execution_order"), _heuristic_execution_order(task, profile))

    return {
        "version": ROUTER_VERSION,
        "route_key": f"{task_type or 'unknown'}:{task_kind or 'unknown'}:{profile.get('stack') or 'generic'}",
        "task_type": task_type,
        "task_kind": task_kind,
        "repo": str(task.get("repo", "")).strip(),
        "profile_stack": str(profile.get("stack", "")).strip() or "generic",
        "read_first": read_first,
        "run_first": run_first,
        "risk_focus": risk_focus,
        "evidence_focus": evidence_focus,
        "execution_order": execution_order,
        "reasons": _router_reasons(task, profile),
    }


def build_router_summary(router: dict) -> str:
    sections = [
        ("route_key", [str(router.get("route_key", "")).strip()]),
        ("execution_order", router.get("execution_order", [])),
        ("read_first", router.get("read_first", [])),
        ("run_first", router.get("run_first", [])),
        ("risk_focus", router.get("risk_focus", [])),
        ("evidence_focus", router.get("evidence_focus", [])),
        ("reasons", router.get("reasons", [])),
    ]
    lines = []
    for label, items in sections:
        normalized_items = [str(item).strip() for item in items if str(item).strip()]
        if not normalized_items:
            continue
        lines.append(f"- {label}:")
        lines.extend([f"  - {item}" for item in normalized_items])
    return "\n".join(lines)


def apply_tool_router(task: dict, profile: dict | None = None) -> dict:
    base_task = ensure_valid_task(task)
    repo = str(base_task.get("repo", "")).strip()
    profile_source = profile if isinstance(profile, dict) else (base_task.get("repo_profile") if isinstance(base_task.get("repo_profile"), dict) else None)
    resolved_profile = normalize_repo_profile(repo, profile_source) if profile_source else load_repo_profile(repo)

    enriched = _clone(base_task)
    enriched["repo_profile"] = resolved_profile
    enriched["repo_profile_summary"] = repo_profile_summary(resolved_profile)

    router = build_router(enriched, resolved_profile)
    enriched["tool_router"] = router
    enriched["tool_router_summary"] = build_router_summary(router)
    enriched["evidence_required"] = _merge_lists(enriched.get("evidence_required", []), router.get("evidence_focus", []))

    return order_task_fields(enriched, load_task_schema())


def _read_task(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_task(path: str, task: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(task, handle, ensure_ascii=False, indent=2)


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "apply":
        print("usage: tool_router.py apply <task.json> [--in-place]", file=sys.stderr)
        return 2

    task_path = argv[2]
    task = apply_tool_router(_read_task(task_path))
    if len(argv) > 3 and argv[3] == "--in-place":
        _write_task(task_path, task)
    else:
        print(json.dumps(task, ensure_ascii=False, indent=2))
    print(f"tool router applied: {task.get('id')} -> {task.get('tool_router', {}).get('route_key', '')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
