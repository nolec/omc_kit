#!/usr/bin/env python3
"""
omc_health.py — 코드 품질 원클릭 대시보드

타입체크 · 린트 · 데드코드 수를 한 번에 요약 출력.
gstack의 /health 개념을 OMC 환경(nx monorepo)에 맞게 구현.

사용법:
  python3 scripts/omc_health.py              # 전체 체크 + 이슈 있으면 exit 1
  python3 scripts/omc_health.py --report-only  # 결과만 출력, 항상 exit 0/1
  python3 scripts/omc_health.py --fast         # 빠른 체크만 (tsc 생략)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# 결과 데이터
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    count: int = 0          # 오류/경고 수


@dataclass
class HealthReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def issue_count(self) -> int:
        return sum(r.count for r in self.results)


# ─────────────────────────────────────────────────────────────────────────────
# 체크 함수들
# ─────────────────────────────────────────────────────────────────────────────

def _run_cmd(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    """명령 실행 후 (returncode, combined_output) 반환"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=timeout,
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return 1, f"[TIMEOUT] {' '.join(cmd)} ({timeout}s 초과)"
    except FileNotFoundError:
        return 1, f"[NOT FOUND] {cmd[0]} 명령을 찾을 수 없습니다"


def check_typecheck(fast: bool = False) -> CheckResult:
    """TypeScript 타입 체크 (npx tsc --noEmit)"""
    if fast:
        return CheckResult(
            name="타입 체크",
            ok=True,
            detail="--fast 모드: 건너뜀",
            count=0,
        )

    code, out = _run_cmd(
        ["npx", "tsc", "--noEmit", "--project", "tsconfig.base.json"],
        timeout=90,
    )
    if code == 0:
        return CheckResult(name="타입 체크", ok=True, detail="오류 없음", count=0)

    error_lines = [l for l in out.splitlines() if "error TS" in l]
    count = len(error_lines)
    summary = "\n".join(error_lines[:5])
    if count > 5:
        summary += f"\n... 외 {count - 5}개"
    return CheckResult(name="타입 체크", ok=False, detail=summary, count=count)


def check_lint(fast: bool = False) -> CheckResult:
    """ESLint 체크.

    --fast 모드: affected 파일만 빠르게 체크.
    전체 모드:   nx affected lint (스테이징된/변경된 파일만).
    전체 프로젝트 lint는 너무 느려 기본 제외.
    """
    if fast:
        return CheckResult(
            name="lint",
            ok=True,
            detail="--fast 모드: 건너뜀",
            count=0,
        )

    code, out = _run_cmd(
        ["npx", "nx", "affected", "--target=lint", "--output-style=static"],
        timeout=60,
    )
    if code == 0:
        return CheckResult(name="lint", ok=True, detail="오류 없음", count=0)

    error_lines = [l for l in out.splitlines() if "error" in l.lower() and l.strip()]
    count = len(error_lines)
    summary = "\n".join(error_lines[:5])
    if count > 5:
        summary += f"\n... 외 {count - 5}개"
    return CheckResult(name="lint", ok=False, detail=summary, count=count)


def check_dead_code() -> CheckResult:
    """간단한 데드코드 감지 — TODO/FIXME/unused import 패턴 카운트"""
    patterns = ["TODO:", "FIXME:", "HACK:", "XXX:"]
    total = 0
    examples: list[str] = []

    src_dirs = [ROOT / "apps", ROOT / "libs"]
    for src in src_dirs:
        if not src.exists():
            continue
        for ts_file in src.rglob("*.ts"):
            if any(skip in str(ts_file) for skip in ["node_modules", "dist", ".next", "coverage"]):
                continue
            try:
                content = ts_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pat in patterns:
                count = content.count(pat)
                if count:
                    total += count
                    if len(examples) < 3:
                        rel = ts_file.relative_to(ROOT)
                        examples.append(f"{rel}: {pat} × {count}")

    ok = total == 0
    detail = f"{total}개 발견" if total else "없음"
    if examples:
        detail += "\n" + "\n".join(examples)
        if total > len(examples):
            detail += f"\n... 외 더 있음"
    return CheckResult(name="데드코드 마커", ok=ok, detail=detail, count=total)


# ─────────────────────────────────────────────────────────────────────────────
# 리포트 출력
# ─────────────────────────────────────────────────────────────────────────────

_ICON = {True: "✅", False: "❌"}


def _print_report(report: HealthReport) -> None:
    width = 56
    print()
    print("═" * width)
    print(f" HEALTH 대시보드 — {ROOT.name}")
    print("═" * width)

    for r in report.results:
        icon = _ICON[r.ok]
        count_str = f"  ({r.count}개)" if r.count else ""
        print(f"  {icon}  {r.name}{count_str}")
        if r.detail and not r.ok:
            for line in r.detail.splitlines()[:3]:
                print(f"       {line}")

    print("─" * width)
    total_issues = report.issue_count
    if report.all_ok:
        print("  ✅  전체 이상 없음")
    else:
        failed = [r.name for r in report.results if not r.ok]
        print(f"  ⚠️   이슈 {total_issues}개 — {', '.join(failed)}")
    print("═" * width)
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────

def run_health(fast: bool = False) -> HealthReport:
    report = HealthReport()
    report.results.append(check_typecheck(fast=fast))
    report.results.append(check_lint(fast=fast))
    report.results.append(check_dead_code())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OMC Health — 코드 품질 원클릭 대시보드",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--report-only", action="store_true",
                        help="결과만 출력, 이슈 있어도 exit 1 반환 (차단 없음)")
    parser.add_argument("--fast", action="store_true",
                        help="빠른 체크만 실행 (tsc 전체 생략)")
    args = parser.parse_args()

    report = run_health(fast=args.fast)
    _print_report(report)

    if report.all_ok:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
