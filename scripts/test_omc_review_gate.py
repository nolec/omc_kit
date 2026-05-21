#!/usr/bin/env python3
"""
test_omc_review_gate.py — omc_tdd_check.py review 확인 단계 단위 테스트

검증 항목:
  1. --run-tests + 터미널 stdin + "y" 입력 → 통과 (exit 0)
  2. --run-tests + 터미널 stdin + "n" 입력 → 차단 (exit 1)
  3. --run-tests + stdin 파이프(CI) → review 묻지 않고 자동 통과
  4. --run-tests + --skip-review → review 묻지 않고 통과
  5. --staged (pre-commit) → review 확인 없음
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "omc_tdd_check.py"


def _run(args: list[str], *, stdin_input: str | None = None, is_tty: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT)] + args
    if is_tty:
        # 실제 터미널 시뮬레이션은 불가하므로 REVIEW_TTY_OVERRIDE 환경변수로 처리
        import os
        env = {**__import__("os").environ, "OMC_REVIEW_FORCE_PROMPT": "1", "OMC_SKIP_REAL_TESTS": "1"}
    else:
        import os
        env = {**os.environ, "OMC_SKIP_REAL_TESTS": "1"}
        env.pop("OMC_REVIEW_FORCE_PROMPT", None)

    return subprocess.run(
        cmd,
        input=stdin_input,
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
        cwd=str(ROOT),
    )


def test_review_gate_yes_passes() -> None:
    """OMC_REVIEW_FORCE_PROMPT=1 + 'y' 입력 → exit 0."""
    result = _run(["--run-tests", "--staged"], stdin_input="y\n", is_tty=True)
    assert result.returncode == 0, (
        f"'y' 입력 시 통과해야 함 (got {result.returncode})\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_review_gate_no_blocks() -> None:
    """OMC_REVIEW_FORCE_PROMPT=1 + 'n' 입력 → exit 1."""
    result = _run(["--run-tests", "--staged"], stdin_input="n\n", is_tty=True)
    assert result.returncode == 1, (
        f"'n' 입력 시 차단해야 함 (got {result.returncode})\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_review_gate_ci_skips() -> None:
    """stdin 파이프(CI) → review 묻지 않고 자동 통과."""
    result = _run(["--run-tests", "--staged"])
    # stdin이 파이프이면 review 프롬프트 없이 통과, exit 0이어야 함
    assert result.returncode == 0, (
        f"CI(pipe stdin) 환경에서는 review 강제 없이 통과해야 함 (got {result.returncode})\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "review" not in result.stdout.lower() or "완료" not in result.stdout, (
        "CI 환경에서 review 프롬프트가 출력되어서는 안 됨"
    )


def test_skip_review_flag() -> None:
    """--skip-review 플래그 → review 묻지 않고 통과."""
    result = _run(["--run-tests", "--staged", "--skip-review"])
    assert result.returncode == 0, (
        f"--skip-review 플래그 사용 시 통과해야 함 (got {result.returncode})\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_staged_only_no_review_prompt() -> None:
    """--staged 단독 (run-tests 없음) → review 확인 없음."""
    result = _run(["--staged"])
    combined = result.stdout + result.stderr
    assert "review" not in combined.lower() or "완료" not in combined, (
        "--staged 단독 실행 시 review 프롬프트가 없어야 함"
    )


def main() -> int:
    tests = [
        test_review_gate_yes_passes,
        test_review_gate_no_blocks,
        test_review_gate_ci_skips,
        test_skip_review_flag,
        test_staged_only_no_review_prompt,
    ]
    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except (AssertionError, Exception) as exc:
            print(f"  FAIL  {fn.__name__}\n        {exc}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"결과: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
