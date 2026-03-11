from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import sys

from dod import dod_summary, normalize_task_kind

MANAGER_DIR = Path(__file__).resolve().parent
WORKFLOW_ROOT = MANAGER_DIR.parent
SCHEMA_PATH = WORKFLOW_ROOT / "config" / "task_schema.json"


def load_task_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _clone(value):
    return deepcopy(value)


def _split_text_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        lines = []
        for line in value.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            lines.append(cleaned)
        if lines:
            return lines
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    return []


def _normalize_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return value


def _normalize_int(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _normalize_change_budget(value, default_value: dict) -> dict:
    if isinstance(value, dict):
        budget = _clone(value)
    elif isinstance(value, str) and value.strip():
        try:
            budget = json.loads(value)
        except json.JSONDecodeError:
            budget = _clone(default_value)
    else:
        budget = _clone(default_value)

    for key, fallback in default_value.items():
        budget.setdefault(key, fallback)
        budget[key] = _normalize_int(budget[key])
    return budget


def _default_goal(task: dict) -> str:
    task_type = task.get("type", "")
    title = str(task.get("title", "")).strip()
    base_branch = str(task.get("base_branch", "main")).strip() or "main"
    source_ref = str(task.get("source_ref", "")).strip()
    if task_type == "review":
        return f"Review {source_ref or title} against origin/{base_branch}"
    if task_type == "verify":
        return f"Verify {source_ref or title} against origin/{base_branch}"
    if task_type == "plan":
        return f"Plan task: {title}"
    return title


def _default_acceptance(task: dict) -> list[str]:
    task_type = task.get("type", "")
    title = str(task.get("title", "")).strip()
    base_branch = str(task.get("base_branch", "main")).strip() or "main"
    source_ref = str(task.get("source_ref", "")).strip()
    if task_type == "review":
        return [
            f"Inspect the real diff against origin/{base_branch}...HEAD",
            f"Review source ref: {source_ref or title}",
            "Only output issues, do not fix code",
        ]
    if task_type == "verify":
        return [
            f"Verify the real diff against origin/{base_branch}...HEAD",
            f"Validate source ref: {source_ref or title}",
            "State whether the change is really usable",
        ]
    if task_type == "plan":
        return [
            "Read the relevant code first",
            "Output the minimal implementation plan only",
        ]
    return [
        f"Complete the task goal: {title}",
        "Provide concrete verification output",
    ]


def _stage_running_state(task_type: str) -> str:
    return {
        "build": "building",
        "review": "reviewing",
        "verify": "verifying",
    }.get(task_type, "routed")


def _stage_done_state(task_type: str) -> str:
    return {
        "build": "build-done",
        "review": "review-done",
        "verify": "verify-done",
    }.get(task_type, "delivered")


def _stage_failed_state(task_type: str) -> str:
    return {
        "build": "failed-build",
        "review": "failed-review",
        "verify": "failed-verify",
    }.get(task_type, "failed-postprocess")


def _default_lifecycle_state(task: dict) -> str:
    task_type = str(task.get("type", "")).strip()
    status = str(task.get("status", "pending")).strip() or "pending"

    if status == "running":
        return _stage_running_state(task_type)
    if status == "done":
        return _stage_done_state(task_type)
    if status == "failed":
        if str(task.get("watchdog_reason", "")).strip() or str(task.get("failure_reason", "")).startswith("watchdog:"):
            return "failed-watchdog"
        return _stage_failed_state(task_type)
    if status == "needs-human":
        if str(task.get("watchdog_reason", "")).strip():
            return "failed-watchdog"
        return _stage_done_state(task_type)
    if status == "ready-to-pr":
        return "ready-to-pr"
    if status == "ready-to-push":
        return "ready-to-push"
    if status == "ready-to-release":
        return "ready-to-release"
    if status == "delivered":
        return "delivered"
    if task_type in {"build", "review", "verify"}:
        return "routed"
    return "queued"


def order_task_fields(task: dict, schema: dict | None = None) -> dict:
    schema = schema or load_task_schema()
    ordered = {}
    for key in schema.get("field_order", []):
        if key in task:
            ordered[key] = task[key]
    for key in sorted(task.keys()):
        if key not in ordered:
            ordered[key] = task[key]
    return ordered


def normalize_task(task: dict, schema: dict | None = None) -> dict:
    schema = schema or load_task_schema()
    normalized = _clone(task)
    task_type = str(normalized.get("type", "")).strip()

    for key, value in schema["defaults"]["common"].items():
        normalized.setdefault(key, _clone(value))
    for key, value in schema["defaults"]["by_type"].get(task_type, {}).items():
        normalized.setdefault(key, _clone(value))

    if task_type and not normalized.get("role"):
        normalized["role"] = schema["role_by_type"].get(task_type, "")
    if task_type and not normalized.get("prompt_file"):
        normalized["prompt_file"] = schema["prompt_file_by_type"].get(task_type, "")
    if task_type in {"review", "verify"} and not str(normalized.get("source_ref", "")).strip() and str(normalized.get("branch", "")).strip():
        normalized["source_ref"] = str(normalized.get("branch", "")).strip()
    normalized["task_kind"] = normalize_task_kind(normalized.get("task_kind"))
    if not str(normalized.get("lifecycle_state", "")).strip():
        normalized["lifecycle_state"] = _default_lifecycle_state(normalized)
    if not str(normalized.get("dod_summary", "")).strip():
        normalized["dod_summary"] = dod_summary(normalized["task_kind"])
    if not str(normalized.get("goal", "")).strip():
        normalized["goal"] = _default_goal(normalized)
    if not _split_text_list(normalized.get("acceptance", [])):
        normalized["acceptance"] = _default_acceptance(normalized)

    for field in ("acceptance", "evidence_required", "allowed_paths", "forbidden_paths", "depends_on", "risk_signals"):
        normalized[field] = _split_text_list(normalized.get(field, []))

    for field in ("allow_auto_commit", "allow_push", "allow_pr", "allow_dependency_changes", "allow_migration", "allow_ci_changes", "allow_deploy_changes", "allow_cross_layer_refactor"):
        normalized[field] = _normalize_bool(normalized.get(field))

    normalized["retry_count"] = _normalize_int(normalized.get("retry_count", 0))
    normalized["change_budget"] = _normalize_change_budget(
        normalized.get("change_budget"),
        schema["defaults"]["by_type"].get(task_type, {}).get("change_budget", {"max_files": 0, "max_lines": 0}),
    )

    return order_task_fields(normalized, schema)


def validate_task(task: dict, schema: dict | None = None) -> list[str]:
    schema = schema or load_task_schema()
    errors: list[str] = []
    task_type = task.get("type")

    required_fields = list(schema["required"]["all"])
    required_fields.extend(schema["required"].get(task_type, []))
    for field in required_fields:
        if field not in task:
            errors.append(f"missing field: {field}")

    for field, type_name in schema["types"].items():
        if field not in task:
            continue
        value = task[field]
        if type_name == "string":
            if not isinstance(value, str) or not value.strip():
                errors.append(f"field {field} must be a non-empty string")
        elif type_name == "string_array":
            if not isinstance(value, list):
                errors.append(f"field {field} must be an array of strings")
            elif any(not isinstance(item, str) or not item.strip() for item in value):
                errors.append(f"field {field} must contain only non-empty strings")
        elif type_name == "boolean":
            if not isinstance(value, bool):
                errors.append(f"field {field} must be boolean")
        elif type_name == "integer":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"field {field} must be a non-negative integer")
        elif type_name == "object":
            if not isinstance(value, dict):
                errors.append(f"field {field} must be an object")

    for field, allowed in schema.get("enums", {}).items():
        if field in task and task[field] not in allowed:
            errors.append(f"field {field} must be one of: {', '.join(allowed)}")

    change_budget = task.get("change_budget", {})
    if isinstance(change_budget, dict):
        for key in ("max_files", "max_lines"):
            value = change_budget.get(key)
            if not isinstance(value, int) or value < 0:
                errors.append(f"change_budget.{key} must be a non-negative integer")

    expected_role = schema["role_by_type"].get(task_type)
    if expected_role and task.get("role") != expected_role:
        errors.append(f"type {task_type} must use role {expected_role}")

    if task_type in {"review", "verify"} and not str(task.get("source_ref", "")).strip():
        errors.append(f"type {task_type} requires source_ref")

    if task_type != "build" and task.get("allow_auto_commit") is True:
        errors.append(f"type {task_type} cannot enable allow_auto_commit")

    return errors


def ensure_valid_task(task: dict, schema: dict | None = None) -> dict:
    schema = schema or load_task_schema()
    normalized = normalize_task(task, schema)
    errors = validate_task(normalized, schema)
    if errors:
        raise ValueError("task schema validation failed: " + "; ".join(errors))
    return normalized


def format_prompt_value(value) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value) if value else "- (none)"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def render_prompt(task: dict, template_text: str) -> str:
    rendered = template_text
    for key, value in task.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", format_prompt_value(value))
    return rendered


def _read_task(path_arg: str) -> dict:
    return json.loads(Path(path_arg).read_text(encoding="utf-8"))


def _write_task(path_arg: str, task: dict) -> None:
    Path(path_arg).write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: task_schema.py <validate|normalize|render-prompt> ...", file=sys.stderr)
        return 2

    command = argv[1]
    if command == "validate":
        task = ensure_valid_task(_read_task(argv[2]))
        print(f"task schema ok: {task['id']}")
        return 0

    if command == "normalize":
        task = ensure_valid_task(_read_task(argv[2]))
        if len(argv) > 3 and argv[3] == "--in-place":
            _write_task(argv[2], task)
        else:
            print(json.dumps(task, ensure_ascii=False, indent=2))
        return 0

    if command == "render-prompt":
        if len(argv) != 5:
            print("usage: task_schema.py render-prompt <task.json> <template.md> <output.md>", file=sys.stderr)
            return 2
        task = ensure_valid_task(_read_task(argv[2]))
        template_text = Path(argv[3]).read_text(encoding="utf-8")
        Path(argv[4]).write_text(render_prompt(task, template_text), encoding="utf-8")
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
