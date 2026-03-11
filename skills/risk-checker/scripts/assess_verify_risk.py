#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANAGER_DIR = ROOT / 'manager'
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))

from change_risk import (  # type: ignore
    ENV_PATH_FILES,
    ENV_PATH_PREFIXES,
    LOCKFILE_FILES,
    PACKAGE_MANAGER_FILES,
    RUNTIME_FILES,
    validation_commands as change_validation_commands,
)
from repo_profile import load_repo_profile, normalize_repo_profile  # type: ignore
from task_boundary import (  # type: ignore
    CI_PATH_FILES,
    CI_PATH_PREFIXES,
    DEPENDENCY_FILES,
    DEPLOY_PATH_FILES,
    DEPLOY_PATH_PREFIXES,
    MIGRATION_PREFIXES,
    classify_soft_layers,
    path_matches_prefix,
)

RISK_ORDER = {'low': 0, 'medium': 1, 'high': 2}
FRONTEND_DIR_HINTS = {'components', 'pages', 'app', 'routes', 'views', 'templates', 'ui', 'public', 'static'}
FRONTEND_EXTS = {'.tsx', '.jsx', '.vue', '.svelte', '.css', '.scss', '.sass', '.less', '.html'}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def dedupe(items) -> list[str]:
    result = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(repo_path), *args], capture_output=True, text=False)


def ref_exists(repo_path: Path, ref: str) -> bool:
    if not str(ref).strip():
        return False
    proc = run_git(repo_path, 'rev-parse', '--verify', ref)
    return proc.returncode == 0


def resolve_base_ref(repo_path: Path, task: dict) -> str:
    base_branch = str(task.get('base_branch', 'main')).strip() or 'main'
    for candidate in (f'origin/{base_branch}', base_branch):
        if ref_exists(repo_path, candidate):
            return candidate
    return 'HEAD'


def resolve_diff_ref(repo_path: Path, task: dict) -> str:
    candidates = [
        str(task.get('source_ref', '')).strip(),
        str(task.get('branch', '')).strip(),
        'HEAD',
    ]
    for candidate in candidates:
        if ref_exists(repo_path, candidate):
            return candidate
    return 'HEAD'


def diff_paths(repo_path: Path, base_ref: str, diff_ref: str) -> list[str]:
    if base_ref == diff_ref == 'HEAD':
        return []
    proc = run_git(repo_path, 'diff', '--name-only', '-z', f'{base_ref}...{diff_ref}')
    if proc.returncode != 0:
        proc = run_git(repo_path, 'diff', '--name-only', '-z', diff_ref)
        if proc.returncode != 0:
            return []
    return [item.decode('utf-8', errors='ignore') for item in proc.stdout.split(b'\0') if item]


def max_risk(*levels: str) -> str:
    ordered = sorted((level for level in levels if level in RISK_ORDER), key=lambda item: RISK_ORDER[item])
    return ordered[-1] if ordered else 'low'


def category_signal(category: str, items: list[str], score: int, summary: str) -> dict:
    return {
        'category': category,
        'items': dedupe(items),
        'score': score,
        'summary': summary,
    }


def detect_ui_surface(paths: list[str], profile: dict) -> list[str]:
    if not profile.get('needs_ui_evidence'):
        return []
    hits = []
    for path in paths:
        parts = set(Path(path).parts)
        if parts & FRONTEND_DIR_HINTS or Path(path).suffix.lower() in FRONTEND_EXTS:
            hits.append(path)
    return dedupe(hits)


def detect_high_risk(task: dict, changed: list[str], profile: dict) -> list[dict]:
    dependency_files = {str(item).strip() for item in DEPENDENCY_FILES}
    dependency_files.update(str(item).strip() for item in profile.get('dependency_files', []) if str(item).strip())
    lockfile_files = set(LOCKFILE_FILES)
    lockfile_files.update(str(item).strip() for item in profile.get('lockfile_files', []) if str(item).strip())
    environment_prefixes = dedupe(list(ENV_PATH_PREFIXES) + [str(item).strip() for item in profile.get('environment_paths', []) if str(item).strip()])
    environment_files = set(ENV_PATH_FILES)
    environment_files.update(str(item).strip() for item in profile.get('environment_files', []) if str(item).strip())
    runtime_files = set(RUNTIME_FILES)
    runtime_files.update(str(item).strip() for item in profile.get('runtime_files', []) if str(item).strip())
    package_manager_files = set(PACKAGE_MANAGER_FILES)
    package_manager_files.update(str(item).strip() for item in profile.get('package_manager_files', []) if str(item).strip())
    high_risk_paths = [str(item).strip() for item in profile.get('high_risk_paths', []) if str(item).strip()]

    signals: list[dict] = []

    dependency_hits = [path for path in changed if Path(path).name in dependency_files]
    if dependency_hits:
        signals.append(category_signal('dependency', dependency_hits, 2, 'dependency manifests changed'))

    lockfile_hits = [path for path in changed if Path(path).name in lockfile_files]
    if lockfile_hits:
        signals.append(category_signal('lockfile', lockfile_hits, 2, 'lockfiles changed'))

    migration_hits = [path for path in changed if path_matches_prefix(path, MIGRATION_PREFIXES)]
    if migration_hits:
        signals.append(category_signal('migration', migration_hits, 4, 'migration paths changed'))

    environment_hits = [path for path in changed if path_matches_prefix(path, environment_prefixes) or Path(path).name in environment_files]
    if environment_hits:
        signals.append(category_signal('environment', environment_hits, 4, 'environment-sensitive files changed'))

    runtime_hits = [path for path in changed if Path(path).name in runtime_files]
    if runtime_hits:
        signals.append(category_signal('runtime', runtime_hits, 4, 'runtime selector files changed'))

    package_manager_hits = [path for path in changed if Path(path).name in package_manager_files]
    if package_manager_hits:
        signals.append(category_signal('package_manager', package_manager_hits, 3, 'package-manager config changed'))

    ci_hits = [path for path in changed if path_matches_prefix(path, CI_PATH_PREFIXES) or Path(path).name in CI_PATH_FILES]
    if ci_hits:
        signals.append(category_signal('ci', ci_hits, 3, 'CI files changed'))

    deploy_hits = [path for path in changed if path_matches_prefix(path, DEPLOY_PATH_PREFIXES) or Path(path).name in DEPLOY_PATH_FILES]
    if deploy_hits:
        signals.append(category_signal('deploy', deploy_hits, 3, 'deploy files changed'))

    profile_hits = [path for path in changed if path_matches_prefix(path, high_risk_paths)]
    if profile_hits:
        signals.append(category_signal('high_risk_path', profile_hits, 3, 'repo high-risk paths touched'))

    ui_hits = detect_ui_surface(changed, profile)
    if ui_hits:
        signals.append(category_signal('ui_surface', ui_hits, 2, 'user-facing surface changed'))

    change_budget = task.get('change_budget', {}) if isinstance(task.get('change_budget'), dict) else {}
    max_files = int(change_budget.get('max_files', 0) or 0)
    if max_files > 0 and len(changed) > max_files:
        signals.append(category_signal('budget_exceeded', changed, 2, f'changed files exceed budget ({len(changed)} > {max_files})'))

    layers = sorted(classify_soft_layers(changed)) if changed else []
    if len(layers) > 1 and not task.get('allow_cross_layer_refactor', False):
        signals.append(category_signal('wide_scope', layers, 2, 'multiple layers changed'))

    return signals


def required_actions(categories: list[str], profile: dict) -> list[str]:
    actions: list[str] = []
    for command in change_validation_commands(categories, profile):
        actions.append(f'Run validation command: {command}')

    if any(category in categories for category in {'dependency', 'lockfile', 'package_manager'}):
        actions.append('Verify install / dependency resolution outcome before merge')
    if any(category in categories for category in {'environment', 'runtime'}):
        actions.append('Verify runtime or environment-sensitive behavior explicitly, not only by static review')
    if 'migration' in categories:
        actions.append('Perform migration-specific verification or dry-run before approval')
    if any(category in categories for category in {'ci', 'deploy'}):
        actions.append('Inspect CI / deploy diff and verify pipeline-facing behavior intentionally')
    if 'high_risk_path' in categories:
        actions.append('Review changes under repo high-risk paths line by line')
    if 'ui_surface' in categories:
        actions.append('Capture UI evidence for the touched user-facing flow')
    if 'wide_scope' in categories:
        actions.append('Verify each affected layer separately before marking usable')
    if 'budget_exceeded' in categories:
        actions.append('Re-check whether the change should be decomposed before release')
    return dedupe(actions)


def residual_items(categories: list[str], profile: dict) -> list[str]:
    residuals: list[str] = []
    if any(category in categories for category in {'dependency', 'lockfile', 'package_manager'}):
        residuals.append('Transitive dependency effects may remain outside targeted tests')
    if any(category in categories for category in {'environment', 'runtime'}):
        residuals.append('Behavior may still differ across local / CI / production runtime environments')
    if 'migration' in categories:
        residuals.append('Schema or data compatibility may still need human judgment after automated checks')
    if any(category in categories for category in {'ci', 'deploy'}):
        residuals.append('Pipeline or deploy behavior may differ outside the local verification surface')
    if 'high_risk_path' in categories:
        residuals.append('Repo-marked high-risk paths need explicit human confidence beyond passing tests')
    if 'ui_surface' in categories and profile.get('needs_ui_evidence'):
        residuals.append('User-facing regressions may remain without manual path coverage')
    if 'wide_scope' in categories:
        residuals.append('Cross-layer interactions may still hide regressions across untouched modules')
    return dedupe(residuals)


def required_evidence(categories: list[str], profile: dict) -> list[str]:
    evidence = ['changed files', 'verification', 'risks']
    if any(category in categories for category in {'dependency', 'lockfile', 'package_manager'}):
        evidence.append('dependency validation')
    if any(category in categories for category in {'environment', 'runtime', 'migration', 'deploy', 'ci'}):
        evidence.append('manual steps')
    if 'ui_surface' in categories and profile.get('needs_ui_evidence'):
        evidence.append('ui evidence')
    evidence.append('residual risks')
    return dedupe(evidence)


def compute_risk_level(task: dict, categories: list[str], score: int) -> str:
    existing = str(task.get('risk_level', 'medium')).strip() or 'medium'
    if existing == 'high':
        return 'high'
    if any(category in categories for category in {'migration', 'environment', 'runtime', 'package_manager', 'deploy', 'ci'}):
        return 'high'
    if score >= 8:
        return 'high'
    if categories or score >= 3:
        return max_risk(existing, 'medium')
    return max_risk(existing, 'low')


def build_report(task: dict, repo_path: Path) -> dict:
    repo = str(task.get('repo', 'repo-main')).strip() or 'repo-main'
    profile = normalize_repo_profile(repo, task.get('repo_profile') or load_repo_profile(repo))
    base_ref = resolve_base_ref(repo_path, task)
    diff_ref = resolve_diff_ref(repo_path, task)
    changed = diff_paths(repo_path, base_ref, diff_ref)
    signals = detect_high_risk(task, changed, profile)
    categories = [item['category'] for item in signals]
    score = sum(int(item.get('score', 0) or 0) for item in signals)
    risk_level = compute_risk_level(task, categories, score)
    actions = required_actions(categories, profile)
    residuals = residual_items(categories, profile)
    evidence = required_evidence(categories, profile)

    if not changed:
        actions = dedupe(actions + ['Confirm the real diff is available before trusting the verify result'])
        residuals = dedupe(residuals + ['Changed file set could not be resolved from git diff'])

    summary_parts = [f'risk-check score={score}', f'level={risk_level}']
    if categories:
        summary_parts.append(f'categories={", ".join(categories)}')
    else:
        summary_parts.append('categories=none')
    summary = '; '.join(summary_parts)

    return {
        'report_type': 'risk-checker',
        'version': 1,
        'repo': repo,
        'base_ref': base_ref,
        'diff_ref': diff_ref,
        'changed_files': changed,
        'score': score,
        'risk_level': risk_level,
        'categories': categories,
        'signals': signals,
        'required_validation_actions': actions,
        'high_risk_residuals': residuals,
        'required_evidence': evidence,
        'summary': summary,
    }


def apply_report(task: dict, report: dict) -> dict:
    current_signals = [str(item).strip() for item in task.get('risk_signals', []) if str(item).strip()] if isinstance(task.get('risk_signals'), list) else []
    report_categories = [str(item).strip() for item in report.get('categories', []) if str(item).strip()]
    current_evidence = [str(item).strip() for item in task.get('evidence_required', []) if str(item).strip()] if isinstance(task.get('evidence_required'), list) else []
    current_risk = str(task.get('risk_level', 'medium')).strip() or 'medium'
    task['risk_level'] = max_risk(current_risk, str(report.get('risk_level', 'low')).strip())
    task['risk_signals'] = dedupe(current_signals + report_categories)
    task['evidence_required'] = dedupe(current_evidence + [str(item).strip() for item in report.get('required_evidence', []) if str(item).strip()])
    task['required_validation_actions'] = dedupe(report.get('required_validation_actions', []))
    task['high_risk_residuals'] = dedupe(report.get('high_risk_residuals', []))
    task['risk_check_summary'] = str(report.get('summary', '')).strip()
    task['risk_check_report'] = report
    return task


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Score verify-stage task risk and stricter validation actions')
    parser.add_argument('--repo-path', required=True, help='Repo path or worktree path used for git diff inspection')
    parser.add_argument('--task-json', required=True, help='Task json path')
    parser.add_argument('--write-task', action='store_true', help='Write report back into task json')
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_path = Path(args.repo_path).expanduser().resolve()
    task_path = Path(args.task_json).expanduser().resolve()
    if not repo_path.is_dir():
        print(f'repo path not found: {repo_path}', file=sys.stderr)
        return 2
    if not task_path.is_file():
        print(f'task json not found: {task_path}', file=sys.stderr)
        return 2

    task = read_json(task_path)
    report = build_report(task, repo_path)
    if args.write_task:
        task = apply_report(task, report)
        write_json(task_path, task)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
