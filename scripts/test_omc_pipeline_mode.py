"""
test_omc_pipeline_mode.py — LITE/FULL 모드 분기 회귀 방지

T1: _detect_pipeline_mode 자동 감지
T2: --mode lite dry-run → plan/critique 스텝 없음
T3: --mode full dry-run → 기존 동작 유지 (plan 포함)
T4: pipeline_run_result.json에 mode 필드 존재
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT = ROOT / "scripts" / "omc_autopilot.py"


def _load_autopilot():
    spec = importlib.util.spec_from_file_location("omc_autopilot", str(AUTOPILOT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUTOPILOT)] + args,
        capture_output=True, text=True, cwd=str(ROOT),
    )


# ── T1: _detect_pipeline_mode ─────────────────────────────────────────────

def test_detect_lite_for_fix_branch():
    """fix/ 브랜치는 LITE로 감지돼야 한다."""
    mod = _load_autopilot()
    assert mod._detect_pipeline_mode("fix/bug-123", "버그 수정", "auto") == "lite"


def test_detect_lite_for_hotfix_branch():
    """hotfix/ 브랜치는 LITE로 감지돼야 한다."""
    mod = _load_autopilot()
    assert mod._detect_pipeline_mode("hotfix/urgent", "긴급 수정", "auto") == "lite"


def test_detect_lite_for_short_instruction():
    """지시문 50자 이하는 LITE로 감지돼야 한다."""
    mod = _load_autopilot()
    assert mod._detect_pipeline_mode("feat/new", "짧은 지시문", "auto") == "lite"


def test_detect_full_for_long_feat():
    """feat/ 브랜치 + 긴 지시문은 FULL로 감지돼야 한다."""
    mod = _load_autopilot()
    long_instruction = "이것은 매우 긴 지시문입니다. " * 5  # 50자 초과
    assert mod._detect_pipeline_mode("feat/new-feature", long_instruction, "auto") == "full"


def test_explicit_lite_overrides_auto():
    """--mode lite 명시 시 브랜치/지시문 무관하게 LITE."""
    mod = _load_autopilot()
    long_instruction = "이것은 매우 긴 지시문입니다. " * 5
    assert mod._detect_pipeline_mode("feat/complex", long_instruction, "lite") == "lite"


def test_explicit_full_overrides_auto():
    """--mode full 명시 시 fix/ 브랜치여도 FULL."""
    mod = _load_autopilot()
    assert mod._detect_pipeline_mode("fix/small", "짧음", "full") == "full"


# ── T2: --mode lite dry-run → plan/critique 없음 ──────────────────────────

def test_lite_mode_skips_plan_and_critique(tmp_path: Path):
    """LITE 모드 dry-run에서 결과 파일에 plan/critique 스텝이 없어야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "버그 수정",
         "--branch", "fix/test",
         "--mode", "lite",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    assert result_file.exists(), "결과 파일 없음"
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert "plan" not in data["steps"], "LITE 모드에 plan 스텝이 있으면 안 됨"
    assert "critique" not in data["steps"], "LITE 모드에 critique 스텝이 있으면 안 됨"


def test_lite_mode_has_task_and_review(tmp_path: Path):
    """LITE 모드 dry-run에서 task + review 스텝은 있어야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "버그 수정",
         "--branch", "fix/test",
         "--mode", "lite",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert "task" in data["steps"], "LITE 모드에 task 스텝 없음"
    assert "review" in data["steps"], "LITE 모드에 review 스텝 없음"


# ── T3: --mode full dry-run → plan 포함 ───────────────────────────────────

def test_full_mode_includes_plan(tmp_path: Path):
    """FULL 모드 dry-run에서 plan 스텝이 있어야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "새 기능 개발",
         "--branch", "feat/new",
         "--mode", "full",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert "plan" in data["steps"], "FULL 모드에 plan 스텝 없음"


# ── T4: mode 필드 존재 ────────────────────────────────────────────────────

def test_result_file_contains_mode_field(tmp_path: Path):
    """pipeline_run_result.json에 mode 필드가 있어야 한다."""
    _run(
        ["--target", str(tmp_path),
         "pipeline",
         "--instruction", "테스트",
         "--branch", "fix/x",
         "--dry-run"],
    )
    result_file = tmp_path / ".omc" / "pipeline_run_result.json"
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert "mode" in data, f"mode 필드 없음: {list(data.keys())}"
    assert data["mode"] in ("lite", "full")
