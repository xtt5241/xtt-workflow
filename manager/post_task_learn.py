from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import re
import sys
import time

WORKFLOW_ROOT = Path.home() / "xtt-workflow"
MANAGER_DIR = WORKFLOW_ROOT / "manager"
RESULTS_DIR = MANAGER_DIR / "results"
STATE_DIR = MANAGER_DIR / "state"
REPORT_PATH = STATE_DIR / "post_task_learn.json"

PATH_FIELD_WEIGHTS = {
    "changed_files": 4,
    "fail_paths": 3,
    "pass_paths": 2,
    "similar_impl_paths": 1,
    "summary": 1,
    "residual_risks": 1,
    "required_validation_actions": 1,
    "high_risk_residuals": 1,
}

ISSUE_FIELDS = [
    "review_findings",
    "rule_conflicts",
    "risk_signals",
    "required_validation_actions",
    "high_risk_residuals",
    "residual_risks",
]

DEBT_FIELDS = ["high_risk_residuals", "residual_risks"]

SIGNAL_LABELS = {
    "diff_sync": "分支 / diff 一致性",
    "test_baseline": "测试基线不足",
    "repo_sync": "仓库同步 / 真实代码树",
    "scope_control": "跨层范围控制",
    "doc_only_tests": "文档级校验过多",
    "runtime_env": "运行时 / 环境风险",
}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default



def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def text_list(result: dict, key: str) -> list[str]:
    value = result.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []



def shorten(text: str, limit: int = 150) -> str:
    text = re.sub(r"\s+", " ", str(text).strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"



def normalize_issue_text(text: str) -> str:
    normalized = str(text).strip()
    if not normalized:
        return ""
    normalized = re.sub(r"`[^`]+`", "<code>", normalized)
    normalized = re.sub(r"\btask-\d+\b", "task-*", normalized)
    normalized = re.sub(r"\bmanual-direct-[a-z0-9-]+\b", "manual-direct-*", normalized)
    normalized = re.sub(r"\b(?:feat|fix|review|verify|chore)/[A-Za-z0-9._/-]+\b", "<branch>", normalized)
    normalized = re.sub(r"\b\d{6,}\b", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower().strip(" .;，。")



def normalize_path_token(token: str) -> str:
    path = str(token).strip().strip("`'\"[](){}<>")
    if not path:
        return ""
    path = path.replace("\\", "/")
    if path.startswith(("http://", "https://")):
        return ""
    path = re.sub(r":\d+(?::\d+)?$", "", path)
    path = re.sub(r"^.*/workspace/[^/]+/", "", path)
    path = re.sub(r"^.*/xtt-workflow/", "", path)
    path = re.sub(r"^\./", "", path)
    path = path.strip("/ ")
    if not path:
        return ""
    if re.search(r"\s", path):
        return ""
    if path in {"none", "null", "true", "false"}:
        return ""
    basename = path.rsplit("/", 1)[-1]
    if basename.startswith(".") and basename not in {".gitignore", ".env", ".env.example", ".env.sample"}:
        return ""
    if path.startswith(("origin/", "feat/", "fix/", "review/", "verify/", "chore/")):
        return ""
    if not re.search(r"\.[A-Za-z0-9]+$", basename) and basename not in {"Makefile", "Dockerfile", "Procfile"}:
        return ""
    return path



def extract_paths_from_text(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return []

    found: list[str] = []
    candidates = re.findall(r"`([^`\n]+)`", text)
    candidates.extend(re.findall(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]+(?::\d+(?::\d+)?)?", text))
    seen = set()
    for candidate in candidates:
        path = normalize_path_token(candidate)
        if not path or path in seen:
            continue
        seen.add(path)
        found.append(path)
    return found



def module_key(path: str) -> str:
    parts = [part for part in str(path).split("/") if part and part not in {".", ".."}]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    top = parts[0]
    if top in {"src", "app", "packages", "services", "modules"} and len(parts) >= 2:
        return f"{top}/{parts[1]}"
    return f"{top}/"



def classify_signal_families(text: str) -> set[str]:
    normalized = normalize_issue_text(text)
    if not normalized:
        return set()

    families = set()
    if any(keyword in normalized for keyword in [
        "wrong branch",
        "错误分支",
        "提交错分支",
        "real diff",
        "git diff",
        "changed file set could not be resolved",
        "漏提交",
        "worktree",
        "sparse checkout",
        "空 diff",
        "误判分支",
    ]):
        families.add("diff_sync")

    if any(keyword in normalized for keyword in [
        "无测试",
        "没有测试",
        "测试不足",
        "覆盖面很窄",
        "验证入口",
        "运行入口",
        "smoke test",
        "test baseline",
        "test entry",
        "无法进一步验证",
        "cannot evaluate real functionality",
    ]):
        families.add("test_baseline")

    if any(keyword in normalized for keyword in [
        "代码库不完整",
        "仓库内容不匹配",
        "真实代码树",
        "没有源码",
        "没有任何业务代码",
        "可运行实现",
        "输入代码库不完整",
        "provide the correct code tree",
        "repo not found",
    ]):
        families.add("repo_sync")

    if any(keyword in normalized for keyword in [
        "wide_scope",
        "cross-layer",
        "cross layer",
        "跨层",
        "untouched modules",
    ]):
        families.add("scope_control")

    if any(keyword in normalized for keyword in [
        "readme",
        "固定文本",
        "文案",
        "doc-only",
        "documentation",
    ]):
        families.add("doc_only_tests")

    if any(keyword in normalized for keyword in [
        "runtime",
        "dependency",
        "env",
        "环境",
        "部署",
    ]):
        families.add("runtime_env")

    return families



def should_skip_issue_item(text: str) -> bool:
    normalized = normalize_issue_text(text)
    if not normalized:
        return True
    if re.match(r"^\d+ files changed(?:, .*insertions?\(\+\))?$", normalized):
        return True
    if normalized.startswith("builder: no changes to commit"):
        return True
    if normalized.startswith(("create mode ", "delete mode ", "rename ", "mode change ")):
        return True
    if normalized.startswith("[") and "builder:" in normalized:
        return True
    return False


def should_skip_debt_item(text: str) -> bool:
    normalized = normalize_issue_text(text)
    if should_skip_issue_item(text):
        return True
    if normalized.startswith("builder: no changes to commit"):
        return True
    return False



def issue_severity(count: int) -> str:
    if count >= 5:
        return "high"
    if count >= 3:
        return "medium"
    return "low"



def load_results(results_dir: Path = RESULTS_DIR) -> list[dict]:
    rows = []
    for path in sorted(results_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = read_json(path, {})
        if not payload:
            continue
        payload["_result_file"] = str(path)
        rows.append(payload)
    return rows



def aggregate_hotspots(results: list[dict]) -> tuple[list[dict], dict]:
    hotspots: dict[str, dict] = {}
    direct_path_hits = 0

    for result in results:
        seen_paths: dict[str, tuple[str, int]] = {}
        for field, weight in PATH_FIELD_WEIGHTS.items():
            values: list[str] = []
            if field in {"summary", "residual_risks", "required_validation_actions", "high_risk_residuals", "pass_paths", "fail_paths"}:
                raw_items = text_list(result, field)
                for raw in raw_items:
                    values.extend(extract_paths_from_text(raw))
            else:
                values = [normalize_path_token(item) for item in text_list(result, field)]

            for value in values:
                path = normalize_path_token(value)
                if not path:
                    continue
                current = seen_paths.get(path)
                if current is None or weight > current[1]:
                    seen_paths[path] = (field, weight)
                if field in {"changed_files", "pass_paths", "fail_paths", "similar_impl_paths"}:
                    direct_path_hits += 1

        for path, (field, weight) in seen_paths.items():
            key = module_key(path)
            if not key:
                continue
            bucket = hotspots.setdefault(
                key,
                {
                    "module": key,
                    "score": 0,
                    "result_files": set(),
                    "sources": set(),
                    "example_paths": [],
                },
            )
            bucket["score"] += weight
            bucket["result_files"].add(result.get("task_id") or result.get("_result_file"))
            bucket["sources"].add(field)
            if path not in bucket["example_paths"]:
                bucket["example_paths"].append(path)

    items = []
    for item in hotspots.values():
        items.append(
            {
                "module": item["module"],
                "score": item["score"],
                "result_count": len(item["result_files"]),
                "source_fields": sorted(item["sources"]),
                "example_paths": item["example_paths"][:4],
                "confidence": "high" if any(field == "changed_files" for field in item["sources"]) else ("medium" if any(field in {"pass_paths", "fail_paths", "similar_impl_paths"} for field in item["sources"]) else "low"),
            }
        )

    items.sort(key=lambda value: (-value["score"], -value["result_count"], value["module"]))
    stats = {
        "direct_path_hits": direct_path_hits,
        "hotspot_count": len(items),
    }
    return items[:10], stats



def aggregate_issues(results: list[dict]) -> tuple[list[dict], Counter]:
    issues: dict[str, dict] = {}
    signal_families: Counter = Counter()

    for result in results:
        task_ref = str(result.get("task_id") or result.get("_result_file") or "").strip()
        for field in ISSUE_FIELDS:
            for raw in text_list(result, field):
                normalized = normalize_issue_text(raw)
                if should_skip_issue_item(raw) or not normalized:
                    continue
                bucket = issues.setdefault(
                    normalized,
                    {
                        "label": shorten(raw),
                        "count": 0,
                        "source_fields": set(),
                        "task_ids": set(),
                    },
                )
                bucket["count"] += 1
                bucket["source_fields"].add(field)
                if task_ref:
                    bucket["task_ids"].add(task_ref)
                for family in classify_signal_families(raw):
                    signal_families[family] += 1

    items = []
    for _, item in issues.items():
        items.append(
            {
                "label": item["label"],
                "count": item["count"],
                "severity": issue_severity(item["count"]),
                "source_fields": sorted(item["source_fields"]),
                "task_count": len(item["task_ids"]),
            }
        )
    items.sort(key=lambda value: (-value["count"], -value["task_count"], value["label"]))
    return items[:12], signal_families



def aggregate_technical_debt(results: list[dict]) -> list[dict]:
    debt: dict[str, dict] = {}

    for result in results:
        task_ref = str(result.get("task_id") or result.get("_result_file") or "").strip()
        for field in DEBT_FIELDS:
            for raw in text_list(result, field):
                if should_skip_debt_item(raw):
                    continue
                normalized = normalize_issue_text(raw)
                if should_skip_issue_item(raw) or not normalized:
                    continue
                bucket = debt.setdefault(
                    normalized,
                    {
                        "label": shorten(raw),
                        "count": 0,
                        "source_fields": set(),
                        "task_ids": set(),
                    },
                )
                bucket["count"] += 1
                bucket["source_fields"].add(field)
                if task_ref:
                    bucket["task_ids"].add(task_ref)

    items = []
    for _, item in debt.items():
        items.append(
            {
                "label": item["label"],
                "count": item["count"],
                "severity": issue_severity(item["count"]),
                "source_fields": sorted(item["source_fields"]),
                "task_count": len(item["task_ids"]),
            }
        )
    items.sort(key=lambda value: (-value["count"], -value["task_count"], value["label"]))
    return items[:12]



def aggregate_test_points(results: list[dict], signal_families: Counter) -> list[dict]:
    suggestions: dict[str, dict] = {}

    def add(title: str, reason: str, weight: int = 1, signal: str = "") -> None:
        bucket = suggestions.setdefault(
            title,
            {
                "title": title,
                "count": 0,
                "reason": reason,
                "signals": set(),
            },
        )
        bucket["count"] += weight
        if signal:
            bucket["signals"].add(signal)

    for result in results:
        for raw in text_list(result, "missing_tests"):
            add(shorten(raw, limit=120), "来自结构化 missing_tests 字段", weight=2, signal="missing_tests")
        for raw in text_list(result, "residual_risks") + text_list(result, "high_risk_residuals") + text_list(result, "required_validation_actions"):
            families = classify_signal_families(raw)
            if "diff_sync" in families:
                add("补 verify 前分支 / diff 一致性检查", "多次出现错误分支、空 diff 或 changed_files 无法解析的风险", signal="diff_sync")
            if "test_baseline" in families:
                add("建立最小 smoke test 与统一测试入口", "多次提到无测试、覆盖过窄或没有运行入口", signal="test_baseline")
            if "scope_control" in families:
                add("为跨层改动补分层验证", "wide scope / cross-layer 风险需要 targeted test + smoke 组合", signal="scope_control")
            normalized = normalize_issue_text(raw)
            if "固定文本" in normalized or "readme" in normalized:
                add("把 README / 文案校验升级为更稳的行为验证", "当前多处验证仍偏向静态文本存在性", signal="doc_only_tests")

    for family, count in signal_families.items():
        if family == "diff_sync" and count:
            add("补 verify 前分支 / diff 一致性检查", "历史结果里反复出现 diff / branch 可信度问题", weight=count, signal=family)
        if family == "test_baseline" and count:
            add("建立最小 smoke test 与统一测试入口", "历史结果里反复出现无测试或覆盖不足", weight=count, signal=family)
        if family == "scope_control" and count:
            add("为跨层改动补分层验证", "跨层风险需要更清晰的分层验证策略", weight=count, signal=family)
        if family == "doc_only_tests" and count:
            add("把 README / 文案校验升级为更稳的行为验证", "当前任务容易停留在文案或文档级校验", weight=count, signal=family)

    items = []
    for item in suggestions.values():
        items.append(
            {
                "title": item["title"],
                "count": item["count"],
                "reason": item["reason"],
                "signals": sorted(item["signals"]),
            }
        )
    items.sort(key=lambda value: (-value["count"], value["title"]))
    return items[:10]



def build_next_task_suggestions(signal_families: Counter, hotspots: list[dict], test_points: list[dict], issue_items: list[dict]) -> list[dict]:
    suggestions = []

    def actionable_hotspot() -> dict | None:
        ignored_modules = {"README.md", "AGENTS.md", ".gitignore", "__pycache__/"}
        for item in hotspots:
            module = str(item.get("module", "")).strip()
            if not module or module in ignored_modules:
                continue
            if module.endswith(".md") and module != "README.md":
                continue
            return item
        return None

    def add(title: str, reason: str, priority: str, signals: list[str]) -> None:
        suggestions.append(
            {
                "title": title,
                "reason": reason,
                "priority": priority,
                "signals": signals,
            }
        )

    if signal_families["diff_sync"]:
        add(
            "修复 worktree / 分支 / diff 识别链路",
            f"历史结果里有 {signal_families['diff_sync']} 条相关信号，verify 容易在错误分支或空 diff 上给出结论。",
            "high" if signal_families["diff_sync"] >= 3 else "medium",
            ["diff_sync"],
        )

    if signal_families["repo_sync"]:
        add(
            "补齐 workspace 仓库同步与真实代码树校验",
            f"历史结果里有 {signal_families['repo_sync']} 条信号提到仓库内容不完整或任务输入与代码树不匹配。",
            "high" if signal_families["repo_sync"] >= 3 else "medium",
            ["repo_sync"],
        )

    if signal_families["test_baseline"]:
        add(
            "建立项目最小测试基线",
            f"历史结果里有 {signal_families['test_baseline']} 条信号提到无测试、覆盖过窄或没有统一验证入口。",
            "high" if signal_families["test_baseline"] >= 3 else "medium",
            ["test_baseline"],
        )

    top_hotspot = actionable_hotspot()
    if top_hotspot:
        add(
            f"优先复盘热点模块 {top_hotspot['module']}",
            f"该模块/路径聚合得分较高（score={top_hotspot['score']}），适合先梳理变更与验证模式。",
            "medium",
            ["hotspot"],
        )

    if test_points:
        top = test_points[0]
        add(
            f"围绕“{top['title']}”补一轮测试",
            f"这是当前最频繁的测试缺口建议（count={top['count']}）。",
            "medium",
            top.get("signals", []),
        )

    if issue_items:
        top_issue = issue_items[0]
        add(
            "先解决最频繁的结构化问题",
            f"当前最常见的问题是“{top_issue['label']}”（count={top_issue['count']}）。",
            "medium",
            top_issue.get("source_fields", []),
        )

    deduped = []
    seen = set()
    for item in suggestions:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        deduped.append(item)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    deduped.sort(key=lambda value: (priority_order.get(value["priority"], 3), value["title"]))
    return deduped[:6]



def build_low_signal_notes(results: list[dict], hotspot_stats: dict, signal_families: Counter) -> list[str]:
    notes = []
    total_changed_files = sum(len(text_list(result, "changed_files")) for result in results)
    total_missing_tests = sum(len(text_list(result, "missing_tests")) for result in results)

    if not results:
        notes.append("暂无历史结果，先跑完至少一轮 build / review / verify，再生成经验总结。")
        return notes

    if total_changed_files == 0:
        notes.append("历史结果里的 `changed_files` 目前几乎为空；热点模块更多依赖 summary / risk 文本里的路径线索，置信度偏低。")

    if hotspot_stats.get("direct_path_hits", 0) == 0:
        notes.append("当前没有直接的 changed/pass/fail 路径证据，说明结果抽取链路还需要补强。")

    if total_missing_tests == 0 and signal_families.get("test_baseline"):
        notes.append("历史结果很少产出结构化 `missing_tests`，建议在 review 阶段把测试缺口写得更结构化。")

    return notes



def build_report(results_dir: Path = RESULTS_DIR, output_path: Path | None = REPORT_PATH) -> dict:
    results = load_results(results_dir)
    stage_counts = Counter(str(item.get("stage", "")).strip() or "unknown" for item in results)
    queue_counts = Counter(str(item.get("queue", "")).strip() or "unknown" for item in results)
    repo_counts = Counter(str(item.get("repo", "")).strip() or "unknown" for item in results)
    written_times = [str(item.get("written_at", "")).strip() for item in results if str(item.get("written_at", "")).strip()]

    hotspots, hotspot_stats = aggregate_hotspots(results)
    issue_items, signal_families = aggregate_issues(results)
    technical_debt = aggregate_technical_debt(results)
    test_points = aggregate_test_points(results, signal_families)
    next_tasks = build_next_task_suggestions(signal_families, hotspots, test_points, issue_items)
    low_signal_notes = build_low_signal_notes(results, hotspot_stats, signal_families)

    signal_family_items = [
        {
            "key": key,
            "label": SIGNAL_LABELS.get(key, key),
            "count": count,
        }
        for key, count in sorted(signal_families.items(), key=lambda item: (-item[1], item[0]))
    ]

    report = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "results_dir": str(results_dir),
        "report_path": str(output_path) if output_path else "",
        "report_window": {
            "total_results": len(results),
            "stage_counts": dict(stage_counts),
            "queue_counts": dict(queue_counts),
            "repo_counts": dict(repo_counts),
            "earliest_written_at": min(written_times) if written_times else "",
            "latest_written_at": max(written_times) if written_times else "",
        },
        "issue_families": signal_family_items,
        "hotspot_modules": hotspots,
        "frequent_issues": issue_items,
        "technical_debt": technical_debt,
        "suggested_test_points": test_points,
        "next_task_suggestions": next_tasks,
        "low_signal_notes": low_signal_notes,
    }

    if output_path:
        write_json(output_path, report)
    return report



def load_post_task_learn_report(report_path: Path = REPORT_PATH, build_if_missing: bool = True) -> dict:
    payload = read_json(report_path, {})
    if payload:
        return payload
    if build_if_missing:
        return build_report(output_path=report_path)
    return {}



def refresh_post_task_learn_report(results_dir: Path = RESULTS_DIR, output_path: Path = REPORT_PATH, strict: bool = False) -> dict:
    try:
        return build_report(results_dir=results_dir, output_path=output_path)
    except Exception:
        if strict:
            raise
        return {}



def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "build":
        results_dir = Path(argv[2]) if len(argv) >= 3 else RESULTS_DIR
        output_path = Path(argv[3]) if len(argv) >= 4 else REPORT_PATH
        report = build_report(results_dir=results_dir, output_path=output_path)
        print(json.dumps({
            "generated_at": report["generated_at"],
            "total_results": report["report_window"]["total_results"],
            "output_path": str(output_path),
        }, ensure_ascii=False))
        return 0

    print("usage: post_task_learn.py build [results_dir] [output_path]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
