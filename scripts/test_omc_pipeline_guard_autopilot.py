"""
test_omc_pipeline_guard_autopilot.py — --autopilot 플래그 회귀 방지

T1 DoD:
  - --autopilot 플래그로 신규 파일 생성 차단 우회 → exit 0
  - --autopilot 없으면 기존대로 차단 → exit 1
  - 기존 파일 수정은 --autopilot 없어도 통과 → exit 0
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "omc_pipeline_guard.py"


def _run_guard(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_autopilot_bypasses_new_file_block(tmp_path: Path):
    """--autopilot 플래그가 있으면 신규 파일 생성이 차단되지 않아야 한다."""
    result = _run_guard(["check", "src/new_feature.py", "--autopilot"], cwd=tmp_path)
    assert result.returncode == 0, (
        f"--autopilot 플래그로 신규 파일이 차단됨 (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_no_autopilot_blocks_new_file(tmp_path: Path):
    """--autopilot 없으면 신규 구현 파일은 차단되어야 한다."""
    result = _run_guard(["check", "src/new_feature.py"], cwd=tmp_path)
    assert result.returncode == 1, (
        f"--autopilot 없을 때 신규 파일이 차단되지 않음 (exit {result.returncode})\n"
        f"stdout: {result.stdout}"
    )


def test_existing_file_always_passes(tmp_path: Path):
    """기존 파일 수정은 --autopilot 없이도 통과해야 한다."""
    existing = tmp_path / "src" / "existing.py"
    existing.parent.mkdir(parents=True)
    existing.write_text("# existing\n", encoding="utf-8")
    result = _run_guard(["check", "src/existing.py"], cwd=tmp_path)
    assert result.returncode == 0, (
        f"기존 파일 수정이 차단됨 (exit {result.returncode})\n"
        f"stdout: {result.stdout}"
    )


def test_autopilot_flag_not_in_check_edit(tmp_path: Path):
    """check-edit에도 --autopilot 플래그가 작동해야 한다."""
    result = _run_guard(["check-edit", "src/new_feature.py", "--autopilot"], cwd=tmp_path)
    assert result.returncode == 0, (
        f"check-edit --autopilot이 차단됨 (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
