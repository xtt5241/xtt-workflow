from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

from task_boundary import DEPLOY_PATH_FILES, DEPLOY_PATH_PREFIXES, MIGRATION_PREFIXES, path_matches_prefix

AUTH_KEYWORDS = (
    'auth', 'login', 'session', 'token', 'oauth', 'permission', 'rbac', 'acl', 'credential', 'passwd', 'password'
)


def run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(repo_path), *args], check=True, capture_output=True, text=True)


def staged_paths(repo_path: Path) -> list[str]:
    output = run_git(repo_path, 'diff', '--cached', '--name-only').stdout
    return [line.strip() for line in output.splitlines() if line.strip()]


def staged_numstat(repo_path: Path) -> list[tuple[str, str, str]]:
    output = run_git(repo_path, 'diff', '--cached', '--numstat').stdout
    rows = []
    for line in output.splitlines():
        parts = line.split('\t', 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def budget_label(max_files: int) -> str:
    if max_files <= 3:
        return 'small'
    if max_files <= 8:
        return 'medium'
    return 'large'


def auth_related_paths(paths: list[str]) -> list[str]:
    hits = []
    for path in paths:
        lowered = path.lower()
        if any(keyword in lowered for keyword in AUTH_KEYWORDS):
            hits.append(path)
    return hits


def deploy_related_paths(paths: list[str]) -> list[str]:
    return [
        path for path in paths
        if path_matches_prefix(path, DEPLOY_PATH_PREFIXES) or Path(path).name in DEPLOY_PATH_FILES
    ]


def migration_related_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if path_matches_prefix(path, MIGRATION_PREFIXES)]


def build_report(task: dict, repo_path: Path) -> dict:
    changed_paths = staged_paths(repo_path)
    numstat = staged_numstat(repo_path)
    binary_paths: list[str] = []
    added_lines = 0
    deleted_lines = 0

    for added, deleted, path in numstat:
        if added == '-' or deleted == '-':
            binary_paths.append(path)
            continue
        added_lines += int(added)
        deleted_lines += int(deleted)

    change_budget = task.get('change_budget', {}) or {}
    max_files = int(change_budget.get('max_files', 0) or 0)
    max_lines = int(change_budget.get('max_lines', 0) or 0)
    total_lines = added_lines + deleted_lines

    reasons: list[str] = []
    if max_files and len(changed_paths) > max_files:
        reasons.append(f'changed files {len(changed_paths)} exceed budget max_files={max_files}')
    if max_lines and total_lines > max_lines:
        reasons.append(f'changed lines {total_lines} exceed budget max_lines={max_lines}')
    if binary_paths:
        reasons.append('binary or non-numstat changes require human review: ' + ', '.join(binary_paths))

    auth_hits = auth_related_paths(changed_paths)
    if auth_hits:
        reasons.append('auth-related files changed: ' + ', '.join(auth_hits))

    deploy_hits = deploy_related_paths(changed_paths)
    if deploy_hits:
        reasons.append('deploy-related files changed: ' + ', '.join(deploy_hits))

    migration_hits = migration_related_paths(changed_paths)
    if migration_hits:
        reasons.append('migration-related files changed: ' + ', '.join(migration_hits))

    retry_count = int(task.get('retry_count', 0) or 0)
    if retry_count >= 2:
        reasons.append(f'retry_count={retry_count} reached human handoff threshold')

    if str(task.get('risk_level', '')).lower() == 'high':
        reasons.append('task risk_level=high requires human review')

    requires_human = bool(reasons)
    summary = 'within budget' if not requires_human else '; '.join(reasons)

    return {
        'budget_label': budget_label(max_files),
        'max_files': max_files,
        'max_lines': max_lines,
        'files_changed_count': len(changed_paths),
        'changed_paths': changed_paths,
        'added_lines': added_lines,
        'deleted_lines': deleted_lines,
        'total_lines': total_lines,
        'binary_paths': binary_paths,
        'requires_human': requires_human,
        'reasons': reasons,
        'summary': summary,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] != 'check-staged':
        print('usage: change_budget.py check-staged <task.json> <repo_path> <report.json>', file=sys.stderr)
        return 2

    task = json.loads(Path(argv[2]).read_text(encoding='utf-8'))
    repo_path = Path(argv[3]).resolve()
    report_path = Path(argv[4]).resolve()

    report = build_report(task, repo_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"change budget: label={report['budget_label']} files={report['files_changed_count']}/{report['max_files']} lines={report['total_lines']}/{report['max_lines']}")
    for path in report['changed_paths']:
        print(f'- changed: {path}')
    if report['requires_human']:
        print('change budget: NEEDS_HUMAN')
        for reason in report['reasons']:
            print(f'- {reason}')
        return 3

    print('change budget: PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
