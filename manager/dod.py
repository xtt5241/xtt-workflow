from __future__ import annotations

from pathlib import Path
import json
import sys

MANAGER_DIR = Path(__file__).resolve().parent
WORKFLOW_ROOT = MANAGER_DIR.parent
DOD_DIR = WORKFLOW_ROOT / 'config' / 'dod'
DEFAULT_TASK_KIND = 'feature'
VALID_TASK_KINDS = {'bugfix', 'feature', 'refactor', 'infra'}


def normalize_task_kind(value: str | None) -> str:
    cleaned = (value or DEFAULT_TASK_KIND).strip().lower()
    return cleaned if cleaned in VALID_TASK_KINDS else DEFAULT_TASK_KIND


def dod_path(task_kind: str) -> Path:
    return DOD_DIR / f'{normalize_task_kind(task_kind)}.json'


def load_dod_profile(task_kind: str) -> dict:
    path = dod_path(task_kind)
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['task_kind'] = normalize_task_kind(payload.get('task_kind'))
    payload.setdefault('label', payload['task_kind'].title())
    payload.setdefault('required_verify_sections', [])
    payload.setdefault('deliver_rule', '')
    return payload


def dod_summary(task_kind: str) -> str:
    profile = load_dod_profile(task_kind)
    sections = ', '.join(profile.get('required_verify_sections', [])) or '(none)'
    lines = [
        f"- task_kind: {profile['task_kind']}",
        f"- required_verify_sections: {sections}",
        f"- deliver_rule: {profile.get('deliver_rule') or '(none)'}",
    ]
    return '\n'.join(lines)


def check_verify_log(task: dict, log_text: str) -> dict:
    task_kind = normalize_task_kind(task.get('task_kind'))
    profile = load_dod_profile(task_kind)
    missing_sections = [section for section in profile.get('required_verify_sections', []) if section not in log_text]
    passed = not missing_sections
    summary = 'DoD passed' if passed else 'missing DoD sections: ' + ', '.join(missing_sections)
    return {
        'task_kind': task_kind,
        'required_verify_sections': profile.get('required_verify_sections', []),
        'missing_sections': missing_sections,
        'deliver_rule': profile.get('deliver_rule', ''),
        'passed': passed,
        'summary': summary,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] != 'check-verify':
        print('usage: dod.py check-verify <task.json> <log_path> <report.json>', file=sys.stderr)
        return 2

    task = json.loads(Path(argv[2]).read_text(encoding='utf-8'))
    log_text = Path(argv[3]).read_text(encoding='utf-8', errors='ignore') if Path(argv[3]).exists() else ''
    report = check_verify_log(task, log_text)
    Path(argv[4]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"DoD task_kind={report['task_kind']}")
    print('DoD required sections: ' + ', '.join(report['required_verify_sections']))
    if report['passed']:
        print('DoD: PASSED')
        return 0

    print('DoD: FAILED')
    for section in report['missing_sections']:
        print(f'- missing: {section}')
    return 4


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
