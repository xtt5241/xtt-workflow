from __future__ import annotations

from pathlib import Path
import fnmatch
import os
import shutil
import subprocess
import sys

DIR_PATTERNS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".nox",
    "htmlcov",
    "coverage",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".parcel-cache",
    ".turbo",
    "dist",
    "build",
    "tmp",
    "temp",
}

FILE_PATTERNS = {
    "*.pyc",
    "*.pyo",
    ".coverage",
    "coverage.xml",
    ".DS_Store",
    "*.tmp",
    "*.temp",
    "*.tsbuildinfo",
    ".eslintcache",
}

TRACKED_SAFE_PATTERNS = {
    "*.pyc",
    "*.pyo",
    ".coverage",
    "coverage.xml",
    ".DS_Store",
}

GITIGNORE_DOC = "docs/gitignore_baseline.md"


def run_git(repo_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=check,
        capture_output=True,
        text=False,
    )


def path_exists(repo_path: Path, relative_path: str) -> bool:
    return (repo_path / relative_path).exists()


def iter_null_sep(stdout: bytes) -> list[str]:
    return [item.decode("utf-8", errors="ignore") for item in stdout.split(b"\0") if item]


def matches_file_pattern(path: str, patterns: set[str]) -> bool:
    name = Path(path).name
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def matching_dir_target(path: str) -> str | None:
    parts = Path(path).parts
    for index, part in enumerate(parts):
        if part in DIR_PATTERNS:
            return str(Path(*parts[: index + 1]))
    return None


def classify_artifact(path: str) -> tuple[str, str] | None:
    dir_target = matching_dir_target(path)
    if dir_target:
        return ("dir", dir_target)
    if matches_file_pattern(path, FILE_PATTERNS):
        return ("file", path)
    return None


def remove_path(repo_path: Path, relative_path: str) -> bool:
    target = repo_path / relative_path
    if not target.exists():
        return False
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink()
    return True


def clean_untracked(repo_path: Path) -> int:
    proc = run_git(repo_path, "ls-files", "--others", "--exclude-standard", "-z")
    removed: list[str] = []
    seen: set[str] = set()
    for path in iter_null_sep(proc.stdout):
        match = classify_artifact(path)
        if not match:
            continue
        _, target = match
        if target in seen:
            continue
        if remove_path(repo_path, target):
            removed.append(target)
            seen.add(target)

    if removed:
        print(f"builder hygiene: removed {len(removed)} untracked artifacts")
        for item in sorted(removed):
            print(f"- removed: {item}")
    else:
        print("builder hygiene: no untracked artifacts removed")
    print(f"builder hygiene: gitignore baseline suggestion -> {GITIGNORE_DOC}")
    return 0


def file_tracked_in_head(repo_path: Path, relative_path: str) -> bool:
    return run_git(repo_path, "cat-file", "-e", f"HEAD:{relative_path}", check=False).returncode == 0


def restore_or_remove_staged(repo_path: Path, relative_path: str) -> None:
    if file_tracked_in_head(repo_path, relative_path):
        subprocess.run(["git", "-C", str(repo_path), "restore", "--staged", "--worktree", "--", relative_path], check=False)
        return

    subprocess.run(["git", "-C", str(repo_path), "reset", "HEAD", "--", relative_path], check=False, capture_output=True)
    remove_path(repo_path, relative_path)


def drop_staged_artifacts(repo_path: Path) -> int:
    proc = run_git(repo_path, "diff", "--cached", "--name-only", "-z")
    removed: list[str] = []
    seen: set[str] = set()

    for path in iter_null_sep(proc.stdout):
        dir_target = matching_dir_target(path)
        if dir_target:
            target = dir_target
        elif matches_file_pattern(path, FILE_PATTERNS | TRACKED_SAFE_PATTERNS):
            target = path
        else:
            continue

        if target in seen:
            continue
        restore_or_remove_staged(repo_path, target)
        removed.append(target)
        seen.add(target)

    if removed:
        print(f"builder hygiene: dropped {len(removed)} staged artifacts")
        for item in sorted(removed):
            print(f"- dropped from commit: {item}")
    else:
        print("builder hygiene: no staged artifacts dropped")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"clean-untracked", "drop-staged"}:
        print("usage: builder_hygiene.py <clean-untracked|drop-staged> <repo_path>", file=sys.stderr)
        return 2

    repo_path = Path(argv[2]).resolve()
    if not (repo_path / ".git").exists():
        print(f"not a git worktree: {repo_path}", file=sys.stderr)
        return 2

    if argv[1] == "clean-untracked":
        return clean_untracked(repo_path)
    return drop_staged_artifacts(repo_path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
