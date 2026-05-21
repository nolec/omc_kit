#!/usr/bin/env python3
"""OMC 공통 유틸리티 — 모든 omc_*.py 스크립트에서 공유합니다.

이 모듈은 순수 라이브러리입니다. main() 진입점이 없으므로
if __name__ == "__main__" 블록을 포함하지 않습니다.
"""
from __future__ import annotations

from pathlib import Path


def project_root(target: "Path | str | None" = None) -> Path:
    """target 인자를 받아 절대 경로로 정규화된 프로젝트 루트를 반환합니다.

    - target이 None이면 현재 작업 디렉토리를 반환합니다.
    - 상대 경로(".")도 .resolve()로 절대화됩니다.
    """
    return Path(target).resolve() if target else Path.cwd()
