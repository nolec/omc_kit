"""
omc-autopilot skill contract regression tests.

Autopilot is an external-effect skill. Its docs must stay short, mirror-safe,
and aligned with the real omc_autopilot.py pipeline CLI.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_NON_EMPTY_LINES = 95

REQUIRED_SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "omc-autopilot" / "SKILL.md",
    ROOT / "templates" / ".agents" / "skills" / "omc-autopilot" / "SKILL.md",
]
OPTIONAL_SKILL_PATHS = [
    ROOT / ".agent" / "skills" / "omc-autopilot" / "SKILL.md",
    ROOT / "templates" / ".agent" / "skills" / "omc-autopilot" / "SKILL.md",
]
AUTOPILOT_SCRIPT = ROOT / "scripts" / "omc_autopilot.py"

REQUIRED_SEQUENCE = [
    "omc-autopilot",
    "지시문",
    "브랜치",
    "실제 pipeline 실행 금지",
    "읽기 전용 확인",
    "git branch --show-current",
    "git status --porcelain",
    "git log --oneline",
    "python3 scripts/omc.py state status --target .",
    "실행 전 확정",
    "dirty",
    "--allow-dirty",
    "사용자 승인",
    "명령 출력",
    "nohup python3 scripts/omc_autopilot.py pipeline",
    "--instruction",
    "--branch",
    "--mode",
    "--dry-run",
    "--force",
    "--resume",
    "python3 scripts/omc_autopilot.py pipeline-status",
]

REQUIRED_BEHAVIOR_MARKERS = [
    "LITE",
    "FULL",
    "plan→critique→task→review",
    "PR",
    ".omc/pipeline.log",
    ".omc/pipeline_run_result.json",
    "N/A",
    "이유",
]

CLI_OPTIONS = [
    "--instruction",
    "--branch",
    "--mode",
    "--allow-dirty",
    "--dry-run",
    "--force",
    "--resume",
]

VALID_AUTOPILOT_SAMPLE = """
AUTOPILOT 실행 전 확정:
- 지시문: 스킬 정리
- 브랜치: feat/skill-cleanup
- dirty: clean
- 사용자 승인: 승인

읽기 전용 확인:
- git branch --show-current
- git status --porcelain
- python3 scripts/omc.py state status --target .

명령 출력:
nohup python3 scripts/omc_autopilot.py pipeline --instruction "스킬 정리" --branch "feat/skill-cleanup" --mode full > .omc/pipeline.log 2>&1 &
"""

INVALID_AUTOPILOT_SAMPLE = """
nohup python3 scripts/omc_autopilot.py pipeline --instruction "x" &
"""


def _read(path: Path) -> str:
    assert path.exists(), f"missing omc-autopilot skill path: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _collect_skill_texts() -> dict[str, str]:
    texts = {path.relative_to(ROOT).as_posix(): _read(path) for path in REQUIRED_SKILL_PATHS}
    texts.update(
        {
            path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
            for path in OPTIONAL_SKILL_PATHS
            if path.exists()
        }
    )
    return texts


def _validate_autopilot_output(sample: str) -> list[str]:
    required_patterns = {
        "confirmation": r"지시문:.*브랜치:.*사용자 승인:\s*(미승인|승인)",
        "readonly": r"읽기 전용 확인:.*git branch --show-current.*git status --porcelain",
        "dirty": r"dirty:\s*(clean|dirty|N/A)",
        "command": r"명령 출력:.*omc_autopilot.py pipeline.*--instruction.*--branch",
    }
    return [
        name
        for name, pattern in required_patterns.items()
        if not re.search(pattern, sample, re.S)
    ]


def test_omc_autopilot_skill_paths_are_identical():
    texts = _collect_skill_texts()
    canonical = texts[".agents/skills/omc-autopilot/SKILL.md"]
    mismatched = [name for name, text in texts.items() if text != canonical]
    assert not mismatched, f"omc-autopilot skill copies differ: {mismatched}"


def test_omc_autopilot_skill_stays_short_enough_to_scan():
    text = _read(REQUIRED_SKILL_PATHS[0])
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= MAX_NON_EMPTY_LINES, (
        f"omc-autopilot has {len(non_empty_lines)} non-empty lines"
    )


def test_omc_autopilot_skill_preserves_required_execution_order():
    text = _read(REQUIRED_SKILL_PATHS[0])
    cursor = -1
    missing_or_reordered: list[str] = []

    for marker in REQUIRED_SEQUENCE:
        next_pos = text.find(marker, cursor + 1)
        if next_pos == -1:
            missing_or_reordered.append(marker)
        else:
            cursor = next_pos

    assert not missing_or_reordered, f"missing or reordered markers: {missing_or_reordered}"


def test_omc_autopilot_skill_preserves_required_behavior_markers():
    text = _read(REQUIRED_SKILL_PATHS[0])
    missing = [marker for marker in REQUIRED_BEHAVIOR_MARKERS if marker not in text]
    assert not missing, f"missing behavior markers: {missing}"


def test_documented_pipeline_options_exist_in_real_cli():
    script_text = AUTOPILOT_SCRIPT.read_text(encoding="utf-8")
    missing = [option for option in CLI_OPTIONS if option not in script_text]
    assert not missing, f"documented pipeline options missing in CLI: {missing}"


def test_omc_autopilot_skill_does_not_execute_pipeline_itself():
    text = _read(REQUIRED_SKILL_PATHS[0])
    forbidden = [r"실행한다", r"직접 실행", r"자동으로 시작"]
    found = [pattern for pattern in forbidden if re.search(pattern, text)]
    assert not found, f"skill should output commands, not run pipeline: {found}"


def test_valid_autopilot_output_fixture_has_required_structure():
    assert _validate_autopilot_output(VALID_AUTOPILOT_SAMPLE) == []


def test_invalid_autopilot_output_fixture_exposes_weak_gate():
    failures = _validate_autopilot_output(INVALID_AUTOPILOT_SAMPLE)
    assert {"confirmation", "readonly", "dirty", "command"}.issubset(set(failures))
