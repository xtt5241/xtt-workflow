from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys
import time

MANAGER_DIR = Path(__file__).resolve().parent
WORKFLOW_ROOT = MANAGER_DIR.parent
QUEUE_DIR = MANAGER_DIR / "queue"
LOGS_DIR = MANAGER_DIR / "logs"
STATE_DIR = MANAGER_DIR / "state"
WATCHDOG_DIR = STATE_DIR / "watchdog"
WORKERS_DIR = WATCHDOG_DIR / "workers"
TASKS_DIR = WATCHDOG_DIR / "tasks"
SUMMARY_PATH = WATCHDOG_DIR / "summary.json"

WORKER_CONFIG = {
    "builder": {"session": "xtt-builder", "heartbeat_stale_sec": 30, "task_heartbeat_timeout_sec": 120, "max_runtime_sec": 3600},
    "reviewer": {"session": "xtt-reviewer", "heartbeat_stale_sec": 30, "task_heartbeat_timeout_sec": 120, "max_runtime_sec": 2400},
    "verifier": {"session": "xtt-verifier", "heartbeat_stale_sec": 30, "task_heartbeat_timeout_sec": 120, "max_runtime_sec": 2400},
    "planner": {"session": "xtt-planner", "heartbeat_stale_sec": 30, "task_heartbeat_timeout_sec": 120, "max_runtime_sec": 1800},
    "postprocess": {"session": "xtt-postprocess", "heartbeat_stale_sec": 30, "task_heartbeat_timeout_sec": 0, "max_runtime_sec": 0},
    "web": {"session": "xtt-web", "heartbeat_stale_sec": 120, "task_heartbeat_timeout_sec": 0, "max_runtime_sec": 0},
}


def now_ts() -> float:
    return time.time()


def fmt_ts(ts: float | int | None = None) -> str:
    value = now_ts() if ts is None else float(ts)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def ensure_dirs() -> None:
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def role_config(role: str) -> dict:
    base = WORKER_CONFIG.get(role, {})
    return {
        "session": base.get("session", f"xtt-{role}"),
        "heartbeat_stale_sec": int(base.get("heartbeat_stale_sec", 30)),
        "task_heartbeat_timeout_sec": int(base.get("task_heartbeat_timeout_sec", 120)),
        "max_runtime_sec": int(base.get("max_runtime_sec", 3600)),
    }


def tmux_session_exists(session_name: str) -> bool:
    if not session_name:
        return False
    try:
        result = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def process_alive(pid) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except OSError:
        return False
    return True


def worker_path(role: str) -> Path:
    return WORKERS_DIR / f"{role}.json"


def task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def load_worker(role: str) -> dict:
    return read_json(worker_path(role), {})


def load_task_snapshot(task_id: str) -> dict:
    return read_json(task_path(task_id), {})


def heartbeat_worker(role: str, status: str = "idle", current_task_id: str = "", session_name: str = "", loop_pid=None) -> dict:
    ensure_dirs()
    config = role_config(role)
    session_name = session_name or config["session"]
    snapshot = load_worker(role)
    ts = now_ts()
    snapshot.update(
        {
            "role": role,
            "session": session_name,
            "status": status,
            "current_task_id": current_task_id,
            "last_seen_at": fmt_ts(ts),
            "last_seen_epoch": ts,
            "loop_pid": int(loop_pid) if str(loop_pid or "").isdigit() else snapshot.get("loop_pid"),
            "tmux_ok": tmux_session_exists(session_name),
            "heartbeat_stale_sec": config["heartbeat_stale_sec"],
        }
    )
    write_json(worker_path(role), snapshot)
    return snapshot


def start_task(task_file: Path, log_path: str = "", session_name: str = "", loop_pid=None, runner_pid=None) -> dict:
    ensure_dirs()
    task = read_json(task_file, {})
    if not task:
        raise ValueError(f"invalid task file: {task_file}")

    task_id = str(task.get("id", task_file.stem)).strip() or task_file.stem
    role = str(task.get("role", "")).strip() or "worker"
    config = role_config(role)
    snapshot = load_task_snapshot(task_id)
    ts = now_ts()
    snapshot.update(
        {
            "task_id": task_id,
            "title": str(task.get("title", "")).strip(),
            "repo": str(task.get("repo", "")).strip(),
            "base_branch": str(task.get("base_branch", "")).strip(),
            "role": role,
            "queue_file": str(task_file),
            "log_path": str(log_path),
            "worker_session": session_name or config["session"],
            "worker_loop_pid": int(loop_pid) if str(loop_pid or "").isdigit() else snapshot.get("worker_loop_pid"),
            "runner_pid": int(runner_pid) if str(runner_pid or "").isdigit() else snapshot.get("runner_pid"),
            "timeout_sec": int(task.get("watchdog_timeout_sec", 0) or config["task_heartbeat_timeout_sec"]),
            "max_runtime_sec": int(task.get("max_runtime_sec", 0) or config["max_runtime_sec"]),
            "status": "running",
            "running_started_at": snapshot.get("running_started_at", fmt_ts(ts)),
            "running_started_epoch": float(snapshot.get("running_started_epoch", ts)),
            "last_heartbeat_at": fmt_ts(ts),
            "last_heartbeat_epoch": ts,
        }
    )
    write_json(task_path(task_id), snapshot)
    heartbeat_worker(role, status="busy", current_task_id=task_id, session_name=session_name, loop_pid=loop_pid)
    return snapshot


def heartbeat_task(task_file: Path, log_path: str = "", session_name: str = "", loop_pid=None, runner_pid=None) -> dict:
    ensure_dirs()
    if not task_file.exists():
        return {}

    task = read_json(task_file, {})
    if not task:
        return {}

    task_id = str(task.get("id", task_file.stem)).strip() or task_file.stem
    snapshot = start_task(task_file, log_path=log_path, session_name=session_name, loop_pid=loop_pid, runner_pid=runner_pid)
    ts = now_ts()
    snapshot["last_heartbeat_at"] = fmt_ts(ts)
    snapshot["last_heartbeat_epoch"] = ts
    if str(runner_pid or "").isdigit():
        snapshot["runner_pid"] = int(runner_pid)
    write_json(task_path(task_id), snapshot)
    heartbeat_worker(snapshot.get("role", "worker"), status="busy", current_task_id=task_id, session_name=snapshot.get("worker_session", session_name), loop_pid=loop_pid)
    return snapshot


def finish_task(task_id: str, final_queue: str, final_status: str, role: str = "") -> dict:
    ensure_dirs()
    snapshot = load_task_snapshot(task_id)
    ts = now_ts()
    snapshot.update(
        {
            "status": final_status,
            "final_queue": final_queue,
            "finished_at": fmt_ts(ts),
            "finished_epoch": ts,
        }
    )
    if snapshot:
        write_json(task_path(task_id), snapshot)
    role_name = role or snapshot.get("role", "")
    if role_name:
        current = load_worker(role_name)
        heartbeat_worker(role_name, status="idle", current_task_id="", session_name=current.get("session", role_config(role_name)["session"]), loop_pid=current.get("loop_pid"))
    return snapshot


def expected_worker_roles() -> list[str]:
    return ["builder", "reviewer", "verifier", "postprocess", "web"]


def build_worker_status(role: str, ts: float | None = None) -> dict:
    ensure_dirs()
    current_ts = now_ts() if ts is None else ts
    config = role_config(role)
    snapshot = load_worker(role)
    last_seen_epoch = float(snapshot.get("last_seen_epoch", 0) or 0)
    last_seen_age = int(max(0, current_ts - last_seen_epoch)) if last_seen_epoch else None
    session_name = snapshot.get("session", config["session"])
    tmux_ok = tmux_session_exists(session_name)
    loop_ok = process_alive(snapshot.get("loop_pid")) if snapshot.get("loop_pid") else False
    stale = (last_seen_age is None or last_seen_age > config["heartbeat_stale_sec"] or not tmux_ok)
    if not tmux_ok:
        health = "tmux-missing"
    elif last_seen_age is None:
        health = "no-heartbeat"
    elif last_seen_age > config["heartbeat_stale_sec"]:
        health = "stale"
    else:
        health = "ok"
    return {
        "role": role,
        "session": session_name,
        "status": snapshot.get("status", "unknown"),
        "current_task_id": snapshot.get("current_task_id", ""),
        "last_seen_at": snapshot.get("last_seen_at", ""),
        "last_seen_age_sec": last_seen_age,
        "tmux_ok": tmux_ok,
        "loop_pid": snapshot.get("loop_pid"),
        "loop_pid_alive": loop_ok,
        "stale_after_sec": config["heartbeat_stale_sec"],
        "health": health,
        "is_alert": stale,
    }


def analyze_running_task(task_file: Path, ts: float | None = None) -> dict:
    current_ts = now_ts() if ts is None else ts
    task = read_json(task_file, {})
    task_id = str(task.get("id", task_file.stem)).strip() or task_file.stem
    snapshot = load_task_snapshot(task_id)
    role = str(task.get("role", snapshot.get("role", "worker"))).strip() or "worker"
    config = role_config(role)
    heartbeat_timeout_sec = int(snapshot.get("timeout_sec", 0) or task.get("watchdog_timeout_sec", 0) or config["task_heartbeat_timeout_sec"])
    max_runtime_sec = int(snapshot.get("max_runtime_sec", 0) or task.get("max_runtime_sec", 0) or config["max_runtime_sec"])
    started_epoch = float(snapshot.get("running_started_epoch", 0) or task_file.stat().st_mtime)
    last_heartbeat_epoch = float(snapshot.get("last_heartbeat_epoch", 0) or task_file.stat().st_mtime)
    runtime_sec = int(max(0, current_ts - started_epoch))
    heartbeat_age_sec = int(max(0, current_ts - last_heartbeat_epoch))
    worker_status = build_worker_status(role, ts=current_ts)
    runner_pid = snapshot.get("runner_pid")
    runner_alive = process_alive(runner_pid) if runner_pid else False
    issues = []
    if heartbeat_timeout_sec and heartbeat_age_sec > heartbeat_timeout_sec:
        issues.append("heartbeat-timeout")
    if max_runtime_sec and runtime_sec > max_runtime_sec:
        issues.append("max-runtime-exceeded")
    if not worker_status["tmux_ok"]:
        issues.append("tmux-missing")
    if runner_pid and not runner_alive:
        issues.append("runner-dead")

    target_queue = ""
    if "heartbeat-timeout" in issues or "runner-dead" in issues or "tmux-missing" in issues:
        target_queue = "failed"
    elif "max-runtime-exceeded" in issues:
        target_queue = "needs-human"

    return {
        "task_id": task_id,
        "title": str(task.get("title", "")).strip(),
        "role": role,
        "repo": str(task.get("repo", "")).strip(),
        "task_file": str(task_file),
        "queue": "running",
        "started_at": snapshot.get("running_started_at", fmt_ts(started_epoch)),
        "last_heartbeat_at": snapshot.get("last_heartbeat_at", fmt_ts(last_heartbeat_epoch)),
        "runtime_sec": runtime_sec,
        "heartbeat_age_sec": heartbeat_age_sec,
        "heartbeat_timeout_sec": heartbeat_timeout_sec,
        "max_runtime_sec": max_runtime_sec,
        "worker_session": snapshot.get("worker_session", worker_status["session"]),
        "runner_pid": runner_pid,
        "runner_alive": runner_alive,
        "worker_health": worker_status["health"],
        "issues": issues,
        "is_stuck": bool(target_queue),
        "target_queue": target_queue,
        "watchdog_report": {
            "issues": issues,
            "detected_at": fmt_ts(current_ts),
            "runtime_sec": runtime_sec,
            "heartbeat_age_sec": heartbeat_age_sec,
            "heartbeat_timeout_sec": heartbeat_timeout_sec,
            "max_runtime_sec": max_runtime_sec,
            "worker_session": snapshot.get("worker_session", worker_status["session"]),
            "runner_pid": runner_pid,
            "worker_health": worker_status["health"],
        },
    }


def append_watchdog_log(task_id: str, report: dict, target_queue: str) -> None:
    log_path = LOGS_DIR / f"{task_id}.log"
    lines = [
        "",
        "--- watchdog ---",
        f"target_queue: {target_queue}",
        f"issues: {', '.join(report.get('issues', [])) or '(none)'}",
        f"runtime_sec: {report.get('runtime_sec')}",
        f"heartbeat_age_sec: {report.get('heartbeat_age_sec')}",
        f"worker_session: {report.get('worker_session')}",
        f"worker_health: {report.get('worker_health')}",
        f"detected_at: {report.get('detected_at')}",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def write_task_result_for(path: Path) -> None:
    try:
        from result_writer import write_task_result
    except Exception:
        return
    try:
        write_task_result(path, log_path=LOGS_DIR / f"{path.stem}.log")
    except Exception:
        return


def move_stuck_task(task_info: dict) -> dict | None:
    source = Path(task_info["task_file"])
    if not source.exists() or task_info.get("target_queue") not in {"failed", "needs-human"}:
        return None

    task = read_json(source, {})
    report = task_info["watchdog_report"]
    queue_name = task_info["target_queue"]
    report["target_queue"] = queue_name
    task["watchdog_report"] = report
    task["watchdog_reason"] = ", ".join(report.get("issues", [])) or "watchdog"
    task["watchdog_detected_at"] = report.get("detected_at")
    if queue_name == "failed":
        task["status"] = "failed"
        task["failure_reason"] = f"watchdog:{task['watchdog_reason']}"
    else:
        task["status"] = "needs-human"
        task["human_gate"] = "watchdog"
        task["human_reason"] = f"watchdog: {task['watchdog_reason']}"
        task["human_updated_at"] = report.get("detected_at")

    target = QUEUE_DIR / queue_name / source.name
    write_json(target, task)
    source.unlink(missing_ok=True)
    append_watchdog_log(task_info["task_id"], report, queue_name)
    finish_task(task_info["task_id"], queue_name, task["status"], role=task_info.get("role", ""))
    write_task_result_for(target)
    return task


def compute_summary(apply_actions: bool = False) -> dict:
    ensure_dirs()
    ts = now_ts()
    workers = [build_worker_status(role, ts=ts) for role in expected_worker_roles()]
    running_tasks = []
    stuck_tasks = []
    for path in sorted((QUEUE_DIR / "running").glob("*.json")):
        info = analyze_running_task(path, ts=ts)
        running_tasks.append(info)
        if info["is_stuck"]:
            stuck_tasks.append(info)
            if apply_actions:
                move_stuck_task(info)

    if apply_actions:
        running_tasks = [analyze_running_task(path, ts=ts) for path in sorted((QUEUE_DIR / "running").glob("*.json"))]
        stuck_tasks = [item for item in running_tasks if item["is_stuck"]]

    summary = {
        "generated_at": fmt_ts(ts),
        "generated_epoch": ts,
        "workers": workers,
        "running_tasks": running_tasks,
        "stuck_tasks": stuck_tasks,
        "counts": {
            "worker_alerts": sum(1 for item in workers if item["is_alert"]),
            "running_tasks": len(running_tasks),
            "stuck_tasks": len(stuck_tasks),
        },
    }
    write_json(SUMMARY_PATH, summary)
    return summary


def main(argv: list[str]) -> int:
    ensure_dirs()
    if len(argv) < 2:
        print("usage: watchdog.py <heartbeat-worker|start-task|heartbeat-task|finish-task|reconcile|summary> ...", file=sys.stderr)
        return 1

    command = argv[1]
    if command == "heartbeat-worker":
        if len(argv) < 3:
            return 1
        role = argv[2]
        status = argv[3] if len(argv) >= 4 else "idle"
        current_task_id = argv[4] if len(argv) >= 5 else ""
        session_name = argv[5] if len(argv) >= 6 else ""
        loop_pid = argv[6] if len(argv) >= 7 else None
        print(json.dumps(heartbeat_worker(role, status=status, current_task_id=current_task_id, session_name=session_name, loop_pid=loop_pid), ensure_ascii=False))
        return 0
    if command == "start-task":
        if len(argv) < 3:
            return 1
        task_file = Path(argv[2])
        log_path = argv[3] if len(argv) >= 4 else ""
        session_name = argv[4] if len(argv) >= 5 else ""
        loop_pid = argv[5] if len(argv) >= 6 else None
        runner_pid = argv[6] if len(argv) >= 7 else None
        print(json.dumps(start_task(task_file, log_path=log_path, session_name=session_name, loop_pid=loop_pid, runner_pid=runner_pid), ensure_ascii=False))
        return 0
    if command == "heartbeat-task":
        if len(argv) < 3:
            return 1
        task_file = Path(argv[2])
        log_path = argv[3] if len(argv) >= 4 else ""
        session_name = argv[4] if len(argv) >= 5 else ""
        loop_pid = argv[5] if len(argv) >= 6 else None
        runner_pid = argv[6] if len(argv) >= 7 else None
        print(json.dumps(heartbeat_task(task_file, log_path=log_path, session_name=session_name, loop_pid=loop_pid, runner_pid=runner_pid), ensure_ascii=False))
        return 0
    if command == "finish-task":
        if len(argv) < 5:
            return 1
        task_id = argv[2]
        final_queue = argv[3]
        final_status = argv[4]
        role = argv[5] if len(argv) >= 6 else ""
        print(json.dumps(finish_task(task_id, final_queue, final_status, role=role), ensure_ascii=False))
        return 0
    if command == "reconcile":
        print(json.dumps(compute_summary(apply_actions=True), ensure_ascii=False))
        return 0
    if command == "summary":
        print(json.dumps(compute_summary(apply_actions=False), ensure_ascii=False))
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
