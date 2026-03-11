from flask import Flask, redirect, render_template, request
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import time

MANAGER_DIR = Path(__file__).resolve().parent
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))

from task_schema import ensure_valid_task
from repo_profile import load_repo_profile, load_repo_profiles, order_remote_branches, repo_default_branch, repo_profile_summary
from result_writer import result_path_for_task, write_task_result
from test_strategy import apply_test_strategy
from watchdog import compute_summary as compute_watchdog_summary, heartbeat_worker as heartbeat_watchdog_worker, load_task_snapshot, role_config as watchdog_role_config

WORKFLOW_ROOT = Path.home() / "xtt-workflow"
ROOT = WORKFLOW_ROOT / "manager"
WORKSPACE = WORKFLOW_ROOT / "workspace"
QUEUE = ROOT / "queue"
LOGS = ROOT / "logs"
RESULTS = ROOT / "results"
STATE_DIR = ROOT / "state"
BACKLOG_FILE = Path.home() / "xtt-workflow 最终版优化清单.md"
BACKLOG_STATE_FILE = STATE_DIR / "backlog_state.json"

app = Flask(__name__)

QUEUE_META = {
    "pending": {"label": "待处理", "tone": "warning", "icon": "hourglass-split"},
    "running": {"label": "执行中", "tone": "primary", "icon": "arrow-repeat"},
    "needs-human": {"label": "待人工处理", "tone": "info", "icon": "person-raised-hand"},
    "ready-to-pr": {"label": "待人工 PR", "tone": "secondary", "icon": "git"},
    "ready-to-push": {"label": "待人工 Push", "tone": "secondary", "icon": "cloud-arrow-up"},
    "ready-to-release": {"label": "待发布", "tone": "secondary", "icon": "rocket-takeoff"},
    "delivered": {"label": "已交付", "tone": "success", "icon": "send-check"},
    "done": {"label": "已完成", "tone": "success", "icon": "check-circle"},
    "failed": {"label": "已失败", "tone": "danger", "icon": "x-octagon"},
}

LIFECYCLE_META = {
    "queued": {"label": "Queued", "tone": "secondary"},
    "spec-ready": {"label": "Spec Ready", "tone": "info"},
    "routed": {"label": "Routed", "tone": "primary"},
    "building": {"label": "Building", "tone": "primary"},
    "build-done": {"label": "Build Done", "tone": "success"},
    "reviewing": {"label": "Reviewing", "tone": "primary"},
    "review-done": {"label": "Review Done", "tone": "success"},
    "verifying": {"label": "Verifying", "tone": "primary"},
    "verify-done": {"label": "Verify Done", "tone": "success"},
    "ready-to-pr": {"label": "Ready To PR", "tone": "secondary"},
    "ready-to-push": {"label": "Ready To Push", "tone": "secondary"},
    "ready-to-release": {"label": "Ready To Release", "tone": "secondary"},
    "delivered": {"label": "Delivered", "tone": "success"},
    "failed-build": {"label": "Failed Build", "tone": "danger"},
    "failed-review": {"label": "Failed Review", "tone": "danger"},
    "failed-verify": {"label": "Failed Verify", "tone": "danger"},
    "failed-watchdog": {"label": "Failed Watchdog", "tone": "danger"},
    "failed-postprocess": {"label": "Failed Postprocess", "tone": "danger"},
}

BACKLOG_STATUS_META = {
    "todo": {"label": "待执行", "tone": "secondary"},
    "in_progress": {"label": "执行中", "tone": "primary"},
    "ready_for_human": {"label": "待人工确认", "tone": "warning"},
    "done": {"label": "已完成", "tone": "success"},
    "failed": {"label": "失败待处理", "tone": "danger"},
    "blocked": {"label": "已阻塞", "tone": "dark"},
}

QUEUE_DIRS = {name: QUEUE / name for name in ("pending", "running", "needs-human", "ready-to-pr", "ready-to-push", "ready-to-release", "delivered", "done", "failed")}


def lifecycle_meta(name):
    return LIFECYCLE_META.get(name, {"label": name or "-", "tone": "secondary"})


def stage_done_state(task_type):
    return {"build": "build-done", "review": "review-done", "verify": "verify-done"}.get(task_type, "delivered")


def failed_state(task):
    if str(task.get("watchdog_reason", "")).strip() or str(task.get("failure_reason", "")).startswith("watchdog:"):
        return "failed-watchdog"
    return {"build": "failed-build", "review": "failed-review", "verify": "failed-verify"}.get(task.get("type"), "failed-postprocess")


def running_state(task_type):
    return {"build": "building", "review": "reviewing", "verify": "verifying"}.get(task_type, "routed")


def effective_task_status(task, queue_name):
    status = str(task.get("status", "")).strip()
    if queue_name in QUEUE_META:
        return queue_name
    return status or "pending"


def effective_lifecycle_state(task, queue_name):
    lifecycle_state = str(task.get("lifecycle_state", "")).strip()
    if lifecycle_state and lifecycle_state not in {"queued", "routed"}:
        return lifecycle_state
    if queue_name == "pending":
        return "routed"
    if queue_name == "running":
        return running_state(task.get("type"))
    if queue_name == "done":
        return stage_done_state(task.get("type"))
    if queue_name == "needs-human":
        return "failed-watchdog" if str(task.get("watchdog_reason", "")).strip() else stage_done_state(task.get("type"))
    if queue_name in {"ready-to-pr", "ready-to-push", "ready-to-release", "delivered"}:
        return queue_name
    if queue_name == "failed":
        return failed_state(task)
    return lifecycle_state or "queued"


def ensure_state_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_task_watchdog_info(task_id, queue_name, task):
    snapshot = load_task_snapshot(task_id)
    report = task.get("watchdog_report", {}) if isinstance(task.get("watchdog_report"), dict) else {}
    role = str(task.get("role", snapshot.get("role", "worker"))).strip() or "worker"
    config = watchdog_role_config(role)

    info = {
        "status": "",
        "issues": report.get("issues", []),
        "last_heartbeat_at": snapshot.get("last_heartbeat_at", ""),
        "runtime_sec": None,
        "heartbeat_age_sec": None,
        "target_queue": report.get("target_queue", ""),
        "detected_at": report.get("detected_at", task.get("watchdog_detected_at", "")),
    }

    if queue_name == "running":
        now_ts = time.time()
        started_epoch = float(snapshot.get("running_started_epoch", 0) or 0)
        heartbeat_epoch = float(snapshot.get("last_heartbeat_epoch", 0) or 0)
        if started_epoch:
            info["runtime_sec"] = int(max(0, now_ts - started_epoch))
        if heartbeat_epoch:
            info["heartbeat_age_sec"] = int(max(0, now_ts - heartbeat_epoch))
        timeout_sec = int(snapshot.get("timeout_sec", 0) or config["task_heartbeat_timeout_sec"])
        max_runtime_sec = int(snapshot.get("max_runtime_sec", 0) or config["max_runtime_sec"])
        if info["heartbeat_age_sec"] is not None and timeout_sec and info["heartbeat_age_sec"] > timeout_sec:
            info["status"] = "stuck"
            info["issues"] = info["issues"] or ["heartbeat-timeout"]
        elif info["runtime_sec"] is not None and max_runtime_sec and info["runtime_sec"] > max_runtime_sec:
            info["status"] = "slow"
            info["issues"] = info["issues"] or ["max-runtime-exceeded"]
        elif snapshot:
            info["status"] = "alive"
    elif report:
        info["status"] = "recovered"
    return info


def verify_decision_label(decision):
    if not isinstance(decision, dict):
        return ""
    if decision.get("delivery_gate") == "ready-to-push":
        return "ready-to-push"
    if decision.get("delivery_gate") == "ready-to-pr":
        return "ready-to-pr"
    if decision.get("merge_ready") is True:
        return "merge-ready"
    if decision.get("merge_ready") is False:
        return "not-ready"
    return ""


def load_result_summary(task_id):
    payload = read_json(result_path_for_task(task_id), {})
    if not payload:
        return {}

    test_results = payload.get("test_results", []) if isinstance(payload.get("test_results"), list) else []
    passed = sum(1 for item in test_results if isinstance(item, dict) and item.get("status") == "passed")
    failed = sum(1 for item in test_results if isinstance(item, dict) and item.get("status") == "failed")

    return {
        "filename": result_path_for_task(task_id).name,
        "summary": str(payload.get("summary", "")).strip(),
        "test_strategy_level": str(payload.get("test_strategy_level", "")).strip(),
        "evidence_count": len(payload.get("evidence", []) if isinstance(payload.get("evidence"), list) else []),
        "changed_files_count": len(payload.get("changed_files", []) if isinstance(payload.get("changed_files"), list) else []),
        "test_total": len(test_results),
        "test_passed": passed,
        "test_failed": failed,
        "review_findings_count": len(payload.get("review_findings", []) if isinstance(payload.get("review_findings"), list) else []),
        "verify_label": verify_decision_label(payload.get("verify_decision", {})),
        "queue": str(payload.get("queue", "")).strip(),
        "written_at": str(payload.get("written_at", "")).strip(),
    }


def load_result_payload(task_id):
    return read_json(result_path_for_task(task_id), {})


def task_log_name(task_id):
    return f"{task_id}.log"


def classify_failure(task, result=None):
    result = result or {}
    if str(task.get("watchdog_reason", "")).strip():
        return "watchdog"
    if str(task.get("failure_reason", "")).startswith("watchdog:"):
        return "watchdog"
    if str(task.get("human_gate", "")).strip():
        return f"human:{task.get('human_gate')}"
    lifecycle = str(task.get("lifecycle_state", "")).strip()
    if lifecycle.startswith("failed-"):
        return lifecycle.removeprefix("failed-")
    if isinstance(result, dict) and result.get("test_results"):
        failed = [item for item in result.get("test_results", []) if isinstance(item, dict) and item.get("status") == "failed"]
        if failed:
            return "test"
    return "general"


def task_risk_tags(task, result=None):
    result = result or {}
    tags = []
    risk_level = str(task.get("risk_level", "")).strip()
    if risk_level:
        tags.append(risk_level)
    if str(task.get("test_strategy_level", "")).strip():
        tags.append(f"test:{task['test_strategy_level']}")
    if task.get("allow_dependency_changes"):
        tags.append("dep-change")
    if task.get("allow_migration"):
        tags.append("migration")
    if task.get("allow_deploy_changes"):
        tags.append("deploy")
    if task.get("allow_ci_changes"):
        tags.append("ci")
    if task.get("allow_cross_layer_refactor"):
        tags.append("xlayer")
    for signal in task.get("risk_signals", []) if isinstance(task.get("risk_signals"), list) else []:
        signal_text = str(signal).strip().replace("_", "-")
        if signal_text:
            tags.append(f"risk:{signal_text}")
    if task.get("human_gate"):
        tags.append(f"gate:{task['human_gate']}")
    if isinstance(result, dict):
        for signal in result.get("risk_signals", []) if isinstance(result.get("risk_signals"), list) else []:
            signal_text = str(signal).strip().replace("_", "-")
            if signal_text:
                tags.append(f"risk:{signal_text}")
        if result.get("review_findings"):
            tags.append("findings")
        if result.get("evidence"):
            tags.append(f"evidence:{len(result['evidence'])}")
        decision = result.get("verify_decision", {}) if isinstance(result.get("verify_decision"), dict) else {}
        if decision.get("merge_ready") is False:
            tags.append("not-merge-ready")
    return dedupe_list(tags)


def find_task_file(queue_name, name):
    if queue_name not in QUEUE_DIRS:
        return None
    filename = name if name.endswith(".json") else f"{name}.json"
    path = QUEUE_DIRS[queue_name] / filename
    return path if path.exists() else None


def build_console_summary(tasks, watchdog):
    failure_categories = {}
    for task in tasks.get("failed", []):
        category = task.get("failure_category", "general")
        failure_categories[category] = failure_categories.get(category, 0) + 1

    return {
        "ready_to_pr": len(tasks.get("ready-to-pr", [])),
        "ready_to_push": len(tasks.get("ready-to-push", [])),
        "needs_human": len(tasks.get("needs-human", [])),
        "worker_alerts": watchdog["counts"].get("worker_alerts", 0),
        "failure_categories": sorted(failure_categories.items(), key=lambda item: (-item[1], item[0])),
    }


def refresh_result_for_queue_file(task_file):
    task_file = Path(task_file)
    if not task_file.exists():
        return
    try:
        write_task_result(task_file, log_path=LOGS / f"{task_file.stem}.log")
    except Exception:
        return


def load_tasks(queue_name):
    items = []
    for path in sorted((QUEUE / queue_name).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        task_id = task.get("id", path.stem)
        result_payload = load_result_payload(task_id)
        result_summary = load_result_summary(task_id)
        failure_category = classify_failure(task, result=result_payload)
        log_name = task_log_name(task_id)
        log_path = LOGS / log_name
        items.append(
            {
                "id": task_id,
                "title": task.get("title", path.stem),
                "task_kind": task.get("task_kind", "feature"),
                "repo": task.get("repo", "-"),
                "role": task.get("role", "-"),
                "type": task.get("type", "-"),
                "status": effective_task_status(task, queue_name),
                "lifecycle_state": effective_lifecycle_state(task, queue_name),
                "lifecycle_meta": lifecycle_meta(effective_lifecycle_state(task, queue_name)),
                "base_branch": task.get("base_branch", "-"),
                "branch": task.get("branch", ""),
                "worktree": task.get("worktree", "-"),
                "retry_count": task.get("retry_count", 0),
                "filename": path.name,
                "backlog_item_id": task.get("backlog_item_id", ""),
                "human_reason": task.get("human_reason", ""),
                "human_gate": task.get("human_gate", ""),
                "human_resolution": task.get("human_resolution", ""),
                "budget_report": task.get("budget_report", {}),
                "dod_report": task.get("dod_report", {}),
                "allowed_paths": task.get("allowed_paths", []),
                "forbidden_paths": task.get("forbidden_paths", []),
                "allow_dependency_changes": task.get("allow_dependency_changes", False),
                "allow_migration": task.get("allow_migration", False),
                "allow_ci_changes": task.get("allow_ci_changes", False),
                "allow_deploy_changes": task.get("allow_deploy_changes", False),
                "allow_cross_layer_refactor": task.get("allow_cross_layer_refactor", False),
                "log_name": log_name,
                "log_exists": log_path.exists(),
                "result_summary": result_summary,
                "result_exists": bool(result_payload),
                "failure_category": failure_category,
                "risk_tags": task_risk_tags(task, result=result_payload),
                "watchdog": build_task_watchdog_info(task.get("id", path.stem), queue_name, task),
            }
        )
    return items


def load_logs(limit=100):
    items = []
    for path in sorted(LOGS.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                "size_kb": max(1, stat.st_size // 1024),
            }
        )
    return items


def list_projects():
    if not WORKSPACE.exists():
        return []
    excluded_suffixes = ("-wt-build", "-wt-review", "-wt-verify", "-wt-plan")
    projects = []
    for path in sorted(WORKSPACE.iterdir()):
        if not path.is_dir():
            continue
        if path.name.endswith(excluded_suffixes):
            continue
        if not (path / ".git").exists():
            continue
        projects.append(path.name)
    return projects


def list_remote_branches(repo):
    repo_path = WORKSPACE / repo
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return []

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "branch", "-r", "--format=%(refname:short)"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    branches = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("origin/") or line == "origin/HEAD":
            continue
        branch = line.removeprefix("origin/")
        if branch == "HEAD":
            continue
        branches.append(branch)

    preferred = ["main", "master", "develop"]
    ordered = [branch for branch in preferred if branch in branches]
    ordered.extend(sorted(branch for branch in branches if branch not in preferred))
    return ordered


def dedupe_list(items):
    result = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def get_repo_profile(repo):
    return load_repo_profile(repo)


def get_repo_default_branch(repo, remote_branches=None, profile=None):
    profile = profile or get_repo_profile(repo)
    branches = remote_branches if remote_branches is not None else list_remote_branches(repo)
    return repo_default_branch(profile, branches)


def get_ordered_repo_branches(repo, remote_branches=None, profile=None):
    profile = profile or get_repo_profile(repo)
    branches = remote_branches if remote_branches is not None else list_remote_branches(repo)
    return order_remote_branches(profile, branches)


def safe_name(value):
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-") or "project"


def next_retry_branch(branch, retry_count):
    retry_index = int(retry_count) + 1
    stamp = int(time.time())
    branch = (branch or "retry").strip()
    if "/" in branch:
        prefix, rest = branch.split("/", 1)
        return f"{prefix}/{safe_name(rest)}-retry-{retry_index}-{stamp}"
    return f"{safe_name(branch)}-retry-{retry_index}-{stamp}"


def worktree_name(repo, stage):
    return f"{repo}-wt-{stage}"


def parse_line_value(section_text, label):
    match = re.search(rf"- \*\*{re.escape(label)}\*\*：(.+)", section_text)
    return match.group(1).strip() if match else ""


def parse_bullet_block(section_text, label):
    match = re.search(rf"- \*\*{re.escape(label)}\*\*：\n((?:  - .+\n)+)", section_text)
    if not match:
        return []
    return [line.strip()[2:].strip() for line in match.group(1).splitlines() if line.strip().startswith("- ")]


def phase_for_position(text, position):
    phase = "未分类"
    for match in re.finditer(r"^#\s+([四五六]、[^\n]+)$", text, re.MULTILINE):
        if match.start() > position:
            break
        phase = match.group(1).strip()
    return phase


def parse_backlog_file():
    if not BACKLOG_FILE.exists():
        return []

    text = BACKLOG_FILE.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^##\s+(P\d-\d{2})\s+(.+)$", text, re.MULTILINE))
    items = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section_text = text[start:end].strip() + "\n"
        item_id = match.group(1).strip()
        title = match.group(2).strip()
        dependency_text = parse_line_value(section_text, "依赖")
        dependencies = [] if dependency_text in {"", "无"} else re.findall(r"`([^`]+)`", dependency_text)
        items.append(
            {
                "id": item_id,
                "title": title,
                "phase": phase_for_position(text, match.start()),
                "priority": parse_line_value(section_text, "优先级") or "-",
                "effort": parse_line_value(section_text, "工作量") or "-",
                "goal": parse_line_value(section_text, "目标") or "",
                "why_now": parse_line_value(section_text, "为什么现在做") or "",
                "outputs": parse_bullet_block(section_text, "产出物"),
                "files": parse_bullet_block(section_text, "涉及文件"),
                "acceptance": parse_bullet_block(section_text, "验收标准"),
                "dependencies": dependencies,
            }
        )
    return items


def load_backlog_state_raw():
    ensure_state_dir()
    if not BACKLOG_STATE_FILE.exists():
        state = {"items": {}}
        save_backlog_state_raw(state)
        return state

    state = read_json(BACKLOG_STATE_FILE, {"items": {}})
    state.setdefault("items", {})
    if not isinstance(state["items"], dict):
        state["items"] = {}
        save_backlog_state_raw(state)
    return state


def save_backlog_state_raw(state):
    write_json(BACKLOG_STATE_FILE, state)


def collect_backlog_tasks():
    task_map = {}
    for queue_name, queue_dir in QUEUE_DIRS.items():
        for path in queue_dir.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            item_id = task.get("backlog_item_id")
            if not item_id:
                continue
            task_map.setdefault(item_id, []).append(
                {
                    "queue": queue_name,
                    "task_id": task.get("id", path.stem),
                    "type": task.get("type", "build"),
                    "title": task.get("title", path.stem),
                    "repo": task.get("repo", ""),
                    "base_branch": task.get("base_branch", ""),
                }
            )
    return task_map


def compute_backlog_items():
    items = parse_backlog_file()
    state = load_backlog_state_raw()
    stored_items = state.get("items", {})
    task_map = collect_backlog_tasks()

    backlog_items = []
    for item in items:
        stored = stored_items.get(item["id"], {})
        manual_status = stored.get("manual_status", "todo")
        related_tasks = task_map.get(item["id"], [])

        if manual_status == "done":
            effective_status = "done"
        elif manual_status == "blocked":
            effective_status = "blocked"
        elif any(task["queue"] == "failed" for task in related_tasks):
            effective_status = "failed"
        elif any(task["queue"] in {"needs-human", "ready-to-pr", "ready-to-push"} for task in related_tasks):
            effective_status = "ready_for_human"
        elif any(task["queue"] in {"pending", "running"} for task in related_tasks):
            effective_status = "in_progress"
        elif any(task["queue"] == "done" and task["type"] == "verify" for task in related_tasks):
            effective_status = "ready_for_human"
        elif any(task["queue"] == "done" for task in related_tasks):
            effective_status = "in_progress"
        else:
            effective_status = "todo"

        backlog_items.append(
            {
                **item,
                "status": effective_status,
                "status_meta": BACKLOG_STATUS_META[effective_status],
                "manual_status": manual_status,
                "repo": stored.get("repo", ""),
                "base_branch": stored.get("base_branch", ""),
                "last_task_id": stored.get("last_task_id", ""),
                "updated_at": stored.get("updated_at", ""),
                "related_tasks": related_tasks,
            }
        )

    status_map = {item["id"]: item["status"] for item in backlog_items}
    for item in backlog_items:
        item["deps_done"] = all(status_map.get(dep) == "done" for dep in item["dependencies"])
        item["is_ready"] = item["status"] == "todo" and item["deps_done"]

    blocked_reason = ""
    if any(item["status"] == "in_progress" for item in backlog_items):
        blocked_reason = "当前已有 backlog 任务执行中，最小模式下一次只推进一项。"
    elif any(item["status"] == "ready_for_human" for item in backlog_items):
        blocked_reason = "当前已有 backlog 项待人工确认，请先确认后再推进下一项。"
    elif any(item["status"] == "failed" for item in backlog_items):
        blocked_reason = "当前有 backlog 项失败，请先处理失败任务后再继续自动推进。"

    next_ready_item = None
    if not blocked_reason:
        next_ready_item = next((item for item in backlog_items if item["is_ready"]), None)

    counts = {key: 0 for key in BACKLOG_STATUS_META}
    for item in backlog_items:
        counts[item["status"]] += 1

    return {
        "items": backlog_items,
        "counts": counts,
        "next_ready_item": next_ready_item,
        "blocked_reason": blocked_reason,
    }


def queue_task(task):
    task = dict(task)
    task["status"] = "pending"
    task["lifecycle_state"] = "routed"
    task = ensure_valid_task(task)
    out = QUEUE / "pending" / f"{task['id']}.json"
    out.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return task


def create_standard_task(title, repo, base_branch, prompt_file="build_prompt.md", extra_fields=None):
    ts = int(time.time())
    task_id = f"task-{ts}"
    repo_safe = safe_name(repo)
    repo_profile = get_repo_profile(repo)
    resolved_branch = base_branch or get_repo_default_branch(repo, profile=repo_profile)
    evidence_required = ["changed files", "summary", "verification", "risks"]
    if repo_profile.get("needs_ui_evidence"):
        evidence_required.append("ui evidence")
    task = {
        "id": task_id,
        "type": "build",
        "task_kind": "infra" if prompt_file == "backlog_build_prompt.md" else "feature",
        "title": title,
        "repo": repo,
        "base_branch": resolved_branch,
        "repo_profile_summary": repo_profile_summary(repo_profile),
        "repo_profile": repo_profile,
        "goal": title,
        "acceptance": [f"完成任务目标：{title}", "给出具体验证结果"],
        "risk_level": "medium",
        "evidence_required": evidence_required,
        "change_budget": {"max_files": 20, "max_lines": 400},
        "allow_auto_commit": True,
        "allow_push": False,
        "allow_pr": False,
        "allow_dependency_changes": False,
        "allow_migration": False,
        "allow_ci_changes": False,
        "allow_deploy_changes": False,
        "allow_cross_layer_refactor": False,
        "allowed_paths": [],
        "forbidden_paths": repo_profile.get("forbidden_paths", []),
        "worktree": worktree_name(repo, "build"),
        "branch": f"feat/{repo_safe}-{task_id}",
        "prompt_file": prompt_file,
        "role": "builder",
        "status": "pending",
        "lifecycle_state": "queued",
        "retry_count": 0,
        "depends_on": [],
    }
    if extra_fields:
        task.update(extra_fields)
    if "repo_profile" not in task:
        task["repo_profile"] = repo_profile
    if "repo_profile_summary" not in task:
        task["repo_profile_summary"] = repo_profile_summary(repo_profile)
    task["forbidden_paths"] = dedupe_list(task.get("forbidden_paths", []) + repo_profile.get("forbidden_paths", []))
    if repo_profile.get("needs_ui_evidence"):
        task["evidence_required"] = dedupe_list(task.get("evidence_required", []) + ["ui evidence"])
    return apply_test_strategy(ensure_valid_task(task), profile=repo_profile)


def validate_repo_and_branch(repo, base_branch):
    repo_path = WORKSPACE / repo
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return f"project repo not found: {repo}"
    valid_branches = get_ordered_repo_branches(repo)
    if valid_branches and base_branch not in valid_branches:
        return f"base branch not found in origin for {repo}: {base_branch}"
    return ""


def dashboard_context():
    heartbeat_watchdog_worker("web", status="serving", session_name="xtt-web", loop_pid=os.getpid())
    queue_order = ["pending", "running", "needs-human", "ready-to-pr", "ready-to-push", "done", "failed"]
    tasks = {name: load_tasks(name) for name in queue_order}
    queue_counts = {name: len(tasks[name]) for name in queue_order}
    logs = load_logs()
    projects = list_projects()
    repo_profiles = load_repo_profiles(projects)
    branch_map = {project: get_ordered_repo_branches(project, profile=repo_profiles[project]) for project in projects}
    repo_default_branch_map = {
        project: get_repo_default_branch(project, remote_branches=branch_map[project], profile=repo_profiles[project])
        for project in projects
    }
    default_repo = projects[0] if projects else "repo-main"
    default_branch = repo_default_branch_map.get(default_repo, "main")
    total_tasks = sum(queue_counts.values())
    backlog = compute_backlog_items()
    self_git_ready = (WORKFLOW_ROOT / ".git").exists()
    watchdog = compute_watchdog_summary(apply_actions=False)
    console_summary = build_console_summary(tasks, watchdog)

    return {
        "queue_order": queue_order,
        "queue_meta": QUEUE_META,
        "lifecycle_meta": LIFECYCLE_META,
        "tasks": tasks,
        "queue_counts": queue_counts,
        "logs": logs,
        "projects": projects,
        "branch_map": branch_map,
        "repo_profiles": repo_profiles,
        "repo_default_branch_map": repo_default_branch_map,
        "default_repo": default_repo,
        "default_branch": default_branch,
        "total_tasks": total_tasks,
        "backlog_file": str(BACKLOG_FILE),
        "backlog": backlog,
        "backlog_status_meta": BACKLOG_STATUS_META,
        "self_git_ready": self_git_ready,
        "watchdog": watchdog,
        "console_summary": console_summary,
    }


@app.get("/")
def index():
    return render_template("index.html", **dashboard_context())


@app.post("/create")
def create():
    title = request.form.get("title", "未命名任务").strip() or "未命名任务"
    repo = request.form.get("repo", "repo-main").strip() or "repo-main"
    base_branch = request.form.get("base_branch", "").strip() or get_repo_default_branch(repo)
    error = validate_repo_and_branch(repo, base_branch)
    if error:
        return error, 400

    try:
        queue_task(create_standard_task(title, repo, base_branch))
    except ValueError as exc:
        return str(exc), 400
    return redirect("/")


@app.post("/backlog/next")
def backlog_next():
    repo = request.form.get("backlog_repo", "repo-main").strip() or "repo-main"
    base_branch = request.form.get("backlog_base_branch", "").strip() or get_repo_default_branch(repo)
    error = validate_repo_and_branch(repo, base_branch)
    if error:
        return error, 400

    backlog = compute_backlog_items()
    next_item = backlog["next_ready_item"]
    if backlog["blocked_reason"]:
        return backlog["blocked_reason"], 409
    if not next_item:
        return "no ready backlog item", 409

    task = create_standard_task(
        title=f"[{next_item['id']}] {next_item['title']}",
        repo=repo,
        base_branch=base_branch,
        prompt_file="backlog_build_prompt.md",
        extra_fields={
            "backlog_item_id": next_item["id"],
            "backlog_item_title": next_item["title"],
            "goal": next_item["goal"],
            "acceptance": next_item["acceptance"] or ["参考 backlog item 的验收标准"],
            "files_hint": next_item["files"] or ["允许先做必要探索，再最小化修改"],
            "outputs_hint": next_item["outputs"] or ["输出 changed files / summary / verification / risks"],
            "phase": next_item["phase"],
            "allowed_paths": next_item["files"],
            "branch": f"feat/{safe_name(repo)}-{safe_name(next_item['id'].lower())}-{int(time.time())}",
        },
    )
    try:
        task = queue_task(task)
    except ValueError as exc:
        return str(exc), 400

    state = load_backlog_state_raw()
    state["items"][next_item["id"]] = {
        "manual_status": "todo",
        "last_task_id": task["id"],
        "repo": repo,
        "base_branch": base_branch,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    save_backlog_state_raw(state)
    return redirect("/")


@app.post("/backlog/approve/<item_id>")
def backlog_approve(item_id):
    state = load_backlog_state_raw()
    current = state["items"].get(item_id, {})
    current["manual_status"] = "done"
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    state["items"][item_id] = current
    save_backlog_state_raw(state)
    return redirect("/")


@app.post("/backlog/block/<item_id>")
def backlog_block(item_id):
    state = load_backlog_state_raw()
    current = state["items"].get(item_id, {})
    current["manual_status"] = "blocked"
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    state["items"][item_id] = current
    save_backlog_state_raw(state)
    return redirect("/")


@app.post("/backlog/reset/<item_id>")
def backlog_reset(item_id):
    state = load_backlog_state_raw()
    current = state["items"].get(item_id, {})
    current["manual_status"] = "todo"
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    state["items"][item_id] = current
    save_backlog_state_raw(state)
    return redirect("/")


@app.get("/retry/<name>")
def retry(name):
    src = QUEUE / "failed" / name
    if not src.exists():
        return "not found", 404

    task = json.loads(src.read_text(encoding="utf-8"))
    retry_count = int(task.get("retry_count", 0))
    task["retry_count"] = retry_count + 1
    if task.get("branch"):
        task["branch"] = next_retry_branch(task.get("branch", ""), retry_count)
    task["status"] = "pending"
    task["lifecycle_state"] = "routed"
    out = QUEUE / "pending" / name
    out.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    src.unlink()
    return redirect("/")


@app.post("/human/continue/<name>")
def human_continue(name):
    src = QUEUE / "needs-human" / name
    if not src.exists():
        return "not found", 404

    task = json.loads(src.read_text(encoding="utf-8"))
    task["status"] = "done"
    task["lifecycle_state"] = stage_done_state(task.get("type"))
    task["human_override"] = True
    task["human_resolution"] = "continued"
    task["human_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    out = QUEUE / "done" / name
    out.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    refresh_result_for_queue_file(out)
    src.unlink()
    return redirect("/")


@app.post("/human/reject/<name>")
def human_reject(name):
    src = QUEUE / "needs-human" / name
    if not src.exists():
        return "not found", 404

    task = json.loads(src.read_text(encoding="utf-8"))
    task["status"] = "failed"
    task["lifecycle_state"] = failed_state(task)
    task["human_resolution"] = "rejected"
    task["human_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    out = QUEUE / "failed" / name
    out.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    refresh_result_for_queue_file(out)
    src.unlink()
    return redirect("/")


@app.get("/log/<name>")
def log(name):
    path = LOGS / name
    if not path.exists():
        return "not found", 404

    stat = path.stat()
    content = path.read_text(encoding="utf-8", errors="ignore")
    return render_template(
        "log.html",
        name=path.name,
        mtime=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        size_kb=max(1, stat.st_size // 1024),
        content=content,
    )


@app.get("/result/<name>")
def result(name):
    filename = name if name.endswith(".json") else f"{name}.json"
    path = RESULTS / filename
    if not path.exists():
        return "not found", 404

    stat = path.stat()
    payload = read_json(path, {})
    return render_template(
        "result.html",
        name=path.name,
        mtime=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        size_kb=max(1, stat.st_size // 1024),
        result=payload,
        raw=json.dumps(payload, ensure_ascii=False, indent=2),
    )


@app.get("/task/<queue_name>/<name>")
def task_detail(queue_name, name):
    path = find_task_file(queue_name, name)
    if not path:
        return "not found", 404

    task = read_json(path, {})
    task_id = task.get("id", path.stem)
    result_payload = load_result_payload(task_id)
    log_path = LOGS / task_log_name(task_id)
    stat = path.stat()
    return render_template(
        "task.html",
        queue_name=queue_name,
        task=task,
        task_id=task_id,
        task_filename=path.name,
        task_mtime=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        result=result_payload,
        result_exists=bool(result_payload),
        log_exists=log_path.exists(),
        log_name=log_path.name,
        failure_category=classify_failure(task, result=result_payload),
        risk_tags=task_risk_tags(task, result=result_payload),
        raw=json.dumps(task, ensure_ascii=False, indent=2),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787, debug=False)
