#!/usr/bin/env python3
"""
omc_tdd_check.py — TDD 진짜 강제 스크립트 (Superpowers 방식)

git diff 기반으로 신규/수정된 구현 파일을 탐지하고,
대응 테스트 파일 존재 여부 확인 + 실제 테스트를 실행해 차단합니다.

사용:
  python3 scripts/omc_tdd_check.py                    # 기본: main 대비 신규 파일 체크
  python3 scripts/omc_tdd_check.py --base HEAD~1      # 최근 커밋 대비
  python3 scripts/omc_tdd_check.py --run-tests        # 테스트 실행 + human review gate
  python3 scripts/omc_tdd_check.py --run-tests --skip-review  # review gate 없이 통과 (CI용)
  python3 scripts/omc_tdd_check.py --staged           # staged 파일만 체크 (pre-commit용)
  python3 scripts/omc_tdd_check.py --report-only      # 차단하지 않고 보고만

환경 변수:
  OMC_SKIP_REVIEW=1  # --skip-review 와 동일 효과 (CI 환경 자동 감지)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
_IMPL_EXTENSIONS = {".ts", ".tsx", ".py", ".js", ".jsx"}
_EXCLUDE_PATTERNS = [
    r"\.spec\.",
    r"\.test\.",
    r"_test\.",
    r"test_",
    r"\.d\.ts$",
    r"\.config\.(ts|js)$",
    r"/types\.ts$",
    r"/types/",
    r"\.styled\.(ts|tsx)$",       # styled-components 파일
    r"/constants/",               # 상수 폴더
    r"\.constants\.(ts|tsx)$",    # Foo.constants.ts 파일명 패턴
    r"/index\.(ts|tsx|js)$",      # 진입점
    r"__test__",
    r"__mocks__",
    r"/node_modules/",
    r"\.min\.",
    r"/api\.ts$",                  # API 클라이언트 단순 wrapper
    r"/queryKey\.(ts|js)$",        # React Query key 파일
    r"\.stories\.(ts|tsx)$",       # Storybook
]


# ---------------------------------------------------------------------------
# Git 유틸
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    return result.stdout.strip()


def _parse_impl_files_from_git_output(raw: str, root: Path, status_filter: str | None = None) -> list[Path]:
    """git diff 출력(파일명만 또는 status\tfilename 형식)에서 구현 파일 목록 파싱.

    status_filter: 'A', 'M' 등 — 지정 시 해당 상태 파일만 포함.
    """
    files: list[Path] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # "M\tpath" 형식과 "path" 형식 모두 처리
        parts = line.split("\t", 1)
        if len(parts) == 2:
            status, filepath = parts[0].strip(), parts[1].strip()
            if status_filter and status != status_filter:
                continue
        else:
            filepath = parts[0]
        p = Path(filepath)
        if p.suffix not in _IMPL_EXTENSIONS:
            continue
        if any(re.search(pat, filepath.replace("\\", "/")) for pat in _EXCLUDE_PATTERNS):
            continue
        abs_p = root / p
        if abs_p.exists():
            files.append(p)
    return files


def _get_new_impl_files(root: Path, base: str, staged_only: bool) -> list[Path]:
    """신규 추가(A) 구현 파일 목록 (테스트·설정 파일 제외)"""
    if staged_only:
        raw = _git(["diff", "--cached", "--name-only", "--diff-filter=A"], root)
    else:
        raw = _git(["diff", "--name-only", "--diff-filter=A", f"{base}...HEAD"], root)
    return _parse_impl_files_from_git_output(raw, root)


def _get_modified_impl_files(root: Path, base: str, staged_only: bool) -> list[Path]:
    """수정된(M) 구현 파일 목록 (테스트·설정 파일 제외)"""
    if staged_only:
        raw = _git(["diff", "--cached", "--name-only", "--diff-filter=M"], root)
    else:
        raw = _git(["diff", "--name-only", "--diff-filter=M", f"{base}...HEAD"], root)
    return _parse_impl_files_from_git_output(raw, root, status_filter="M")


# ---------------------------------------------------------------------------
# 테스트 파일 탐색
# ---------------------------------------------------------------------------

def _find_test_file(impl: Path, root: Path) -> Path | None:
    """구현 파일에 대응하는 테스트 파일을 찾는다."""
    stem = impl.stem
    parent = impl.parent

    candidates = [
        # 같은 폴더
        parent / f"{stem}.spec.ts",
        parent / f"{stem}.spec.tsx",
        parent / f"{stem}.test.ts",
        parent / f"{stem}.test.tsx",
        parent / f"{stem}.spec.js",
        parent / f"{stem}.test.js",
        parent / f"test_{stem}.py",
        parent / f"{stem}_test.py",
        # __test__ 폴더
        parent / "__test__" / f"{stem}.spec.ts",
        parent / "__test__" / f"{stem}.test.ts",
        parent / "__tests__" / f"{stem}.spec.ts",
        parent / "__tests__" / f"{stem}.test.ts",
        # tests/ 상위 폴더
        parent.parent / "tests" / f"test_{stem}.py",
    ]
    for c in candidates:
        if (root / c).exists():
            return c
    return None


# ---------------------------------------------------------------------------
# 테스트 실행
# ---------------------------------------------------------------------------

def _detect_runner(root: Path) -> list[str] | None:
    """프로젝트 타입에 맞는 테스트 커맨드 감지"""
    if (root / "nx.json").exists():
        return None  # nx는 별도 처리
    if (root / "package.json").exists():
        return ["npx", "jest", "--passWithNoTests", "--findRelatedTests"]
    for name in ["pytest.ini", "setup.cfg", "pyproject.toml"]:
        if (root / name).exists():
            return ["pytest", "--tb=short", "-q"]
    return None


def _run_tests_for_files(impl_files: list[Path], root: Path, base: str) -> tuple[bool, str]:
    """관련 테스트를 실제로 실행하고 결과를 반환한다."""
    # nx affected 우선
    if (root / "nx.json").exists():
        cmd = ["npx", "nx", "affected", "--target=test", f"--base={base}", "--head=HEAD"]
        result = subprocess.run(cmd, cwd=str(root), capture_output=False, text=True)
        return result.returncode == 0, " ".join(cmd)

    runner = _detect_runner(root)
    if not runner:
        return True, "(테스트 러너 미감지 — 건너뜀)"

    file_args = [str(root / f) for f in impl_files]
    cmd = runner + file_args
    result = subprocess.run(cmd, cwd=str(root), capture_output=False, text=True)
    return result.returncode == 0, " ".join(runner)


# ── human review gate helpers ────────────────────────────────────────────────
# TTY 시뮬레이션: OMC_REVIEW_FORCE_PROMPT=1 이면 stdin.isatty() 대신 항상 프롬프트
_REVIEW_GATE_RESULT: bool | None = None  # True=통과, False=차단, None=미실행


def _should_prompt_review(skip_review: bool) -> bool:
    """review 프롬프트를 띄울지 여부를 결정한다."""
    if skip_review:
        return False
    if os.environ.get("OMC_SKIP_REVIEW") == "1":
        return False
    if os.environ.get("OMC_REVIEW_FORCE_PROMPT") == "1":
        return True
    return sys.stdin.isatty()


def _maybe_run_review_gate(skip_review: bool, already_blocked: bool) -> None:
    """Human Review Gate를 조건에 따라 실행하고 전역 결과를 저장한다."""
    global _REVIEW_GATE_RESULT
    _REVIEW_GATE_RESULT = None

    if not _should_prompt_review(skip_review):
        _REVIEW_GATE_RESULT = True
        return

    print()
    print(" ─── 🔍 Human Review Gate ─────────────────────────────────────")
    print("  테스트가 통과했습니다. 코드 리뷰를 완료했나요?")
    print("  [y] 확인 완료, 진행  [n] 아직, 중단")
    print(" ─────────────────────────────────────────────────────────────")
    try:
        answer = input("  선택 (y/n): ").strip().lower()
    except EOFError:
        answer = "y"

    if answer in ("y", "yes"):
        _REVIEW_GATE_RESULT = True
    else:
        print("[TDD] ⛔ Review Gate: 사용자가 중단했습니다.")
        _REVIEW_GATE_RESULT = False


def _review_gate_blocked(skip_review: bool) -> bool:
    """마지막 review gate 결과를 반환한다. False = 차단됨."""
    if _REVIEW_GATE_RESULT is None:
        return False
    return not _REVIEW_GATE_RESULT


# ---------------------------------------------------------------------------
# 메인 체크
# ---------------------------------------------------------------------------

def check(
    root: Path,
    base: str = "origin/main",
    staged_only: bool = False,
    run_tests: bool = False,
    report_only: bool = False,
    skip_review: bool = False,
) -> int:
    """0 = 통과, 1 = 차단"""
    added_files = _get_new_impl_files(root, base, staged_only)
    modified_files = _get_modified_impl_files(root, base, staged_only)

    if not added_files and not modified_files:
        print("[TDD] ✅ 신규 구현 파일 없음 — 통과")
        if run_tests:
            _maybe_run_review_gate(skip_review, False)
            if _review_gate_blocked(skip_review):
                return 1
        return 0

    # ── 신규 파일(A): 테스트 없으면 차단 ────────────────────────────────
    added_missing: list[Path] = []
    added_covered: list[Path] = []
    for f in added_files:
        test_file = _find_test_file(f, root)
        if test_file:
            added_covered.append(f)
        else:
            added_missing.append(f)

    if added_covered:
        print(f"[TDD] ✅ 신규 파일 — 테스트 있음 ({len(added_covered)}개):")
        for f in added_covered:
            print(f"       {f}")

    blocked = False

    if added_missing:
        print(f"\n[TDD] 🚨 신규 파일 — 테스트 파일 없음 ({len(added_missing)}개):")
        for f in added_missing:
            print(f"       {f}  ← 대응 테스트 파일 없음")
        print()
        print(" ─── 복구 절차 ─────────────────────────────────────────────────")
        for f in added_missing:
            stem = f.stem
            test_candidate = f"{f.parent}/{stem}.spec.ts"
            print(f" [{f}]")
            print(f"   1. 테스트 파일 생성: {test_candidate}")
            print(f"   2. 실패하는 테스트 케이스 작성")
            print(f"   3. 실행 → FAIL 출력 확인 (실제 출력 캡처):")
            print(f"        npx jest {test_candidate} --verbose 2>&1 | head -40")
            print(f"   4. RED 등록: python3 scripts/omc_pipeline_guard.py red-done {test_candidate}")
        print()
        print(" ─── 일괄 예외 허용 ─────────────────────────────────────────────")
        print("   python3 scripts/omc_tdd_check.py --staged --report-only")
        print(" ────────────────────────────────────────────────────────────────")
        blocked = True

    # ── 수정 파일(M): 테스트 없으면 경고(차단 아님) ──────────────────────
    modified_missing: list[Path] = []
    modified_covered: list[Path] = []
    for f in modified_files:
        test_file = _find_test_file(f, root)
        if test_file:
            modified_covered.append(f)
        else:
            modified_missing.append(f)

    if modified_covered:
        print(f"[TDD] ✅ 수정 파일 — 테스트 있음 ({len(modified_covered)}개):")
        for f in modified_covered:
            print(f"       {f}")

    if modified_missing:
        print(f"\n[TDD] ⚠️  수정 파일 — 테스트 파일 없음 ({len(modified_missing)}개) [경고]:")
        for f in modified_missing:
            print(f"       {f}  ← 대응 테스트 파일 권장")
        print()
        print(" ─── 테스트 추가를 권장합니다 ──────────────────────────────────")
        for f in modified_missing:
            stem = f.stem
            test_candidate = f"{f.parent}/{stem}.spec.ts"
            print(f"   {test_candidate}")
        print(" (수정 파일 미테스트는 경고만, 차단하지 않습니다)")
        print(" ────────────────────────────────────────────────────────────────")

    # 실제 테스트 실행
    all_impl_files = added_files + modified_files

    # OMC_SKIP_REAL_TESTS=1: 테스트 실행 자체를 건너뛰고 review gate만 테스트 (단위 테스트용)
    if os.environ.get("OMC_SKIP_REAL_TESTS") == "1":
        all_impl_files = []

    if run_tests and all_impl_files:
        print(f"\n[TDD] 🧪 테스트 실행 중...")
        passed, runner_cmd = _run_tests_for_files(all_impl_files, root, base)
        if not passed:
            print(f"[TDD] ❌ 테스트 실패: {runner_cmd}")
            blocked = True
        else:
            print(f"[TDD] ✅ 테스트 통과: {runner_cmd}")
            _maybe_run_review_gate(skip_review, blocked)
            if _review_gate_blocked(skip_review):
                blocked = True
    elif run_tests and not all_impl_files:
        # 파일은 없지만 --run-tests 플래그가 있으면 review gate만 실행
        _maybe_run_review_gate(skip_review, blocked)
        if _review_gate_blocked(skip_review):
            blocked = True

    if blocked:
        if report_only:
            print("\n[TDD] ⚠️  --report-only 모드 — 차단하지 않음")
            return 0
        return 1

    print("[TDD] ✅ TDD 체크 통과")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="OMC TDD 강제 체크")
    ap.add_argument("--base", default="origin/main", help="비교 기준 브랜치/커밋 (기본: origin/main)")
    ap.add_argument("--staged", action="store_true", help="staged 파일만 체크 (pre-commit 모드)")
    ap.add_argument("--run-tests", action="store_true", help="테스트 파일 존재 확인 후 실제 테스트도 실행")
    ap.add_argument("--report-only", action="store_true", help="차단하지 않고 보고만 (경고 모드)")
    ap.add_argument("--skip-review", action="store_true", help="human review gate 없이 자동 통과 (CI 환경)")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="프로젝트 루트")
    args = ap.parse_args()

    return check(
        root=args.target.resolve(),
        base=args.base,
        staged_only=args.staged,
        run_tests=args.run_tests,
        report_only=args.report_only,
        skip_review=args.skip_review,
    )


if __name__ == "__main__":
    raise SystemExit(main())
