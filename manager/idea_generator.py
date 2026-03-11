from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import re
import sys
import time

from post_task_learn import REPORT_PATH as LEARN_REPORT_PATH, load_post_task_learn_report

WORKFLOW_ROOT = Path.home() / "xtt-workflow"
MANAGER_DIR = WORKFLOW_ROOT / "manager"
RESULTS_DIR = MANAGER_DIR / "results"
STATE_DIR = MANAGER_DIR / "state"
IDEA_REPORT_PATH = STATE_DIR / "idea_backlog.json"

CATEGORY_LABELS = {
    "diff_sync": "验证链路",
    "repo_sync": "仓库同步",
    "test_baseline": "测试基线",
    "scope_control": "范围治理",
    "doc_only_tests": "验证质量",
    "runtime_env": "环境风险",
    "general": "工程质量",
}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default



def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip())



def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value).strip().lower())
    return cleaned.strip("-") or "idea"



def shorten(text: str, limit: int = 140) -> str:
    text = normalize_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_path_token(token: str) -> str:
    path = str(token).strip().strip("`'\"[](){}<>")
    if not path:
        return ""
    path = path.replace("\\", "/")
    path = re.sub(r":\d+(?::\d+)?$", "", path)
    path = re.sub(r"^.*/workspace/[^/]+/", "", path)
    path = re.sub(r"^.*/xtt-workflow/", "", path)
    path = re.sub(r"^\./", "", path)
    path = path.strip("/ ")
    if not path or re.search(r"\s", path):
        return ""
    if path.startswith(("origin/", "feat/", "fix/", "review/", "verify/", "chore/")):
        return ""
    basename = path.rsplit("/", 1)[-1]
    if basename.startswith(".") and basename not in {".gitignore", ".env", ".env.example", ".env.sample"}:
        return ""
    if not re.search(r"\.[A-Za-z0-9]+$", basename) and basename not in {"Makefile", "Dockerfile", "Procfile"}:
        return ""
    return path


def extract_paths_from_text(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return []
    candidates = re.findall(r"`([^`\n]+)`", text)
    candidates.extend(re.findall(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]+(?::\d+(?::\d+)?)?", text))
    paths = []
    seen = set()
    for candidate in candidates:
        path = normalize_path_token(candidate)
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths



def text_list(result: dict, key: str) -> list[str]:
    value = result.get(key)
    if isinstance(value, list):
        return [normalize_text(item) for item in value if normalize_text(item)]
    if isinstance(value, str) and normalize_text(value):
        return [normalize_text(value)]
    return []



def load_results(results_dir: Path = RESULTS_DIR) -> list[dict]:
    rows = []
    for path in sorted(results_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = read_json(path, {})
        if not payload:
            continue
        payload["_result_file"] = str(path)
        rows.append(payload)
    return rows



def family_evidence_tokens(results: list[dict]) -> dict[str, dict]:
    tokens: dict[str, dict] = defaultdict(lambda: {"count": 0, "evidence": [], "repos": Counter(), "branches": Counter(), "paths": Counter()})

    family_rules = {
        "diff_sync": ["wrong branch", "错误分支", "real diff", "git diff", "changed file set could not be resolved", "误判分支", "空 diff", "漏提交", "worktree", "sparse checkout"],
        "repo_sync": ["代码库不完整", "仓库内容不匹配", "真实代码树", "没有源码", "没有任何业务代码", "provide the correct code tree", "可运行实现"],
        "test_baseline": ["无测试", "没有测试", "测试不足", "覆盖面很窄", "验证入口", "运行入口", "smoke test", "cannot evaluate real functionality", "无法进一步验证"],
        "scope_control": ["wide_scope", "cross-layer", "cross layer", "跨层", "untouched modules"],
        "doc_only_tests": ["readme", "固定文本", "文案", "documentation", "doc-only"],
        "runtime_env": ["runtime", "dependency", "env", "环境", "部署"],
    }

    signal_fields = [
        "summary",
        "residual_risks",
        "required_validation_actions",
        "high_risk_residuals",
        "risk_signals",
        "review_findings",
        "missing_tests",
        "rule_conflicts",
    ]

    for result in results:
        repo = normalize_text(result.get("repo", "")) or "repo-main"
        base_branch = normalize_text(result.get("base_branch", "")) or "main"

        raw_paths = []
        for field in ("changed_files", "similar_impl_paths"):
            raw_paths.extend(text_list(result, field))
        for field in ("pass_paths", "fail_paths", "summary", "residual_risks", "required_validation_actions", "high_risk_residuals"):
            for line in text_list(result, field):
                raw_paths.extend(extract_paths_from_text(line))

        paths = []
        seen_paths = set()
        for raw_path in raw_paths:
            candidate = normalize_path_token(raw_path)
            if not candidate or candidate in seen_paths:
                continue
            seen_paths.add(candidate)
            paths.append(candidate)

        raw_items = []
        for field in signal_fields:
            raw_items.extend(text_list(result, field))

        task_ref = normalize_text(result.get("task_id", "") or result.get("_result_file", ""))
        for raw in raw_items:
            normalized = raw.lower()
            for family, keywords in family_rules.items():
                if any(keyword in normalized for keyword in keywords):
                    tokens[family]["count"] += 1
                    tokens[family]["repos"][repo] += 1
                    tokens[family]["branches"][base_branch] += 1
                    for candidate in paths:
                        tokens[family]["paths"][candidate] += 1
                    if len(tokens[family]["evidence"]) < 10:
                        snippet = shorten(raw)
                        if task_ref:
                            snippet = f"{snippet} · {task_ref}"
                        if snippet not in tokens[family]["evidence"]:
                            tokens[family]["evidence"].append(snippet)

    return dict(tokens)



def infer_repo_branch(results: list[dict], learn_report: dict, family_data: dict) -> tuple[str, str]:
    repo_counter = Counter()
    branch_counter = Counter()
    for result in results:
        repo = normalize_text(result.get("repo", ""))
        base_branch = normalize_text(result.get("base_branch", ""))
        if repo:
            repo_counter[repo] += 1
        if base_branch:
            branch_counter[(repo or "repo-main", base_branch)] += 1

    for payload in family_data.values():
        repo_counter.update(payload.get("repos", {}))

    report_repo_counts = learn_report.get("report_window", {}).get("repo_counts", {})
    if isinstance(report_repo_counts, dict):
        for repo, count in report_repo_counts.items():
            repo_counter[str(repo)] += int(count)

    repo = repo_counter.most_common(1)[0][0] if repo_counter else "repo-main"
    branch = "main"
    branch_hits = Counter()
    for (branch_repo, base_branch), count in branch_counter.items():
        if branch_repo == repo:
            branch_hits[base_branch] += count
    if branch_hits:
        branch = branch_hits.most_common(1)[0][0]
    return repo, branch



def family_priority(count: int) -> str:
    if count >= 6:
        return "high"
    if count >= 3:
        return "medium"
    return "low"



def family_risk(count: int) -> str:
    if count >= 5:
        return "high"
    if count >= 2:
        return "medium"
    return "low"



def family_task_kind(family: str) -> str:
    return {
        "diff_sync": "infra",
        "repo_sync": "infra",
        "test_baseline": "infra",
        "scope_control": "refactor",
        "doc_only_tests": "refactor",
        "runtime_env": "infra",
    }.get(family, "feature")



def family_title(family: str) -> str:
    return {
        "diff_sync": "修复 verify 前的分支 / diff 一致性检查",
        "repo_sync": "补齐 workspace 仓库同步与真实代码树校验",
        "test_baseline": "建立项目最小 smoke test 与统一测试入口",
        "scope_control": "为跨层改动补分层验证和串行策略",
        "doc_only_tests": "把文档级校验升级为行为级验证",
        "runtime_env": "补环境风险与依赖变更的专项验证",
    }.get(family, "基于历史问题补一轮工程治理")



def family_goal(family: str) -> str:
    return {
        "diff_sync": "降低 verify 在错误分支、空 diff、漏提交场景下误判“可用”的概率。",
        "repo_sync": "在任务开始前尽早发现仓库内容不完整、工作树不对或代码树未同步的问题。",
        "test_baseline": "为当前项目建立可稳定复用的最小验证入口，避免每次都只能做静态判断。",
        "scope_control": "把跨层改动拆成更可验证的层次，减少大范围回归盲区。",
        "doc_only_tests": "让验证结果从文案存在性升级到更贴近真实行为的证据。",
        "runtime_env": "把运行时 / 依赖 / 环境风险纳入固定验证流程。",
    }.get(family, "基于历史任务沉淀输出一个可执行治理任务。")



def family_acceptance(family: str) -> list[str]:
    mapping = {
        "diff_sync": [
            "verify 前会检查真实 diff 是否存在",
            "错误分支 / 空 diff / changed_files 无法解析时不会直接给出可用结论",
            "结果里能看到结构化 diff 可信度证据",
        ],
        "repo_sync": [
            "任务开始前能发现仓库未同步、代码树缺失或工作树错误",
            "风险会在 build 早期被结构化输出",
            "无真实代码树时不会继续进入无意义实现",
        ],
        "test_baseline": [
            "项目存在最小 smoke test 或统一测试入口",
            "builder / verifier 能复用同一套最小验证命令",
            "结果里能看到结构化测试命令和状态",
        ],
        "scope_control": [
            "跨层改动会被识别并标记更严格验证要求",
            "建议串行化或拆任务时有明确规则",
            "结果里能输出分层验证建议",
        ],
        "doc_only_tests": [
            "不再只靠 README / 文案存在性判断功能可用",
            "至少补一个更接近真实行为的验证点",
            "结果里能解释文档级验证与行为级验证的差异",
        ],
        "runtime_env": [
            "环境 / 依赖风险会产出明确验证动作",
            "高风险环境改动不会被默认轻量放过",
            "结果里能保留专项环境验证证据",
        ],
    }
    return mapping.get(family, ["输出一个可执行且可验证的治理改动"])



def family_allowed_paths(family: str, family_payload: dict) -> list[str]:
    preferred = []
    path_counter = family_payload.get("paths", {})
    if isinstance(path_counter, Counter):
        candidates = path_counter.most_common(8)
    elif isinstance(path_counter, dict):
        candidates = Counter(path_counter).most_common(8)
    else:
        candidates = []

    ignored = {"AGENTS.md"}
    for path, _ in candidates:
        candidate = normalize_path_token(path)
        if not candidate or candidate in ignored:
            continue
        if candidate.startswith("__pycache__/"):
            continue
        preferred.append(candidate)

    defaults = {
        "diff_sync": ["manager/", "config/", "scripts/"],
        "repo_sync": ["manager/", "config/", "workspace/"],
        "test_baseline": ["tests/", "manager/", "README.md"],
        "scope_control": ["manager/", "config/"],
        "doc_only_tests": ["tests/", "README.md"],
        "runtime_env": ["config/", "manager/", "scripts/"],
    }
    for item in defaults.get(family, ["manager/"]):
        if item not in preferred:
            preferred.append(item)
    return preferred[:8]



def idea_score(count: int, evidence_count: int, module_count: int) -> int:
    return count * 10 + evidence_count * 3 + module_count * 2



def build_family_ideas(results: list[dict], learn_report: dict) -> list[dict]:
    family_data = family_evidence_tokens(results)
    repo, base_branch = infer_repo_branch(results, learn_report, family_data)
    hotspot_modules = learn_report.get("hotspot_modules", []) if isinstance(learn_report.get("hotspot_modules"), list) else []
    hotspot_by_module = {str(item.get("module", "")).strip(): item for item in hotspot_modules if isinstance(item, dict)}

    ideas = []
    for family_item in learn_report.get("issue_families", []):
        if not isinstance(family_item, dict):
            continue
        family = normalize_text(family_item.get("key", ""))
        count = int(family_item.get("count", 0) or 0)
        if not family or count <= 0:
            continue

        payload = family_data.get(family, {})
        evidence = payload.get("evidence", [])[:5] if isinstance(payload.get("evidence"), list) else []
        allowed_paths = family_allowed_paths(family, payload)
        related_modules = []
        ignored_modules = {"README.md", "AGENTS.md", ".gitignore", "__pycache__/"}
        for item in hotspot_modules:
            if not isinstance(item, dict):
                continue
            module = normalize_text(item.get("module", ""))
            if not module or module in ignored_modules:
                continue
            if any(path.startswith(module.rstrip("/")) or module.startswith(path.rstrip("/")) for path in allowed_paths):
                related_modules.append(module)
            if len(related_modules) >= 4:
                break
        if not related_modules:
            for item in hotspot_modules:
                module = normalize_text(item.get("module", ""))
                if module and module not in ignored_modules:
                    related_modules = [module]
                    break
        related_modules = [item for item in related_modules if item]

        score = idea_score(count, len(evidence), len(related_modules))
        title = family_title(family)
        idea_id = f"idea-{slugify(family)}"
        why_now = f"历史结果里“{CATEGORY_LABELS.get(family, family)}”相关信号出现 {count} 次，已经不是一次性的偶发问题。"
        if related_modules:
            why_now += f" 当前更相关的热点区域包括：{', '.join(related_modules[:3])}。"

        acceptance = family_acceptance(family)
        outputs_hint = [
            "输出 changed files / summary / verification / risks",
            "给出结构化历史证据引用与修复前后差异",
            "补充对应的 targeted validation 命令",
        ]

        ideas.append(
            {
                "id": idea_id,
                "title": title,
                "category": family,
                "category_label": CATEGORY_LABELS.get(family, family),
                "priority": family_priority(count),
                "score": score,
                "task_kind": family_task_kind(family),
                "risk_level": family_risk(count),
                "repo": repo,
                "base_branch": base_branch,
                "goal": family_goal(family),
                "why_now": why_now,
                "acceptance": acceptance,
                "allowed_paths": allowed_paths,
                "related_modules": related_modules,
                "evidence": evidence,
                "source_signals": [family],
                "source_counts": {family: count},
                "outputs_hint": outputs_hint,
                "change_budget": {"max_files": 12, "max_lines": 260} if family in {"diff_sync", "repo_sync", "test_baseline"} else {"max_files": 16, "max_lines": 320},
            }
        )

    return ideas



def build_hotspot_ideas(learn_report: dict, default_repo: str, default_branch: str) -> list[dict]:
    hotspot_modules = learn_report.get("hotspot_modules", []) if isinstance(learn_report.get("hotspot_modules"), list) else []
    ideas = []
    ignored_modules = {"README.md", "AGENTS.md", ".gitignore", "__pycache__/"}
    for item in hotspot_modules[:6]:
        if not isinstance(item, dict):
            continue
        module = normalize_text(item.get("module", ""))
        if not module or module in ignored_modules:
            continue
        score = int(item.get("score", 0) or 0)
        if score <= 1:
            continue
        idea_id = f"idea-hotspot-{slugify(module)}"
        ideas.append(
            {
                "id": idea_id,
                "title": f"复盘热点模块 {module} 的历史改动与验证盲区",
                "category": "general",
                "category_label": CATEGORY_LABELS["general"],
                "priority": "medium" if score < 8 else "high",
                "score": score * 4,
                "task_kind": "refactor",
                "risk_level": "medium" if score < 8 else "high",
                "repo": default_repo,
                "base_branch": default_branch,
                "goal": f"针对热点模块 {module} 梳理真实改动模式、常见失败点和应补验证。",
                "why_now": f"该模块在历史结果中的聚合得分为 {score}，属于当前最频繁被提及的区域。",
                "acceptance": [
                    f"总结 {module} 的主要变更入口与验证入口",
                    "输出至少一条与真实历史证据绑定的改造建议",
                    "补最小验证策略，而不是泛泛 brainstorm",
                ],
                "allowed_paths": [module],
                "related_modules": [module],
                "evidence": [f"Hotspot score={score}", *[f"Path: {path}" for path in item.get("example_paths", [])[:3]]],
                "source_signals": ["hotspot"],
                "source_counts": {"hotspot_score": score},
                "outputs_hint": [
                    "输出热点模块的历史模式总结",
                    "输出下一条最有价值的小任务建议",
                    "输出建议补测点",
                ],
                "change_budget": {"max_files": 10, "max_lines": 220},
            }
        )
    return ideas[:3]



def dedupe_and_rank_ideas(ideas: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    priority_order = {"high": 0, "medium": 1, "low": 2}

    for item in ideas:
        title = normalize_text(item.get("title", ""))
        if not title:
            continue
        current = merged.get(title)
        if current is None:
            merged[title] = item
            continue
        current["score"] = max(int(current.get("score", 0)), int(item.get("score", 0)))
        current["evidence"] = list(dict.fromkeys(current.get("evidence", []) + item.get("evidence", [])))[:6]
        current["related_modules"] = list(dict.fromkeys(current.get("related_modules", []) + item.get("related_modules", [])))[:6]
        current["allowed_paths"] = list(dict.fromkeys(current.get("allowed_paths", []) + item.get("allowed_paths", [])))[:8]
        current["source_signals"] = list(dict.fromkeys(current.get("source_signals", []) + item.get("source_signals", [])))
        current["priority"] = sorted([current.get("priority", "low"), item.get("priority", "low")], key=lambda value: priority_order.get(value, 9))[0]

    ordered = list(merged.values())
    ordered.sort(key=lambda item: (priority_order.get(item.get("priority", "low"), 9), -int(item.get("score", 0) or 0), item.get("title", "")))
    return ordered[:10]



def build_report(results_dir: Path = RESULTS_DIR, learn_report_path: Path = LEARN_REPORT_PATH, output_path: Path | None = IDEA_REPORT_PATH) -> dict:
    results = load_results(results_dir)
    learn_report = load_post_task_learn_report(report_path=learn_report_path, build_if_missing=True)
    default_repo, default_branch = infer_repo_branch(results, learn_report, {})

    ideas = build_family_ideas(results, learn_report)
    ideas.extend(build_hotspot_ideas(learn_report, default_repo, default_branch))
    ideas = dedupe_and_rank_ideas(ideas)

    for index, item in enumerate(ideas, start=1):
        item["rank"] = index
        item["fingerprint"] = hashlib.sha256(json.dumps({
            "title": item.get("title"),
            "repo": item.get("repo"),
            "base_branch": item.get("base_branch"),
            "source_signals": item.get("source_signals"),
            "allowed_paths": item.get("allowed_paths"),
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    report = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "results_dir": str(results_dir),
        "learn_report_path": str(learn_report_path),
        "report_path": str(output_path) if output_path else "",
        "summary": {
            "idea_count": len(ideas),
            "top_priority_count": sum(1 for item in ideas if item.get("priority") == "high"),
            "repo": default_repo,
            "base_branch": default_branch,
        },
        "ideas": ideas,
    }

    if output_path:
        write_json(output_path, report)
    return report



def load_idea_backlog_report(report_path: Path = IDEA_REPORT_PATH, build_if_missing: bool = True) -> dict:
    payload = read_json(report_path, {})
    if payload:
        return payload
    if build_if_missing:
        return build_report(output_path=report_path)
    return {}



def refresh_idea_backlog_report(results_dir: Path = RESULTS_DIR, learn_report_path: Path = LEARN_REPORT_PATH, output_path: Path = IDEA_REPORT_PATH, strict: bool = False) -> dict:
    try:
        return build_report(results_dir=results_dir, learn_report_path=learn_report_path, output_path=output_path)
    except Exception:
        if strict:
            raise
        return {}



def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "build":
        results_dir = Path(argv[2]) if len(argv) >= 3 else RESULTS_DIR
        output_path = Path(argv[3]) if len(argv) >= 4 else IDEA_REPORT_PATH
        report = build_report(results_dir=results_dir, output_path=output_path)
        print(json.dumps({
            "generated_at": report["generated_at"],
            "idea_count": report["summary"]["idea_count"],
            "output_path": str(output_path),
        }, ensure_ascii=False))
        return 0

    print("usage: idea_generator.py build [results_dir] [output_path]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
