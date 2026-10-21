"""
conftest.py — scripts/ pytest 공통 fixture

pipeline 테스트에서 프로젝트 루트 pipeline_run_result.json 오염을 방지한다.
autouse=False: subprocess 기반 테스트는 --target tmp_path 를 직접 제어하므로
개별 테스트가 경로를 명시적으로 관리한다.
단위 테스트(importlib/_get_result_path)는 monkeypatch.setenv 로 직접 격리한다.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests that run external health checks")


@pytest.fixture(autouse=True)
def isolate_work_class_lock_custody(monkeypatch):
    """Keep developer-level signer custody from changing repository tests."""
    monkeypatch.setenv("OMC_REQUIRE_WORK_CLASS_LOCK", "0")
    monkeypatch.delenv("OMC_WORK_CLASS_LOCK_CONFIG_FILE", raising=False)
    monkeypatch.delenv("OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY", raising=False)


@pytest.fixture()
def isolated_result_path(tmp_path: Path, monkeypatch):
    """OmC_PIPELINE_RESULT_PATH를 tmp_path 기반으로 격리한다 (opt-in)."""
    isolated = tmp_path / "pipeline_run_result.json"
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(isolated))
    return isolated


@pytest.fixture()
def installed_omc_target(tmp_path: Path):
    """Install this candidate into an empty target, leaving the checkout alone."""
    source = Path(__file__).resolve().parent.parent
    target = tmp_path / "installed-omc"
    target.mkdir()
    result = subprocess.run(
        [sys.executable, str(source / "scripts/install.py"), "--target", str(target)],
        cwd=source, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"OMC installation failed ({result.returncode}): "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    return target
