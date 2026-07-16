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

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests that run external health checks")


@pytest.fixture()
def isolated_result_path(tmp_path: Path, monkeypatch):
    """OmC_PIPELINE_RESULT_PATH를 tmp_path 기반으로 격리한다 (opt-in)."""
    isolated = tmp_path / "pipeline_run_result.json"
    monkeypatch.setenv("OmC_PIPELINE_RESULT_PATH", str(isolated))
    return isolated
