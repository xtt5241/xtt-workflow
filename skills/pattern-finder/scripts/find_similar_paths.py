#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    '.git', '.hg', '.svn', '.venv', 'venv', '__pycache__', 'node_modules',
    'dist', 'build', 'coverage', '.next', '.nuxt', '.turbo', '.cache',
    'target', 'out', '.idea', '.vscode'
}
TEXT_EXTENSIONS = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.json', '.yml', '.yaml', '.toml', '.ini', '.cfg',
    '.md', '.txt', '.html', '.css', '.scss', '.sass', '.less', '.vue', '.svelte', '.go', '.rs', '.java',
    '.kt', '.rb', '.php', '.sh', '.sql', '.xml'
}
STOP_WORDS = {
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'into', 'need', 'needs', 'task', 'repo', 'main',
    'work', 'make', 'update', 'fix', 'add', 'use', 'using', 'build', 'review', 'verify', 'real', 'usable',
    'project', 'branch', 'base', 'allow', 'allowed', 'path', 'paths', 'code', 'file', 'files', 'result',
    'summary', 'test', 'tests', 'route', 'routes', 'page', 'pages', 'change', 'changes', 'current', 'support',
    'should', 'would', 'could', 'about', 'after', 'before', 'under', 'your', 'when', 'then', 'there', 'here',
    'true', 'false', 'json', 'done', 'gate', 'ready', 'push', 'release', 'deliver', 'feature', 'bugfix',
}
FRONTEND_HINT_DIRS = {'components', 'pages', 'app', 'routes', 'views', 'templates', 'ui'}
BACKEND_HINT_DIRS = {'api', 'server', 'handlers', 'services', 'controllers', 'models'}
TEST_HINT_NAMES = {'test', 'tests', 'spec', 'specs', '__tests__'}
MAX_FILE_SIZE = 200_000
MAX_CONTENT_CHARS = 40_000
MAX_CANDIDATES = 8


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def split_camel(value: str) -> str:
    return re.sub(r'(?<!^)([A-Z])', r' \1', value)


def tokenize_text(value: str) -> list[str]:
    normalized = split_camel(value)
    raw_tokens = re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}', normalized)
    tokens: list[str] = []
    for token in raw_tokens:
        pieces = re.split(r'[_-]+', token)
        for piece in pieces:
            lowered = piece.strip().lower()
            if len(lowered) < 3 or lowered in STOP_WORDS:
                continue
            tokens.append(lowered)
    return dedupe(tokens)


def extract_cjk_phrases(value: str) -> list[str]:
    phrases = []
    for match in re.findall(r'[\u4e00-\u9fff]{2,}', value or ''):
        if match not in phrases:
            phrases.append(match)
    return phrases


def collect_query_bits(task: dict) -> tuple[list[str], list[str], str]:
    fields = [
        str(task.get('title', '')).strip(),
        str(task.get('goal', '')).strip(),
        '\n'.join(str(item).strip() for item in task.get('acceptance', []) if str(item).strip()),
        '\n'.join(str(item).strip() for item in task.get('allowed_paths', []) if str(item).strip()),
    ]
    raw_query = '\n'.join(item for item in fields if item)
    keywords = tokenize_text(raw_query)

    path_tokens: list[str] = []
    for item in task.get('allowed_paths', []) if isinstance(task.get('allowed_paths'), list) else []:
        path_tokens.extend(tokenize_text(str(item)))
    keywords = dedupe(keywords + path_tokens)
    phrases = extract_cjk_phrases(raw_query)
    return keywords[:20], phrases[:8], raw_query


def should_scan(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    if path.name in {'Dockerfile', 'Makefile'}:
        return True
    return False


def read_text_snippet(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return ''
        return path.read_text(encoding='utf-8', errors='ignore')[:MAX_CONTENT_CHARS]
    except OSError:
        return ''


def under_allowed_paths(rel_path: str, allowed_paths: list[str]) -> bool:
    normalized = rel_path.strip('/')
    for item in allowed_paths:
        candidate = str(item).strip().strip('/')
        if candidate and (normalized == candidate or normalized.startswith(candidate + '/')):
            return True
    return False


def stack_dirs(profile: dict) -> set[str]:
    stack = str(profile.get('stack', '')).strip().lower()
    if stack in {'nextjs', 'react', 'vue', 'nuxt', 'angular', 'svelte', 'sveltekit', 'vite'}:
        return FRONTEND_HINT_DIRS
    if stack.startswith('python') or stack in {'node', 'go', 'rust'}:
        return BACKEND_HINT_DIRS
    return FRONTEND_HINT_DIRS | BACKEND_HINT_DIRS


def score_candidate(rel_path: str, content: str, keywords: list[str], phrases: list[str], allowed_paths: list[str], profile: dict) -> tuple[int, list[str], list[str]]:
    path_lower = rel_path.lower()
    reasons: list[str] = []
    matched_keywords: list[str] = []
    score = 0

    path_parts = [part.lower() for part in Path(rel_path).parts]
    path_blob = '/'.join(path_parts)
    content_lower = content.lower()

    for keyword in keywords:
        keyword_score = 0
        if keyword in path_blob:
            keyword_score += 7
        elif any(keyword == part or keyword in part for part in path_parts):
            keyword_score += 5
        if keyword in content_lower:
            keyword_score += 2
        if keyword_score:
            score += keyword_score
            matched_keywords.append(keyword)

    for phrase in phrases:
        if phrase in content or phrase in rel_path:
            score += 4
            reasons.append(f'phrase match: {phrase}')

    matched_keywords = dedupe(matched_keywords)
    if matched_keywords:
        reasons.append(f'keyword match: {", ".join(matched_keywords[:5])}')

    if under_allowed_paths(rel_path, allowed_paths):
        score += 4
        reasons.append('inside allowed_paths')

    hint_dirs = stack_dirs(profile)
    if any(part in hint_dirs for part in path_parts):
        score += 2
        reasons.append('stack-hint directory')

    if any(part in TEST_HINT_NAMES for part in path_parts) or any(token in path_lower for token in ('test', 'spec')):
        score += 1
        reasons.append('test-related file')

    if rel_path.endswith(('.md', '.json', '.yml', '.yaml')):
        score -= 1

    return score, dedupe(reasons), matched_keywords


def scan_repo(repo_path: Path, task: dict) -> dict:
    profile = task.get('repo_profile') if isinstance(task.get('repo_profile'), dict) else {}
    allowed_paths = [str(item).strip() for item in task.get('allowed_paths', []) if str(item).strip()] if isinstance(task.get('allowed_paths'), list) else []
    keywords, phrases, raw_query = collect_query_bits(task)

    candidates = []
    for current_root, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        current = Path(current_root)
        for filename in filenames:
            path = current / filename
            rel_path = path.relative_to(repo_path).as_posix()
            if not should_scan(path):
                continue
            content = read_text_snippet(path)
            if not content and path.suffix.lower() not in {'.json', '.yml', '.yaml', '.toml', '.md'}:
                continue
            score, reasons, matched_keywords = score_candidate(rel_path, content, keywords, phrases, allowed_paths, profile)
            if score <= 0:
                continue
            candidates.append({
                'path': rel_path,
                'score': score,
                'reasons': reasons,
                'matched_keywords': matched_keywords,
            })

    candidates.sort(key=lambda item: (-item['score'], item['path']))
    top = candidates[:MAX_CANDIDATES]
    similar_paths = [item['path'] for item in top]

    if top:
        lines = [f"- `{item['path']}` — {'; '.join(item['reasons'][:3])}" for item in top]
        summary = '\n'.join(lines)
    else:
        summary = '- No strong similar implementation found; read nearby modules and tests manually.'

    return {
        'version': 1,
        'query_text': raw_query,
        'keywords': keywords,
        'phrases': phrases,
        'candidates': top,
        'similar_impl_paths': similar_paths,
        'summary': summary,
    }


def apply_to_task(task_path: Path, repo_path: Path) -> dict:
    task = read_json(task_path)
    report = scan_repo(repo_path, task)
    task['similar_impl_paths'] = report['similar_impl_paths']
    task['pattern_finder_summary'] = report['summary']
    task['pattern_finder'] = report
    write_json(task_path, task)
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Find similar implementation paths for an xtt-workflow task')
    parser.add_argument('--repo-path', required=True, help='Path to the repo root')
    parser.add_argument('--task-json', required=True, help='Path to the task json file')
    parser.add_argument('--write-task', action='store_true', help='Write similar_impl_paths/pattern_finder fields back into task json')
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

    if args.write_task:
        report = apply_to_task(task_path, repo_path)
    else:
        report = scan_repo(repo_path, read_json(task_path))

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
