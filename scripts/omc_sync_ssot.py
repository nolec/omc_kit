#!/usr/bin/env python3
"""
omc_sync_ssot.py — Codex·공통 파일의 명시적 SSOT 동기화

기존 목적지 ROOT/omc_kit을 유지하며, 라이브 공통 스크립트와 Codex 문서를 복사합니다.
외부 source 등록 해석·설치·자동 훅 활성화는 수행하지 않습니다.
차이가 있는 파일은 경고, 없으면 ✅ 출력.

사용:
  python3 scripts/omc_sync_ssot.py          # 전체 동기화
  python3 scripts/omc_sync_ssot.py --check  # diff 확인만 (복사 안 함)
  python3 scripts/omc_sync_ssot.py --dry-run  # 동기화 대상 목록만 출력
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "omc_kit"

_MANAGED_AGENT_SKILL_ROOTS = {"pr-create"}
_MANAGED_AGENT_SKILL_FILES = {"SKILL_CHECKLIST.md", "SKILL_TEMPLATE.md"}


def _is_managed_agent_skill(relative_path: Path) -> bool:
    if len(relative_path.parts) == 1:
        return relative_path.name in _MANAGED_AGENT_SKILL_FILES
    skill_root = relative_path.parts[0]
    return skill_root.startswith("omc-") or skill_root in _MANAGED_AGENT_SKILL_ROOTS

# 라이브 파일 → 템플릿 경로 매핑
SYNC_MAP: list[tuple[str, str]] = [
    # scripts
    *[(f"scripts/{p.name}", f"scripts/{p.name}") for p in (ROOT / "scripts").glob("omc_*.py")],
    # Shared and Codex top-level documents
    ("AGENTS.md", "templates/AGENTS.md"),
    ("CODEX.md", "templates/CODEX.md"),
    # Agent Skills canonical source
    *[
        (
            f".agents/skills/{p.relative_to(ROOT / '.agents' / 'skills')}",
            f"templates/.agents/skills/{p.relative_to(ROOT / '.agents' / 'skills')}",
        )
        for p in (ROOT / ".agents" / "skills").rglob("*.md")
        if (ROOT / ".agents" / "skills").exists()
        and _is_managed_agent_skill(p.relative_to(ROOT / ".agents" / "skills"))
    ],
    # .codex/commands
    *[
        (f".codex/commands/{p.name}", f"templates/.codex/commands/{p.name}")
        for p in (ROOT / ".codex" / "commands").glob("*.md")
        if (ROOT / ".codex" / "commands").exists()
    ],

]


def _pairs(root: Path, kit: Path) -> list[tuple[Path, Path]]:
    """(라이브 파일, 템플릿 파일) 쌍 목록 반환. 존재하는 파일만."""
    result = []
    for live_rel, tmpl_rel in SYNC_MAP:
        live = root / live_rel
        tmpl = kit / tmpl_rel
        if live.exists():
            result.append((live, tmpl))
    return result


def check(root: Path = ROOT, kit: Path = KIT) -> list[tuple[Path, Path]]:
    """동기화가 필요한 (라이브, 템플릿) 쌍 목록 반환."""
    out_of_sync = []
    for live, tmpl in _pairs(root, kit):
        if not tmpl.exists():
            out_of_sync.append((live, tmpl))
        elif not filecmp.cmp(live, tmpl, shallow=False):
            out_of_sync.append((live, tmpl))
    return out_of_sync


def sync(root: Path = ROOT, kit: Path = KIT) -> int:
    """동기화 실행. 변경 파일 수 반환."""
    pairs = _pairs(root, kit)
    synced = 0
    for live, tmpl in pairs:
        if not tmpl.exists() or not filecmp.cmp(live, tmpl, shallow=False):
            tmpl.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, tmpl)
            synced += 1
            rel = live.relative_to(root)
            print(f"  ✅ {rel}")
    return synced


def main() -> int:
    ap = argparse.ArgumentParser(description="OMC SSOT 동기화")
    ap.add_argument("--check", action="store_true", help="diff 확인만 (복사 안 함)")
    ap.add_argument("--dry-run", action="store_true", help="대상 목록 출력 (복사 안 함)")
    args = ap.parse_args()

    if args.check or args.dry_run:
        out_of_sync = check()
        if not out_of_sync:
            print("[SSOT] ✅ 모든 파일 동기화됨")
            return 0
        label = "동기화 필요" if args.check else "동기화 대상"
        print(f"[SSOT] ⚠️  {label} {len(out_of_sync)}개:")
        for live, tmpl in out_of_sync:
            print(f"  {live.relative_to(ROOT)}  →  {tmpl.relative_to(KIT)}")
        return 1 if args.check else 0

    # 실제 동기화
    print("[SSOT] 동기화 시작...")
    synced = sync()
    if synced:
        print(f"[SSOT] ✅ {synced}개 파일 동기화 완료")
    else:
        print("[SSOT] ✅ 이미 모두 최신 상태")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
