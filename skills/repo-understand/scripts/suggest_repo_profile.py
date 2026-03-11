#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

KNOWN_ENV_FILES = [
    ".env.example",
    ".env.sample",
    ".env.local.example",
    "env.example",
    "env.sample",
]
KNOWN_RUNTIME_FILES = [
    ".python-version",
    ".nvmrc",
    ".node-version",
    ".tool-versions",
    "runtime.txt",
]
KNOWN_PACKAGE_MANAGER_FILES = [
    "package.json",
    "pnpm-workspace.yaml",
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    "turbo.json",
]
KNOWN_DEPENDENCY_FILES = [
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "Pipfile",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
]
KNOWN_LOCKFILES = [
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
]
SKIP_SCAN_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}

KNOWN_HIGH_RISK_NAMES = [
    ".github",
    "deploy",
    "docker",
    "infra",
    "k8s",
    "helm",
    "terraform",
    "migrations",
    "migration",
    "alembic",
    "prisma",
    "db",
    "database",
    "config",
    "ops",
    "scripts",
]
FRONTEND_FRAMEWORKS = {
    "next": "nextjs",
    "nuxt": "nuxt",
    "react": "react",
    "react-dom": "react",
    "vue": "vue",
    "@angular/core": "angular",
    "svelte": "svelte",
    "@sveltejs/kit": "sveltekit",
    "vite": "vite",
}
PYTHON_FRAMEWORKS = {
    "fastapi": "python-fastapi",
    "django": "python-django",
    "flask": "python-flask",
}


def dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def safe_repo_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-") or "repo"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def existing_relpaths(repo_path: Path, candidates: list[str]) -> list[str]:
    return [item for item in candidates if (repo_path / item).exists()]


def top_level_dirs(repo_path: Path) -> list[str]:
    items = []
    for child in sorted(repo_path.iterdir(), key=lambda item: item.name):
        if child.is_dir() and child.name not in {".git", ".venv", "venv", "node_modules", ".next", "dist", "build"}:
            items.append(child.name)
    return items


def discover_named_paths(repo_path: Path, names: list[str], max_depth: int = 3) -> list[str]:
    wanted = set(names)
    found: list[str] = []
    for current_root, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [name for name in dirnames if name not in SKIP_SCAN_DIRS]
        current = Path(current_root)
        depth = len(current.relative_to(repo_path).parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        entries = [*dirnames, *filenames]
        for entry in entries:
            if entry in wanted:
                rel = (current / entry).relative_to(repo_path).as_posix()
                found.append(rel)
    return dedupe(found)


def run_git(repo_path: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return result.stdout.strip()


def git_default_branch(repo_path: Path) -> str:
    head = run_git(repo_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head.startswith("origin/"):
        return head.removeprefix("origin/")

    remote_branches = [
        line.removeprefix("origin/")
        for line in run_git(repo_path, "branch", "-r", "--format=%(refname:short)").splitlines()
        if line.startswith("origin/") and line not in {"origin/HEAD"}
    ]
    for preferred in ("main", "master", "develop"):
        if preferred in remote_branches:
            return preferred
    if remote_branches:
        return sorted(remote_branches)[0]

    local_branches = [line.strip().lstrip("*").strip() for line in run_git(repo_path, "branch", "--format=%(refname:short)").splitlines() if line.strip()]
    for preferred in ("main", "master", "develop"):
        if preferred in local_branches:
            return preferred
    return local_branches[0] if local_branches else "main"


def parse_make_targets(repo_path: Path) -> set[str]:
    targets: set[str] = set()
    makefile = repo_path / "Makefile"
    if not makefile.exists():
        return targets
    for line in read_text(makefile).splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+):", line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith("."):
            continue
        targets.add(target)
    return targets


def package_manager(repo_path: Path, has_package_json: bool) -> str:
    if (repo_path / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo_path / "yarn.lock").exists():
        return "yarn"
    if (repo_path / "bun.lockb").exists() or (repo_path / "bun.lock").exists():
        return "bun"
    if has_package_json:
        return "npm"
    return ""


def package_script_command(pm: str, script: str) -> str:
    if pm == "npm":
        return f"npm run {script}"
    if pm == "pnpm":
        return f"pnpm run {script}"
    if pm == "yarn":
        return f"yarn {script}"
    if pm == "bun":
        return f"bun run {script}"
    return script


def choose_script_command(scripts: dict, pm: str, names: list[str]) -> str:
    for name in names:
        if name in scripts and str(scripts.get(name, "")).strip():
            return package_script_command(pm, name)
    return ""


def parse_python_dependencies(repo_path: Path) -> set[str]:
    deps: set[str] = set()
    pyproject = read_toml(repo_path / "pyproject.toml")
    project = pyproject.get("project", {}) if isinstance(pyproject.get("project"), dict) else {}
    for item in project.get("dependencies", []) if isinstance(project.get("dependencies"), list) else []:
        name = re.split(r"[<>=!~ \[]", str(item), maxsplit=1)[0].strip().lower()
        if name:
            deps.add(name)

    tool = pyproject.get("tool", {}) if isinstance(pyproject.get("tool"), dict) else {}
    poetry = tool.get("poetry", {}) if isinstance(tool.get("poetry"), dict) else {}
    poetry_deps = poetry.get("dependencies", {}) if isinstance(poetry.get("dependencies"), dict) else {}
    deps.update(str(name).strip().lower() for name in poetry_deps.keys() if str(name).strip().lower() != "python")

    for requirements_name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
        requirements = repo_path / requirements_name
        if not requirements.exists():
            continue
        for line in read_text(requirements).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            name = re.split(r"[<>=!~ \[]", stripped, maxsplit=1)[0].strip().lower()
            if name:
                deps.add(name)
    return deps


def detect_stack(repo_path: Path, package: dict, python_deps: set[str]) -> tuple[str, list[str]]:
    markers: list[str] = []
    package_deps = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        payload = package.get(key, {}) if isinstance(package.get(key), dict) else {}
        package_deps.update(str(name).strip() for name in payload.keys())

    for dependency, stack in FRONTEND_FRAMEWORKS.items():
        if dependency in package_deps:
            markers.append(dependency)
            return stack, markers

    if package_deps:
        if any(dep in package_deps for dep in {"express", "koa", "fastify", "nest"}):
            markers.extend(sorted(dep for dep in package_deps if dep in {"express", "koa", "fastify", "nest"}))
            return "node", markers
        markers.extend(sorted(package_deps)[:5])
        return "node", markers

    for dependency, stack in PYTHON_FRAMEWORKS.items():
        if dependency in python_deps:
            markers.append(dependency)
            return stack, markers
    if python_deps:
        markers.extend(sorted(python_deps)[:5])
        return "python", markers
    if (repo_path / "go.mod").exists():
        return "go", ["go.mod"]
    if (repo_path / "Cargo.toml").exists():
        return "rust", ["Cargo.toml"]
    return "generic", []


def has_frontend_surface(repo_path: Path, stack: str, package: dict) -> bool:
    if stack in {"nextjs", "react", "vue", "nuxt", "angular", "svelte", "sveltekit", "vite"}:
        return True
    package_deps = set()
    for key in ("dependencies", "devDependencies"):
        payload = package.get(key, {}) if isinstance(package.get(key), dict) else {}
        package_deps.update(str(name).strip() for name in payload.keys())
    if package_deps & {"playwright", "cypress", "storybook", "@playwright/test"}:
        return True
    return any((repo_path / item).exists() for item in ("pages", "public", "components", "src/components", "templates", "static"))


def detect_commands(repo_path: Path, stack: str, package: dict) -> dict[str, str]:
    commands = {
        "install_cmd": "",
        "lint_cmd": "",
        "test_cmd": "",
        "build_cmd": "",
        "smoke_test_cmd": "",
        "targeted_test_cmd": "",
        "extended_test_cmd": "",
    }
    make_targets = parse_make_targets(repo_path)
    package_json_exists = bool(package)
    pm = package_manager(repo_path, package_json_exists)
    scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}

    if pm:
        install_map = {
            "npm": "npm install",
            "pnpm": "pnpm install",
            "yarn": "yarn install",
            "bun": "bun install",
        }
        commands["install_cmd"] = install_map.get(pm, "")
        commands["lint_cmd"] = choose_script_command(scripts, pm, ["lint", "check"])
        commands["build_cmd"] = choose_script_command(scripts, pm, ["build", "compile"])
        commands["test_cmd"] = choose_script_command(scripts, pm, ["test", "test:unit", "unit", "check"])
        commands["targeted_test_cmd"] = choose_script_command(scripts, pm, ["test:unit", "unit", "test:integration", "integration", "test"])
        commands["smoke_test_cmd"] = choose_script_command(scripts, pm, ["test:smoke", "smoke", "test:e2e", "e2e", "playwright", "test"])
        commands["extended_test_cmd"] = choose_script_command(scripts, pm, ["test:ci", "ci:test", "test:all", "test:e2e", "test"])
        for key in ("targeted_test_cmd", "smoke_test_cmd", "extended_test_cmd"):
            if not commands[key]:
                commands[key] = commands["test_cmd"]
        return commands

    pyproject = read_toml(repo_path / "pyproject.toml")
    python_deps = parse_python_dependencies(repo_path)
    if pyproject or python_deps or any((repo_path / name).exists() for name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt")):
        tool = pyproject.get("tool", {}) if isinstance(pyproject.get("tool"), dict) else {}
        if (repo_path / "uv.lock").exists() or isinstance(tool.get("uv"), dict):
            commands["install_cmd"] = "uv sync"
        elif (repo_path / "poetry.lock").exists() or isinstance(tool.get("poetry"), dict):
            commands["install_cmd"] = "poetry install"
        elif (repo_path / "requirements.txt").exists():
            commands["install_cmd"] = "python3 -m pip install -r requirements.txt"
        if "ruff" in python_deps or isinstance(tool.get("ruff"), dict):
            commands["lint_cmd"] = "ruff check ."
        elif "lint" in make_targets:
            commands["lint_cmd"] = "make lint"
        pytest_signal = any((repo_path / name).exists() for name in ("pytest.ini", "conftest.py")) or "pytest" in python_deps or (repo_path / "tests").exists()
        if pytest_signal:
            commands["test_cmd"] = "pytest -q"
        elif "test" in make_targets:
            commands["test_cmd"] = "make test"
        else:
            commands["test_cmd"] = "python3 -m unittest discover -v"
        commands["targeted_test_cmd"] = commands["test_cmd"]
        commands["smoke_test_cmd"] = commands["test_cmd"]
        commands["extended_test_cmd"] = commands["test_cmd"]
        if pyproject.get("build-system"):
            commands["build_cmd"] = "python3 -m build"
        elif "build" in make_targets:
            commands["build_cmd"] = "make build"
        return commands

    if (repo_path / "go.mod").exists():
        commands["test_cmd"] = "go test ./..."
        commands["targeted_test_cmd"] = "go test ./..."
        commands["smoke_test_cmd"] = "go test ./..."
        commands["extended_test_cmd"] = "go test ./..."
        commands["build_cmd"] = "go build ./..."
        if "lint" in make_targets:
            commands["lint_cmd"] = "make lint"
        return commands

    if (repo_path / "Cargo.toml").exists():
        commands["test_cmd"] = "cargo test"
        commands["targeted_test_cmd"] = "cargo test"
        commands["smoke_test_cmd"] = "cargo test"
        commands["extended_test_cmd"] = "cargo test"
        commands["build_cmd"] = "cargo build"
        commands["lint_cmd"] = "cargo fmt -- --check"
        return commands

    if "test" in make_targets:
        commands["test_cmd"] = "make test"
        commands["targeted_test_cmd"] = "make test"
        commands["smoke_test_cmd"] = "make test"
        commands["extended_test_cmd"] = "make test"
    if "lint" in make_targets:
        commands["lint_cmd"] = "make lint"
    if "build" in make_targets:
        commands["build_cmd"] = "make build"
    return commands


def suggested_tool_router(stack: str, needs_ui_evidence: bool, high_risk_paths: list[str], top_dirs: list[str], has_tests: bool) -> dict:
    read_first = [item for item in ["README.md", "docs/", *(f"{name}/" for name in top_dirs[:4]), "tests/" if has_tests else ""] if item]
    evidence_focus = ["structured result json"]
    if needs_ui_evidence:
        evidence_focus.append("ui evidence")
    if high_risk_paths:
        evidence_focus.append("risk-path evidence")
    risk_focus = []
    if high_risk_paths:
        risk_focus.append(f"review high-risk paths first: {', '.join(high_risk_paths[:5])}")
    if stack.startswith("python"):
        risk_focus.append("watch runtime and dependency drift")
    if stack in {"nextjs", "react", "vue", "nuxt", "angular", "svelte", "sveltekit", "vite"}:
        risk_focus.append("watch UI regressions and config drift")

    build_flow = ["inspect-current-behavior", "patch-minimal-surface", "run-targeted-validation", "capture-structured-result"]
    if needs_ui_evidence and "capture-ui-evidence" not in build_flow:
        build_flow.append("capture-ui-evidence")
    verify_flow = ["inspect-real-diff", "run-targeted-or-smoke-validation", "write-merge-decision-and-risks"]
    if needs_ui_evidence and "capture-ui-evidence" not in verify_flow:
        verify_flow.insert(2, "capture-ui-evidence")

    return {
        "read_first": dedupe(read_first),
        "run_first": [],
        "risk_focus": dedupe(risk_focus),
        "evidence_focus": dedupe(evidence_focus),
        "execution_order": [],
        "by_type": {
            "build": {"execution_order": dedupe(build_flow)},
            "review": {"execution_order": ["inspect-real-diff", "read-risk-files", "report-findings-only"]},
            "verify": {"execution_order": dedupe(verify_flow)},
        },
        "by_task_kind": {
            "infra": {
                "risk_focus": ["runtime / dependency / config drift"],
            }
        },
    }


def build_profile(repo_path: Path, repo_name: str) -> dict:
    package = read_json(repo_path / "package.json")
    python_deps = parse_python_dependencies(repo_path)
    stack, stack_markers = detect_stack(repo_path, package, python_deps)
    commands = detect_commands(repo_path, stack, package)
    top_dirs = top_level_dirs(repo_path)
    package_files = existing_relpaths(repo_path, KNOWN_PACKAGE_MANAGER_FILES)
    dependency_files = existing_relpaths(repo_path, KNOWN_DEPENDENCY_FILES)
    lockfiles = existing_relpaths(repo_path, KNOWN_LOCKFILES)
    env_files = existing_relpaths(repo_path, KNOWN_ENV_FILES)
    runtime_files = existing_relpaths(repo_path, KNOWN_RUNTIME_FILES)
    env_paths = dedupe(existing_relpaths(repo_path, ["env", "config/env", "config/runtime", "config"]))
    high_risk_paths = dedupe(existing_relpaths(repo_path, [".github/workflows", "deploy", "docker", "infra", "k8s", "helm", "terraform", "migrations", "alembic", "prisma", "db", "config"]))
    high_risk_paths.extend(discover_named_paths(repo_path, KNOWN_HIGH_RISK_NAMES, max_depth=3))
    high_risk_paths = dedupe(high_risk_paths)
    needs_ui_evidence = has_frontend_surface(repo_path, stack, package)
    has_tests = any((repo_path / item).exists() for item in ("tests", "test", "spec", "cypress", "playwright"))
    default_branch = git_default_branch(repo_path)

    profile = {
        "version": 1,
        "stack": stack,
        "default_branch": default_branch,
        "install_cmd": commands["install_cmd"],
        "lint_cmd": commands["lint_cmd"],
        "test_cmd": commands["test_cmd"],
        "build_cmd": commands["build_cmd"],
        "smoke_test_cmd": commands["smoke_test_cmd"],
        "targeted_test_cmd": commands["targeted_test_cmd"],
        "extended_test_cmd": commands["extended_test_cmd"],
        "high_risk_paths": high_risk_paths,
        "forbidden_paths": [".git", ".env", "secrets", "node_modules", "dist", "build"],
        "needs_ui_evidence": needs_ui_evidence,
        "dependency_files": dependency_files,
        "lockfile_files": lockfiles,
        "environment_paths": env_paths,
        "environment_files": env_files,
        "runtime_files": runtime_files,
        "package_manager_files": package_files,
        "tool_router": suggested_tool_router(stack, needs_ui_evidence, high_risk_paths, top_dirs, has_tests),
    }

    scripts = sorted((package.get("scripts", {}) or {}).keys()) if isinstance(package.get("scripts"), dict) else []
    notes = []
    if not profile["test_cmd"]:
        notes.append("No obvious test command detected; test_cmd is blank")
    if not profile["build_cmd"]:
        notes.append("No obvious build command detected; build_cmd is blank")
    if stack == "generic":
        notes.append("Stack fell back to generic; review commands and risk paths manually")

    report = {
        "repo": repo_name,
        "repo_path": str(repo_path),
        "profile_path": f"config/repos/{repo_name}.json",
        "detected": {
            "stack": stack,
            "stack_markers": stack_markers,
            "top_level_dirs": top_dirs[:12],
            "package_scripts": scripts[:20],
            "notes": notes,
        },
        "profile": profile,
    }
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Suggest an xtt-workflow repo profile from a local repo path")
    parser.add_argument("repo_path", help="Path to the local Git repo")
    parser.add_argument("--repo-name", default="", help="Repo name used for config/repos/<repo>.json")
    parser.add_argument("--write-profile", default="", help="Write the suggested profile JSON to this path")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_path = Path(args.repo_path).expanduser().resolve()
    if not repo_path.is_dir():
        print(f"repo path not found: {repo_path}", file=sys.stderr)
        return 2
    if not (repo_path / ".git").exists():
        print(f"not a git repo: {repo_path}", file=sys.stderr)
        return 2

    repo_name = safe_repo_name(args.repo_name or repo_path.name)
    report = build_profile(repo_path, repo_name)

    if args.write_profile:
        output_path = Path(args.write_profile).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report["profile"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote profile: {output_path}", file=sys.stderr)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
