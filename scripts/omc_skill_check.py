#!/usr/bin/env python3
"""
omc_skill_check.py — OMC 스킬 등록 9개 항목 검증기

사용법:
  python3 scripts/omc_skill_check.py omc-critique
  python3 scripts/omc_skill_check.py omc-critique --fix-hint
  python3 scripts/omc_skill_check.py --all

exit code:
  0 = 모든 항목 통과
  1 = 누락 항목 있음
"""

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent

# omc_kit 자체 내부에서 실행될 때: templates/ 하위를 ROOT로 사용
# (설치된 프로젝트에서 실행될 때는 ROOT = 프로젝트 루트)
_TEMPLATES = ROOT / "templates"
if _TEMPLATES.exists() and (ROOT / "scripts" / "install.py").exists():
    # omc_kit 자체에서 실행 중
    ROOT = _TEMPLATES


def _rel(p: Path, root: Path) -> Path:
    """Python 3.8 호환 relative_to — 실패 시 절대 경로 반환."""
    try:
        return p.relative_to(root)
    except ValueError:
        return p


def get_omc_kit_path() -> Path | None:
    hub_path_file = ROOT / ".omc" / "hub.path"
    if hub_path_file.exists():
        p = hub_path_file.read_text().strip()
        if p and Path(p).exists():
            return Path(p)
    return None


def skill_name_to_command_name(skill: str) -> str:
    """omc-critique -> critique"""
    return skill.removeprefix("omc-")


def check_skill(skill: str, verbose: bool = True) -> list[dict]:
    """9개 항목 체크. 결과 리스트 반환."""
    cmd = skill_name_to_command_name(skill)
    omc_kit = get_omc_kit_path()

    checks = [
        {
            "num": 1,
            "label": "Cursor 스킬",
            "path": ROOT / ".agent" / "skills" / skill / "SKILL.md",
        },
        {
            "num": 2,
            "label": "Codex 스킬",
            "path": ROOT / ".agents" / "skills" / skill / "SKILL.md",
        },
        {
            "num": 3,
            "label": "Claude Code 커맨드",
            "path": ROOT / ".claude" / "commands" / f"{cmd}.md",
        },
        {
            "num": 4,
            "label": "Gemini CLI (omc-commands.md 내 섹션)",
            "path": ROOT / ".gemini" / "commands" / "omc-commands.md",
            "contains_any": [f"## /{cmd}", f"### `/{cmd}", f"/`{cmd}"],
        },
        {
            "num": 5,
            "label": "Codex CLI (omc-commands.md 내 $omc-NAME)",
            "path": ROOT / ".codex" / "commands" / "omc-commands.md",
            "contains": f"$omc-{cmd}",
        },
        {
            "num": 6,
            "label": "AGENTS.md 커맨드 테이블",
            "path": ROOT / "AGENTS.md",
            "contains": f"`/{cmd}",
        },
        {
            "num": 7,
            "label": "omc-commands.mdc 슬래시 커맨드",
            "path": ROOT / ".cursor" / "rules" / "omc-commands.mdc",
            "contains": f"## /{cmd}",
        },
        {
            "num": 8,
            "label": "omc-roles.mdc 트리거 키워드",
            "path": ROOT / ".cursor" / "rules" / "omc-roles.mdc",
            "contains": cmd,
        },
    ]

    # 9번: omc_kit SSOT — 1~3번 파일이 omc_kit에 존재하는지
    if omc_kit:
        checks.append({
            "num": 9,
            "label": f"omc_kit SSOT ({omc_kit.name})",
            "path": omc_kit / "templates" / ".agent" / "skills" / skill / "SKILL.md",
        })
    else:
        checks.append({
            "num": 9,
            "label": "omc_kit SSOT (.omc/hub.path 없음 — 건너뜀)",
            "path": None,
            "skip": True,
        })

    results = []
    for c in checks:
        if c.get("skip"):
            results.append({**c, "ok": None, "reason": "hub.path 없음"})
            continue

        p: Path = c["path"]
        if not p.exists():
            results.append({**c, "ok": False, "reason": f"파일 없음: {_rel(p, ROOT)}"})
            continue

        # contains 체크
        file_text = p.read_text(encoding="utf-8")
        if "contains" in c:
            if c["contains"] not in file_text:
                results.append({**c, "ok": False, "reason": f"'{c['contains']}' 문자열 없음: {_rel(p, ROOT)}"})
                continue
        if "contains_any" in c:
            if not any(pat in file_text for pat in c["contains_any"]):
                results.append({**c, "ok": False, "reason": f"{c['contains_any']} 중 없음: {_rel(p, ROOT)}"})
                continue

        results.append({**c, "ok": True, "reason": ""})

    return results


def print_report(skill: str, results: list[dict], fix_hint: bool = False):
    passed = sum(1 for r in results if r["ok"] is True)
    skipped = sum(1 for r in results if r["ok"] is None)
    failed = sum(1 for r in results if r["ok"] is False)

    print(f"\n{'━'*50}")
    print(f"SKILL CHECK: {skill}")
    print(f"{'━'*50}")
    for r in results:
        if r["ok"] is True:
            icon = "[x]"
        elif r["ok"] is None:
            icon = "[-]"
        else:
            icon = "[ ]"
        print(f"  {icon} {r['num']}. {r['label']}")
        if not r["ok"] and r.get("reason"):
            print(f"       → {r['reason']}")

    print(f"{'━'*50}")
    print(f"결과: {passed}/9 통과  {failed}개 누락  {skipped}개 건너뜀")

    if failed > 0:
        print("\n누락 항목을 보완하려면:")
        for r in results:
            if not r["ok"]:
                print(f"  {r['num']}. {r['label']} — {r['reason']}")
        if fix_hint:
            print("\n빠른 동기화 참고:")
            print(f"  cat .agent/skills/SKILL_CHECKLIST.md")
    print(f"{'━'*50}\n")


def list_all_skills() -> list[str]:
    skills_dir = ROOT / ".agents" / "skills"
    return sorted([
        d.name for d in skills_dir.iterdir()
        if d.is_dir() and d.name.startswith("omc-")
    ])


def main():
    parser = argparse.ArgumentParser(description="OMC 스킬 등록 9개 항목 검증")
    parser.add_argument("skill", nargs="?", help="스킬 이름 (예: omc-critique)")
    parser.add_argument("--all", action="store_true", help="모든 스킬 검증")
    parser.add_argument("--fix-hint", action="store_true", help="누락 시 수정 힌트 출력")
    args = parser.parse_args()

    if args.all:
        skills = list_all_skills()
        if not skills:
            print("스킬 없음 (.agent/skills/ 에 omc-* 폴더 없음)")
            sys.exit(0)
        total_failed = 0
        failed_skills = []
        for skill in skills:
            results = check_skill(skill, verbose=False)
            print_report(skill, results, fix_hint=args.fix_hint)
            f = sum(1 for r in results if r["ok"] is False)
            if f > 0:
                failed_skills.append(skill)
            total_failed += f
        print(f"{'━'*50}")
        print(f"전체 요약: {len(skills)}개 스킬  통과 {len(skills)-len(failed_skills)}개  누락 {len(failed_skills)}개")
        if failed_skills:
            print(f"누락 스킬: {', '.join(failed_skills)}")
        print(f"{'━'*50}\n")
        sys.exit(1 if total_failed > 0 else 0)

    elif args.skill:
        skill = args.skill
        if not skill.startswith("omc-"):
            skill = f"omc-{skill}"
        results = check_skill(skill)
        print_report(skill, results, fix_hint=args.fix_hint)
        failed = sum(1 for r in results if r["ok"] is False)
        sys.exit(1 if failed > 0 else 0)

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
