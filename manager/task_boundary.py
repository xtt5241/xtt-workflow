from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

DEPENDENCY_FILES = {
    'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock',
    'requirements.txt', 'pyproject.toml', 'poetry.lock', 'Pipfile', 'Pipfile.lock',
    'Gemfile.lock', 'go.mod', 'go.sum', 'Cargo.toml', 'Cargo.lock',
}

CI_PATH_PREFIXES = [
    '.github/workflows/', '.circleci/', '.azure/', 'ci/', '.gitlab/',
]
CI_PATH_FILES = {'Jenkinsfile', '.gitlab-ci.yml', 'azure-pipelines.yml'}

DEPLOY_PATH_PREFIXES = [
    'deploy/', 'deployment/', 'k8s/', 'helm/', '.docker/', '.devcontainer/',
]
DEPLOY_PATH_FILES = {'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml'}

MIGRATION_PREFIXES = [
    'migrations/', 'migration/', 'alembic/', 'db/migrations/',
]

SOFT_LAYER_PREFIXES = [
    'frontend/', 'web/', 'ui/', 'backend/', 'api/', 'server/', 'manager/', 'scripts/', 'config/', 'docs/',
]


def iter_null_sep(stdout: bytes) -> list[str]:
    return [item.decode('utf-8', errors='ignore') for item in stdout.split(b'\0') if item]


def run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(repo_path), *args], check=True, capture_output=True, text=False)


def path_matches_prefix(path: str, prefixes: list[str]) -> bool:
    normalized = path.strip('/')
    for prefix in prefixes:
        cleaned = prefix.strip('/')
        if not cleaned:
            continue
        if normalized == cleaned or normalized.startswith(cleaned + '/'):
            return True
    return False


def staged_paths(repo_path: Path) -> list[str]:
    proc = run_git(repo_path, 'diff', '--cached', '--name-only', '-z')
    return iter_null_sep(proc.stdout)


def classify_soft_layers(paths: list[str]) -> set[str]:
    layers = set()
    for path in paths:
        matched = False
        for prefix in SOFT_LAYER_PREFIXES:
            if path_matches_prefix(path, [prefix]):
                layers.add(prefix.rstrip('/'))
                matched = True
                break
        if not matched:
            first = Path(path).parts[0] if Path(path).parts else path
            if first not in {'tests', 'test', 'README.md'}:
                layers.add(first)
    return layers


def check_boundaries(task: dict, repo_path: Path) -> tuple[list[str], list[str]]:
    changed = staged_paths(repo_path)
    violations: list[str] = []
    details: list[str] = []

    allowed_paths = task.get('allowed_paths', []) or []
    forbidden_paths = task.get('forbidden_paths', []) or []

    details.append(f'boundary check: staged paths={changed}')
    details.append(f'boundary check: allowed_paths={allowed_paths}')
    details.append(f'boundary check: forbidden_paths={forbidden_paths}')

    if allowed_paths:
        out_of_scope = [path for path in changed if not path_matches_prefix(path, allowed_paths)]
        if out_of_scope:
            violations.append('changed files exceed allowed_paths: ' + ', '.join(out_of_scope))

    forbidden_hits = [path for path in changed if path_matches_prefix(path, forbidden_paths)]
    if forbidden_hits:
        violations.append('changed files hit forbidden_paths: ' + ', '.join(forbidden_hits))

    if not task.get('allow_dependency_changes', False):
        dep_hits = [path for path in changed if Path(path).name in DEPENDENCY_FILES]
        if dep_hits:
            violations.append('dependency files changed without allow_dependency_changes: ' + ', '.join(dep_hits))

    if not task.get('allow_migration', False):
        migration_hits = [path for path in changed if path_matches_prefix(path, MIGRATION_PREFIXES)]
        if migration_hits:
            violations.append('migration files changed without allow_migration: ' + ', '.join(migration_hits))

    if not task.get('allow_ci_changes', False):
        ci_hits = [
            path for path in changed
            if path_matches_prefix(path, CI_PATH_PREFIXES) or Path(path).name in CI_PATH_FILES
        ]
        if ci_hits:
            violations.append('CI files changed without allow_ci_changes: ' + ', '.join(ci_hits))

    if not task.get('allow_deploy_changes', False):
        deploy_hits = [
            path for path in changed
            if path_matches_prefix(path, DEPLOY_PATH_PREFIXES) or Path(path).name in DEPLOY_PATH_FILES
        ]
        if deploy_hits:
            violations.append('deploy files changed without allow_deploy_changes: ' + ', '.join(deploy_hits))

    if allowed_paths:
        details.append('boundary check: skip cross-layer heuristic because allowed_paths is explicit')
    elif not task.get('allow_cross_layer_refactor', False):
        layers = classify_soft_layers(changed)
        details.append(f'boundary check: detected_layers={sorted(layers)}')
        if len(layers) > 1:
            violations.append('cross-layer refactor detected without allow_cross_layer_refactor: ' + ', '.join(sorted(layers)))

    return violations, details


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != 'check-staged':
        print('usage: task_boundary.py check-staged <task.json> <repo_path>', file=sys.stderr)
        return 2

    task = json.loads(Path(argv[2]).read_text(encoding='utf-8'))
    repo_path = Path(argv[3]).resolve()
    violations, details = check_boundaries(task, repo_path)
    for line in details:
        print(line)
    if violations:
        print('boundary check: FAILED')
        for line in violations:
            print(f'- {line}')
        return 1
    print('boundary check: PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
