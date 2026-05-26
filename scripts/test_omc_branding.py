#!/usr/bin/env python3
"""
test_omc_branding.py — "multi-assistant" 구명칭 잔재 검증

코드베이스에서 "multi-assistant" / "multi_assistant" 문자열이
배포 대상 파일에 남아 있지 않은지 확인한다.
export_repo.py 는 kit-only 내부 도구라 제외.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# 검사 제외 파일 (kit-only 내부 도구)
_EXCLUDE = {
    "export_repo.py",
    "test_omc_branding.py",  # 이 파일 자체
}

# 검사 대상 확장자
_EXTS = {".py", ".md", ".mdc", ".json", ".txt"}


def _scan_files() -> list[tuple[Path, int, str]]:
    """multi.assistant 패턴이 포함된 (파일, 라인번호, 내용) 목록 반환."""
    hits: list[tuple[Path, int, str]] = []
    for ext in _EXTS:
        for path in _ROOT.rglob(f"*{ext}"):
            if ".git" in path.parts or "__pycache__" in path.parts or ".omc" in path.parts:
                continue
            if path.name in _EXCLUDE:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                lower = line.lower()
                if "multi-assistant" in lower or "multi_assistant" in lower:
                    hits.append((path.relative_to(_ROOT), i, line.strip()))
    return hits


def test_no_multi_assistant_branding() -> None:
    hits = _scan_files()
    if hits:
        report = "\n".join(f"  {p}:{n}  {l}" for p, n, l in hits)
        pytest.fail(f"multi-assistant 구명칭 잔재 {len(hits)}건:\n{report}")
