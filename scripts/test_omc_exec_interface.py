#!/usr/bin/env python3
"""
test_omc_exec_interface.py — omc_autopilot이 omc_exec.py의 올바른 CLI를 사용하는지 검증

omc_exec.py는 --prompt-file / --execution-mode / --timeout-sec 인터페이스를 씀.
omc_autopilot.py가 구버전(--prompt / --headless / --timeout)을 쓰면 실행 시 crash.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
_AUTOPILOT = _SCRIPTS / "omc_autopilot.py"

# 사용하면 안 되는 구버전 플래그
_FORBIDDEN = ["--headless", "--timeout", "--prompt"]

# 반드시 포함돼야 하는 신버전 플래그
_REQUIRED = ["--prompt-file", "--execution-mode", "--timeout-sec"]


def _get_cmd_string_literals(source: str) -> list[str]:
    """소스에서 문자열 리터럴을 모두 추출."""
    tree = ast.parse(source)
    literals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
    return literals


def test_autopilot_does_not_use_old_exec_flags() -> None:
    source = _AUTOPILOT.read_text(encoding="utf-8")
    literals = _get_cmd_string_literals(source)
    found = [flag for flag in _FORBIDDEN if flag in literals]
    assert not found, (
        f"omc_autopilot.py에 구버전 omc_exec.py 플래그가 남아 있음: {found}\n"
        "  --prompt → --prompt-file (tempfile)\n"
        "  --headless → --execution-mode headless\n"
        "  --timeout → --timeout-sec"
    )


def test_autopilot_uses_new_exec_flags() -> None:
    source = _AUTOPILOT.read_text(encoding="utf-8")
    literals = _get_cmd_string_literals(source)
    missing = [flag for flag in _REQUIRED if flag not in literals]
    assert not missing, (
        f"omc_autopilot.py에 신버전 omc_exec.py 플래그가 없음: {missing}"
    )
