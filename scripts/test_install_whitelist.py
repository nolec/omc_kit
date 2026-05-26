#!/usr/bin/env python3
"""
test_install_whitelist.py — install.py 화이트리스트 전환 검증

- T1: test_*.py / conftest.py 가 타겟에 배포되지 않음
- T2: 기대 배포 파일 전수 존재 확인 (회귀 방지)
- T3: omc_kit 전용 파일(auto_prompt.py, autopilot.py)이 배포되지 않음
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent
_INSTALL_PY = _SCRIPTS_DIR / "install.py"

_EXPECTED_SCRIPTS = {
    "install.py",
    "omc.py",
    "compose_prompt.py",
    "omc_autopilot.py",
    "omc_chat.py",
    "omc_context.py",
    "omc_context_save.py",
    "omc_cost.py",
    "omc_doctor.py",
    "omc_domain.py",
    "omc_exec.py",
    "omc_guard.py",
    "omc_health.py",
    "omc_hooks.py",
    "omc_hub_push.py",
    "omc_lesson.py",
    "omc_peer_review.py",
    "omc_pipeline_guard.py",
    "omc_role_suggest.py",
    "omc_run.py",
    "omc_skill_check.py",
    "omc_state.py",
    "omc_sync_ssot.py",
    "omc_tdd_check.py",
    "omc_utils.py",
}

_KIT_ONLY_SCRIPTS = {
    "auto_prompt.py",
    "autopilot.py",
    "safe_trash.py",
    "export_repo.py",
    "conftest.py",
}


@pytest.fixture()
def installed_target(tmp_path: Path) -> Path:
    """빈 임시 디렉토리에 install.py를 실행한다."""
    (tmp_path / "scripts").mkdir()
    result = subprocess.run(
        [sys.executable, str(_INSTALL_PY), "--target", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"install 실패:\n{result.stderr}"
    return tmp_path


def _scripts(target: Path) -> set[str]:
    return {p.name for p in (target / "scripts").glob("*.py")}


def test_install_does_not_copy_test_files(installed_target: Path) -> None:
    copied = _scripts(installed_target)
    leaked = {f for f in copied if f.startswith("test_")}
    assert not leaked, f"타겟에 test_*.py가 배포됨: {leaked}"


def test_install_does_not_copy_conftest(installed_target: Path) -> None:
    assert "conftest.py" not in _scripts(installed_target), \
        "타겟에 conftest.py가 배포됨"


def test_install_copies_expected_scripts(installed_target: Path) -> None:
    copied = _scripts(installed_target)
    missing = _EXPECTED_SCRIPTS - copied
    assert not missing, f"기대 배포 파일이 누락됨: {missing}"


def test_install_excludes_kit_only_files(installed_target: Path) -> None:
    copied = _scripts(installed_target)
    leaked = _KIT_ONLY_SCRIPTS & copied
    assert not leaked, f"omc_kit 전용 파일이 타겟에 배포됨: {leaked}"
