"""
test_omc_macos_compat.py — macOS 호환성 회귀 방지 테스트

태스크 1: install.py non-interactive (stdin 없을 때 exit 0)
태스크 2: .sh 파일에 mktemp --suffix 없음
태스크 3: omc_doctor.py가 mktemp --suffix 패턴을 감지·경고
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.py"


# ─────────────────────────────────────────────────────────────
# 태스크 1: install.py --force 를 stdin 없이 실행하면 exit 0
# ─────────────────────────────────────────────────────────────

def test_install_force_non_tty():
    """stdin=DEVNULL 으로 install.py --force 실행 시 exit 0 이어야 한다."""
    with tempfile.TemporaryDirectory(prefix="omc-macos-compat.") as tmp:
        result = subprocess.run(
            [sys.executable, str(INSTALL), "--target", tmp, "--force"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"install.py --force exited {result.returncode} in non-interactive mode.\n"
            f"stdout: {result.stdout[-500:]}\n"
            f"stderr: {result.stderr[-300:]}"
        )


# ─────────────────────────────────────────────────────────────
# 태스크 2: 모든 .sh 파일에 mktemp --suffix 없음
# ─────────────────────────────────────────────────────────────

def _collect_sh_files() -> list[Path]:
    """templates/ 및 live 훅 디렉토리의 .sh 파일 전체 수집."""
    dirs = [
        ROOT / "templates" / ".agent-hooks",
        ROOT / "templates" / ".cursor" / "hooks",
        ROOT / ".agent-hooks",
        ROOT / ".cursor" / "hooks",
    ]
    files: list[Path] = []
    for d in dirs:
        if d.is_dir():
            files.extend(d.rglob("*.sh"))
    return files


@pytest.mark.parametrize("sh_file", _collect_sh_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_mktemp_suffix_in_sh(sh_file: Path):
    """각 .sh 파일에 GNU 전용 mktemp --suffix 옵션이 없어야 한다."""
    content = sh_file.read_text(encoding="utf-8")
    assert "mktemp --suffix" not in content, (
        f"{sh_file.relative_to(ROOT)} 에 macOS 비호환 'mktemp --suffix' 가 포함되어 있습니다.\n"
        "수정: mktemp -t prefix.XXXXXX 방식으로 교체하세요."
    )


# ─────────────────────────────────────────────────────────────
# 태스크 3: omc_doctor.py가 mktemp --suffix 패턴을 감지한다
# ─────────────────────────────────────────────────────────────

def test_doctor_detects_mktemp_suffix(tmp_path: Path):
    """omc_doctor.py가 .sh 파일의 mktemp --suffix 를 [WARN]으로 출력해야 한다."""
    fake_hooks = tmp_path / ".agent-hooks"
    fake_hooks.mkdir()
    bad_sh = fake_hooks / "bad-hook.sh"
    bad_sh.write_text("#!/bin/sh\nTMP=$(mktemp --suffix=.py)\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "omc_doctor.py"), "--target", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    combined = result.stdout + result.stderr
    assert "WARN" in combined and "mktemp" in combined, (
        "omc_doctor.py가 mktemp --suffix 패턴을 감지하지 못했습니다.\n"
        f"출력:\n{combined[:800]}"
    )
