from __future__ import annotations

from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import time

MANAGER_DIR = Path(__file__).resolve().parent
WORKFLOW_ROOT = MANAGER_DIR.parent
RESULTS_DIR = MANAGER_DIR / "results"
LOGS_DIR = MANAGER_DIR / "logs"
PROMPTS_DIR = MANAGER_DIR / "prompts"
REPO_CONFIG_DIR = WORKFLOW_ROOT / "config" / "repos"
TASK_SCHEMA_PATH = WORKFLOW_ROOT / "config" / "task_schema.json"
QUEUE_NAMES = {"pending", "running", "needs-human", "ready-to-pr", "ready-to-push", "done", "failed"}
TEST_COMMAND_RE = re.compile(
    r"(pytest|unittest|vitest|jest|playwright|cypress|go test|cargo test|npm(?:\s+run)?\s+(?:test|build)|"
    r"pnpm(?:\s+run)?\s+(?:test|build)|yarn\s+(?:test|build)|make\s+(?:test|build)|"
    r"python\s+-m\s+pytest|uv\s+run\s+.*pytest|ruff|mypy|tsc|smoke)",
    re.IGNORECASE,
)
LINT_COMMAND_RE = re.compile(r"(ruff|flake8|eslint|stylelint|mypy|tsc|lint)", re.IGNORECASE)
BUILD_COMMAND_RE = re.compile(r"(npm(?:\s+run)?\s+build|pnpm(?:\s+run)?\s+build|yarn\s+build|make\s+build|cargo\s+build|go\s+build|build)", re.IGNORECASE)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dedupe_list(items) -> list:
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def file_version(path: Path, extra: dict | None = None) -> dict:
    payload = extra.copy() if extra else {}
    payload["path"] = str(path)
    if not path.exists():
        payload["exists"] = False
        return payload

    stat = path.stat()
    raw = path.read_bytes()
    payload.update(
        {
            "exists": True,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            "sha256": sha256_bytes(raw),
        }
    )
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict) and "version" in parsed:
        payload["version"] = parsed["version"]
    return payload


def normalize_heading(text: str) -> str:
    return re.sub(r"[\s`*_#：:（）()\-]+", "", text.strip()).lower()


def detect_heading(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if re.fullmatch(r"\*\*[^*\n]+\*\*", stripped):
        return stripped[2:-2].strip()
    if re.fullmatch(r"#{1,6}\s+.+", stripped):
        return re.sub(r"^#{1,6}\s+", "", stripped).strip()
    if stripped[0] not in "-*`" and len(stripped) <= 32 and stripped.endswith((":", "：")):
        return stripped[:-1].strip()
    return ""


def parse_sections(text: str) -> list[dict]:
    sections = []
    current = None
    for line in text.splitlines():
        heading = detect_heading(line)
        if heading:
            current = {"name": normalize_heading(heading), "raw_name": heading, "lines": []}
            sections.append(current)
            continue
        if current is not None:
            current["lines"].append(line)
    return sections


def section_items(sections: list[dict], aliases: list[str]) -> list[str]:
    normalized = {normalize_heading(alias) for alias in aliases}
    for section in reversed(sections):
        if section["name"] not in normalized:
            continue
        items = []
        for line in section["lines"]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("- "):
                items.append(stripped[2:].strip())
                continue
            numbered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
            if numbered:
                items.append(numbered.group(1).strip())
                continue
            if not stripped.startswith("`"):
                items.append(stripped)
        return dedupe_list(items)
    return []


def last_summary_text(sections: list[dict]) -> str:
    for aliases in (["结论", "summary"], ["是否可合并", "merge readiness", "mergeability"], ["成功路径", "verification"]):
        items = section_items(sections, aliases)
        if items:
            return items[0]
    return ""


def parse_exec_records(log_text: str) -> list[dict]:
    lines = log_text.splitlines()
    records = []
    for index, line in enumerate(lines[:-1]):
        if line.strip() != "exec":
            continue
        next_line = lines[index + 1].strip()
        match = re.match(
            r"(?P<command>.+?) in (?P<cwd>/.*?) (?P<status>succeeded|exited\s+\d+) in (?P<duration>[0-9.]+(?:ms|s)):?$",
            next_line,
        )
        if not match:
            continue
        raw_status = match.group("status")
        exit_code = 0 if raw_status == "succeeded" else int(raw_status.split()[-1])
        records.append(
            {
                "command": match.group("command").strip(),
                "cwd": match.group("cwd").strip(),
                "duration": match.group("duration"),
                "exit_code": exit_code,
                "status": "passed" if exit_code == 0 else "failed",
            }
        )
    return records


def extract_test_results(log_text: str, existing: list | None = None) -> list[dict]:
    results = []
    seen = set()
    for record in parse_exec_records(log_text):
        if not TEST_COMMAND_RE.search(record["command"]):
            continue
        key = (record["command"], record["status"], record["exit_code"])
        if key in seen:
            continue
        seen.add(key)
        results.append(record)
    return results or (existing or [])


def run_git(cwd: Path, args: list[str]) -> list[str]:
    if not cwd.exists() or not (cwd / ".git").exists():
        return []
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def parse_changed_files_from_log(log_text: str) -> list[str]:
    match = re.search(r"--- git diff names vs origin/[^\n]+ ---\n(?P<body>.*?)(?:\n\n|\Z)", log_text, re.DOTALL)
    if not match:
        return []
    return dedupe_list([line.strip() for line in match.group("body").splitlines() if line.strip()])


def parse_uncommitted_files(status_lines: list[str]) -> list[str]:
    paths = []
    for line in status_lines:
        if len(line) < 4:
            continue
        candidate = line[3:].strip()
        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1].strip()
        if candidate:
            paths.append(candidate)
    return dedupe_list(paths)


def extract_changed_files(task: dict, log_text: str, existing: list | None = None) -> list[str]:
    worktree_path = WORKFLOW_ROOT / "workspace" / str(task.get("worktree", "")).strip()
    base_branch = str(task.get("base_branch", "")).strip()
    changed_files = []
    if base_branch:
        changed_files = run_git(worktree_path, ["diff", "--name-only", f"origin/{base_branch}...HEAD"])
    if not changed_files:
        changed_files = parse_uncommitted_files(run_git(worktree_path, ["status", "--porcelain"]))
    if not changed_files:
        changed_files = parse_changed_files_from_log(log_text)
    return changed_files or (existing or [])


def extract_review_findings(task: dict, sections: list[dict], existing: list | None = None) -> list[str]:
    findings = section_items(sections, ["发现的问题", "review findings", "issues", "问题", "review notes"]) 
    if not findings and task.get("type") == "review":
        findings = section_items(sections, ["结论"])
    return findings or (existing or [])


def extract_missing_tests(sections: list[dict], existing: list | None = None) -> list[str]:
    values = section_items(sections, ["missing tests", "missing_tests", "缺失测试"])
    return values or (existing or [])


def extract_rule_conflicts(sections: list[dict], existing: list | None = None) -> list[str]:
    values = section_items(sections, ["rule conflicts", "rule_conflicts", "规则冲突", "与agents冲突"])
    return values or (existing or [])


def extract_pass_paths(sections: list[dict], existing: list | None = None) -> list[str]:
    values = section_items(sections, ["成功路径", "pass paths", "pass_paths"])
    return values or (existing or [])


def extract_fail_paths(sections: list[dict], existing: list | None = None) -> list[str]:
    values = section_items(sections, ["失败路径", "fail paths", "fail_paths"])
    return values or (existing or [])


def extract_severity(findings: list[str], sections: list[dict], existing: str | None = None) -> str:
    values = section_items(sections, ["severity", "严重级别"])
    if values:
        return values[0]
    combined = " ".join(findings).lower()
    if any(token in combined for token in ["critical", "严重", "high", "高危"]):
        return "high"
    if findings:
        return "medium"
    return existing or "none"


def extract_residual_risks(task: dict, sections: list[dict], existing: list | None = None) -> list[str]:
    risks = section_items(sections, ["剩余风险", "risks", "风险"])
    human_reason = str(task.get("human_reason", "")).strip()
    if human_reason and human_reason not in risks:
        risks.append(human_reason)
    dod_report = task.get("dod_report", {}) if isinstance(task.get("dod_report"), dict) else {}
    for section in dod_report.get("missing_sections", []):
        message = f"missing DoD section: {section}"
        if message not in risks:
            risks.append(message)
    return risks or (existing or [])


def infer_queue_name(task_path: Path, task: dict) -> str:
    queue_name = task_path.parent.name if task_path.parent.name in QUEUE_NAMES else ""
    task_status = str(task.get("status", "")).strip()
    if queue_name == "running" and task_status and task_status != "running":
        return task_status
    return queue_name or task_status or "unknown"


def effective_status(task: dict, queue_name: str) -> str:
    status = str(task.get("status", "")).strip()
    return queue_name if queue_name in QUEUE_NAMES else (status or "pending")


def effective_lifecycle_state(task: dict, queue_name: str) -> str:
    lifecycle_state = str(task.get("lifecycle_state", "")).strip()
    task_type = str(task.get("type", "")).strip()
    if lifecycle_state and lifecycle_state not in {"queued", "routed"}:
        return lifecycle_state
    if queue_name == "pending":
        return "routed"
    if queue_name == "running":
        return {"build": "building", "review": "reviewing", "verify": "verifying"}.get(task_type, "routed")
    if queue_name == "done":
        return {"build": "build-done", "review": "review-done", "verify": "verify-done"}.get(task_type, "delivered")
    if queue_name == "needs-human":
        return "failed-watchdog" if str(task.get("watchdog_reason", "")).strip() else {"build": "build-done", "review": "review-done", "verify": "verify-done"}.get(task_type, "delivered")
    if queue_name in {"ready-to-pr", "ready-to-push", "ready-to-release", "delivered"}:
        return queue_name
    if queue_name == "failed":
        if str(task.get("watchdog_reason", "")).strip() or str(task.get("failure_reason", "")).startswith("watchdog:"):
            return "failed-watchdog"
        return {"build": "failed-build", "review": "failed-review", "verify": "failed-verify"}.get(task_type, "failed-postprocess")
    return lifecycle_state or "queued"


def infer_merge_ready(text: str) -> bool | None:
    lowered = text.lower()
    if not lowered:
        return None
    if any(token in text for token in ["不可合并", "不建议合并", "不建议", "不能合并", "不要合并", "不应合并"]) or "not merge" in lowered or "not ready" in lowered:
        return False
    if any(token in text for token in ["可以合并", "可合并", "建议合并"]) or "mergeable" in lowered:
        return True
    return None


def extract_verify_decision(task: dict, sections: list[dict], queue_name: str, existing: dict | None = None) -> dict:
    decision_items = section_items(sections, ["是否可合并", "merge readiness", "mergeability"])
    decision_text = " ".join(decision_items).strip()
    merge_ready = infer_merge_ready(decision_text)

    if queue_name in {"ready-to-pr", "ready-to-push"}:
        merge_ready = True
        if not decision_text:
            decision_text = "verify passed and moved to delivery gate"
    elif queue_name in {"needs-human", "failed"} and task.get("type") == "verify" and merge_ready is None:
        merge_ready = False

    summary = last_summary_text(sections) or str(task.get("human_reason", "")).strip()
    result = {
        "label": decision_text,
        "merge_ready": merge_ready,
        "summary": summary,
        "delivery_gate": queue_name,
        "human_gate": str(task.get("human_gate", "")).strip(),
        "human_resolution": str(task.get("human_resolution", "")).strip(),
    }
    if existing:
        merged = dict(existing)
        merged.update({key: value for key, value in result.items() if value not in (None, "", [], {})})
        return merged
    return result


def build_evidence(payload: dict, sections: list[dict], task: dict) -> list[dict]:
    evidence = []
    test_results = payload.get("test_results", []) if isinstance(payload.get("test_results"), list) else []
    test_commands = payload.get("test_commands", []) if isinstance(payload.get("test_commands"), list) else []

    lint_results = [item for item in test_results if isinstance(item, dict) and LINT_COMMAND_RE.search(item.get("command", ""))]
    build_results = [item for item in test_results if isinstance(item, dict) and BUILD_COMMAND_RE.search(item.get("command", ""))]
    pure_test_results = [
        item
        for item in test_results
        if isinstance(item, dict)
        and not LINT_COMMAND_RE.search(item.get("command", ""))
        and not BUILD_COMMAND_RE.search(item.get("command", ""))
    ]

    if test_commands or pure_test_results:
        evidence.append(
            {
                "type": "test_output",
                "label": payload.get("test_strategy_level", "test"),
                "summary": f"{len([item for item in pure_test_results if item.get('status') == 'passed'])}/{len(pure_test_results) or len(test_commands)} test commands passed",
                "items": [item.get("command", "") for item in pure_test_results] or test_commands,
            }
        )

    if lint_results:
        evidence.append(
            {
                "type": "lint_output",
                "label": "lint",
                "summary": f"{len([item for item in lint_results if item.get('status') == 'passed'])}/{len(lint_results)} lint commands passed",
                "items": [item.get("command", "") for item in lint_results],
            }
        )

    if build_results:
        evidence.append(
            {
                "type": "build_output",
                "label": "build",
                "summary": f"{len([item for item in build_results if item.get('status') == 'passed'])}/{len(build_results)} build commands passed",
                "items": [item.get("command", "") for item in build_results],
            }
        )

    manual_steps = section_items(sections, ["复现步骤", "manual steps"])
    if manual_steps:
        evidence.append({"type": "manual_steps", "label": "manual", "summary": f"{len(manual_steps)} manual steps", "items": manual_steps})

    screenshots = section_items(sections, ["截图", "screenshot", "screenshots"])
    if screenshots:
        evidence.append({"type": "screenshot", "label": "ui", "summary": f"{len(screenshots)} screenshot refs", "items": screenshots})

    api_samples = section_items(sections, ["api sample", "api_sample", "接口样例"])
    if api_samples:
        evidence.append({"type": "api_sample", "label": "api", "summary": f"{len(api_samples)} api samples", "items": api_samples})

    rollback_notes = section_items(sections, ["rollback note", "回滚方案", "rollback"])
    if rollback_notes:
        evidence.append({"type": "rollback_note", "label": "rollback", "summary": f"{len(rollback_notes)} rollback notes", "items": rollback_notes})

    changed_files = payload.get("changed_files", []) if isinstance(payload.get("changed_files"), list) else []
    if task.get("allow_migration") or any("migration" in item.lower() for item in changed_files):
        evidence.append(
            {
                "type": "migration_note",
                "label": "migration",
                "summary": "migration-affecting change detected or explicitly allowed",
                "items": [item for item in changed_files if "migration" in item.lower()] or ["allow_migration=true"],
            }
        )

    return evidence


def maybe_keep(new_value, existing_value):
    if isinstance(new_value, list):
        return new_value or (existing_value or [])
    if isinstance(new_value, dict):
        merged = dict(existing_value or {})
        for key, value in new_value.items():
            if value not in (None, "", [], {}):
                merged[key] = value
        return merged
    return new_value if new_value not in (None, "", [], {}) else existing_value


def result_path_for_task(task_id: str) -> Path:
    return RESULTS_DIR / f"{task_id}.json"


def build_result_payload(task_path: Path, log_path: Path | None = None, result_path: Path | None = None) -> dict:
    task = read_json(task_path, {})
    if not task:
        raise ValueError(f"invalid task file: {task_path}")

    task_id = str(task.get("id", task_path.stem)).strip() or task_path.stem
    result_path = result_path or result_path_for_task(task_id)
    existing = read_json(result_path, {})
    log_path = log_path or (LOGS_DIR / f"{task_id}.log")
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
    tail_text = "\n".join(log_text.splitlines()[-400:])
    sections = parse_sections(tail_text)
    queue_name = infer_queue_name(task_path, task)
    resolved_status = effective_status(task, queue_name)
    resolved_lifecycle = effective_lifecycle_state(task, queue_name)
    test_results = extract_test_results(log_text, existing.get("test_results"))
    summary = last_summary_text(sections) or str(task.get("human_reason", "")).strip() or existing.get("summary", "") or task.get("title", "")
    repo = str(task.get("repo", "")).strip()
    prompt_file = str(task.get("prompt_file", "")).strip()
    repo_profile_path = REPO_CONFIG_DIR / f"{repo}.json"
    review_findings = extract_review_findings(task, sections, existing.get("review_findings"))

    payload = {
        "version": 1,
        "evidence_schema_version": 1,
        "task_id": task_id,
        "title": str(task.get("title", "")).strip(),
        "stage": str(task.get("type", "")).strip(),
        "task_kind": str(task.get("task_kind", "")).strip(),
        "role": str(task.get("role", "")).strip(),
        "repo": repo,
        "base_branch": str(task.get("base_branch", "")).strip(),
        "branch": str(task.get("branch", "")).strip(),
        "source_ref": str(task.get("source_ref", "")).strip(),
        "worktree": str(task.get("worktree", "")).strip(),
        "status": resolved_status,
        "lifecycle_state": resolved_lifecycle,
        "queue": queue_name,
        "written_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "summary": summary,
        "test_strategy_level": str(task.get("test_strategy_level", "")).strip(),
        "test_strategy_reason": str(task.get("test_strategy_reason", "")).strip(),
        "test_strategy_commands": [str(item).strip() for item in task.get("test_strategy_commands", []) if str(item).strip()],
        "changed_files": extract_changed_files(task, log_text, existing.get("changed_files")),
        "test_commands": maybe_keep([item["command"] for item in test_results], existing.get("test_commands")),
        "test_results": test_results,
        "review_findings": review_findings,
        "severity": extract_severity(review_findings, sections, existing.get("severity")),
        "missing_tests": extract_missing_tests(sections, existing.get("missing_tests")),
        "rule_conflicts": extract_rule_conflicts(sections, existing.get("rule_conflicts")),
        "pass_paths": extract_pass_paths(sections, existing.get("pass_paths")),
        "fail_paths": extract_fail_paths(sections, existing.get("fail_paths")),
        "verify_decision": extract_verify_decision(task, sections, queue_name, existing.get("verify_decision")),
        "merge_decision": extract_verify_decision(task, sections, queue_name, existing.get("merge_decision")),
        "residual_risks": extract_residual_risks(task, sections, existing.get("residual_risks")),
        "prompt_version": file_version(PROMPTS_DIR / prompt_file, {"file": prompt_file}),
        "repo_profile_version": file_version(repo_profile_path, {"repo": repo}),
        "task_schema_version": file_version(TASK_SCHEMA_PATH),
        "budget_report": maybe_keep(task.get("budget_report", {}), existing.get("budget_report")),
        "dod_report": maybe_keep(task.get("dod_report", {}), existing.get("dod_report")),
        "human_gate": str(task.get("human_gate", "")).strip(),
        "human_reason": str(task.get("human_reason", "")).strip(),
        "human_resolution": str(task.get("human_resolution", "")).strip(),
        "log_path": str(log_path),
        "task_path": str(task_path),
    }
    payload["evidence"] = build_evidence(payload, sections, task)

    if existing:
        payload["changed_files"] = maybe_keep(payload["changed_files"], existing.get("changed_files"))
        payload["review_findings"] = maybe_keep(payload["review_findings"], existing.get("review_findings"))
        payload["residual_risks"] = maybe_keep(payload["residual_risks"], existing.get("residual_risks"))
        payload["missing_tests"] = maybe_keep(payload["missing_tests"], existing.get("missing_tests"))
        payload["rule_conflicts"] = maybe_keep(payload["rule_conflicts"], existing.get("rule_conflicts"))
        payload["pass_paths"] = maybe_keep(payload["pass_paths"], existing.get("pass_paths"))
        payload["fail_paths"] = maybe_keep(payload["fail_paths"], existing.get("fail_paths"))
        payload["test_commands"] = maybe_keep(payload["test_commands"], existing.get("test_commands"))
        payload["summary"] = maybe_keep(payload["summary"], existing.get("summary"))

    return payload


def write_task_result(task_path: str | Path, log_path: str | Path | None = None, result_path: str | Path | None = None) -> dict:
    task_path = Path(task_path)
    log_path = Path(log_path) if log_path else None
    result_path = Path(result_path) if result_path else None
    payload = build_result_payload(task_path, log_path=log_path, result_path=result_path)
    write_json(result_path or result_path_for_task(payload["task_id"]), payload)
    return payload


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "write":
        print("usage: result_writer.py write <task.json> [log_path] [result_path]", file=sys.stderr)
        return 1

    task_path = argv[2]
    log_path = argv[3] if len(argv) >= 4 else None
    result_path = argv[4] if len(argv) >= 5 else None
    payload = write_task_result(task_path, log_path=log_path, result_path=result_path)
    print(json.dumps({"task_id": payload["task_id"], "result_path": str(result_path or result_path_for_task(payload["task_id"]))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
