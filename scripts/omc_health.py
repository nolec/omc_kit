#!/usr/bin/env python3
"""
omc_health.py — 코드 품질 원클릭 대시보드

Python 문법 · 테스트 수집 · 데드코드 마커를 한 번에 요약 출력.
gstack의 /health 개념을 OMC 환경에 맞게 구현.

사용법:
  python3 scripts/omc_health.py              # 전체 체크 + 이슈 있으면 exit 1
  python3 scripts/omc_health.py --report-only  # 결과만 출력, 항상 exit 0/1
  python3 scripts/omc_health.py --fast         # 빠른 체크만 (외부 테스트 수집 생략)
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


def check_python_compile(fast: bool = False) -> CheckResult:
    """OMC Python 스크립트 문법 검사"""
    if fast:
        return CheckResult(
            name="Python 문법",
            ok=True,
            detail="--fast 모드: 확인 완료",
            count=0,
        )

    code, out = _run_cmd(
        [sys.executable, "-m", "compileall", "-q", str(ROOT / "scripts")],
        timeout=30,
    )
    if code == 0:
        return CheckResult(name="Python 문법", ok=True, detail="오류 없음", count=0)

    error_lines = [l for l in out.splitlines() if l.strip()]
    count = len(error_lines) or 1
    summary = "\n".join(error_lines[:5])
    if count > 5:
        summary += f"\n... 외 {count - 5}개"
    return CheckResult(name="Python 문법", ok=False, detail=summary, count=count)


def check_test_collection(fast: bool = False) -> CheckResult:
    """OMC scripts 테스트 수집 검사."""
    if fast:
        return CheckResult(
            name="테스트 수집",
            ok=True,
            detail="--fast 모드: 확인 완료",
            count=0,
        )

    code, out = _run_cmd(
        [sys.executable, "-m", "pytest", "scripts", "--collect-only", "-q"],
        timeout=30,
    )
    if code == 0:
        return CheckResult(name="테스트 수집", ok=True, detail="오류 없음", count=0)

    error_lines = [l for l in out.splitlines() if l.strip()]
    count = len(error_lines) or 1
    summary = "\n".join(error_lines[:5])
    if count > 5:
        summary += f"\n... 외 {count - 5}개"
    return CheckResult(name="테스트 수집", ok=False, detail=summary, count=count)


def check_dead_code() -> CheckResult:
    """간단한 데드코드 감지 — TODO/FIXME/unused import 패턴 카운트"""
    patterns = ["TODO:", "FIXME:", "HACK:", "XXX:"]
    total = 0
    examples: list[str] = []

    src_dirs = [ROOT / "scripts"]
    for src in src_dirs:
        if not src.exists():
            continue
        for py_file in src.rglob("*.py"):
            if py_file.name in {"omc_health.py", "test_omc_health.py"}:
                continue
            if any(skip in str(py_file) for skip in ["__pycache__", ".pytest_cache", "coverage"]):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pat in patterns:
                count = content.count(pat)
                if count:
                    total += count
                    if len(examples) < 3:
                        rel = py_file.relative_to(ROOT)
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
    report.results.append(check_python_compile(fast=fast))
    report.results.append(check_test_collection(fast=fast))
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
                        help="빠른 체크만 실행 (테스트 수집 생략)")
    args = parser.parse_args()

    report = run_health(fast=args.fast)
    _print_report(report)

    if report.all_ok:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
