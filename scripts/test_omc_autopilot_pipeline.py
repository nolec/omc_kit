"""
test_omc_autopilot_pipeline.py — pipeline 서브커맨드 회귀 방지

T3/T4 DoD:
  - pipeline --dry-run exit 0
  - pipeline_run_result.json 생성
  - uncommitted 변경 있으면 abort
  - retry_exhausted 시 비0 종료
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT = ROOT / "scripts" / "omc_autopilot.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUTOPILOT)] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd or ROOT),
    )


def test_pipeline_dry_run_exits_zero(tmp_path: Path):
    """pipeline --dry-run은 exit 0으로 완료돼야 한다."""
    result = _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "테스트용 더미 지시문",
         "--branch", "feat/test-pipeline",
         "--dry-run"],
    )
    assert result.returncode == 0, (
        f"pipeline --dry-run exit {result.returncode}\n"
        f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-300:]}"
    )


def test_pipeline_dry_run_creates_result_file(tmp_path: Path):
    """pipeline --dry-run 완료 후 pipeline_run_result.json이 생성돼야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "테스트용 더미 지시문",
         "--branch", "feat/test-pipeline",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    assert result_file.exists(), "pipeline_run_result.json 미생성"
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert "status" in data
    assert "branch" in data
    assert "steps" in data


def test_pipeline_requires_instruction():
    """--instruction 없으면 exit 비0이어야 한다."""
    result = _run(["pipeline", "--dry-run"])
    assert result.returncode != 0, "instruction 없이 성공해선 안 됨"


def test_pipeline_subcommand_exists():
    """pipeline 서브커맨드가 argparse에 등록돼야 한다."""
    result = _run(["pipeline", "--help"])
    assert result.returncode == 0, f"pipeline --help 실패: {result.stderr}"
    assert "instruction" in result.stdout.lower() or "instruction" in result.stderr.lower()
