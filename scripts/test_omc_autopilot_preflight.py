"""
test_omc_autopilot_preflight.py — pre-flight 체크 회귀 방지

T1: 빈 --instruction → exit 非0
T2: 빈 --branch → exit 非0
T3: 지시문 10자 미만 + --force 없음 → exit 非0 + 경고 메시지
T4: 지시문 10자 미만 + --force → 진행 (dry-run exit 0)
T5: uncommitted 변경 경고 (abort 아님, 경고만)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT = ROOT / "scripts" / "omc_autopilot.py"


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUTOPILOT)] + args,
        capture_output=True, text=True,
        cwd=cwd or str(ROOT),
    )


# ── T1: 빈 --instruction ─────────────────────────────────────────────────

def test_empty_instruction_exits_nonzero():
    """--instruction '' 은 exit 非0이어야 한다."""
    r = _run(["pipeline", "--instruction", "", "--branch", "fix/x", "--dry-run"])
    assert r.returncode != 0, f"빈 instruction에서 exit 0 — stdout: {r.stdout[:200]}"


# ── T2: 빈 --branch ──────────────────────────────────────────────────────

def test_empty_branch_exits_nonzero():
    """--branch '' 은 exit 非0이어야 한다."""
    r = _run(["pipeline", "--instruction", "테스트 지시문", "--branch", "", "--dry-run"])
    assert r.returncode != 0, f"빈 branch에서 exit 0 — stdout: {r.stdout[:200]}"


# ── T3: 지시문 10자 미만 → 경고 + exit 非0 ──────────────────────────────

def test_short_instruction_exits_nonzero():
    """10자 미만 지시문은 --force 없으면 exit 非0이어야 한다."""
    r = _run(["pipeline", "--instruction", "짧음", "--branch", "fix/x", "--dry-run"])
    assert r.returncode != 0, f"짧은 instruction에서 exit 0"


def test_short_instruction_shows_warning():
    """10자 미만 지시문은 경고 메시지를 출력해야 한다."""
    r = _run(["pipeline", "--instruction", "짧음", "--branch", "fix/x", "--dry-run"])
    combined = r.stdout + r.stderr
    assert any(kw in combined for kw in ("지시문", "짧", "short", "--force")), (
        f"경고 메시지 없음 — output: {combined[:300]}"
    )


# ── T4: --force로 짧은 지시문 통과 ───────────────────────────────────────

def test_short_instruction_with_force_exits_zero():
    """--force 플래그 시 짧은 지시문도 dry-run exit 0이어야 한다."""
    r = _run([
        "pipeline",
        "--instruction", "짧음",
        "--branch", "fix/x",
        "--dry-run",
        "--force",
    ])
    assert r.returncode == 0, (
        f"--force에도 exit {r.returncode}\n"
        f"stdout: {r.stdout[-400:]}\nstderr: {r.stderr[-200:]}"
    )


# ── T5: uncommitted 변경 경고 (abort 아님) ────────────────────────────────

def test_uncommitted_change_shows_warning(tmp_path: Path):
    """uncommitted 변경이 있어도 --dry-run은 abort가 아닌 경고만 해야 한다."""
    # tmp_path는 clean git repo가 아니라 uncommitted 감지 로직은
    # root(ROOT)에서만 동작하므로, 실제 uncommitted 파일 생성 후 테스트
    dirty_file = ROOT / "scripts" / "_test_dirty_file_delete_me.txt"
    try:
        dirty_file.write_text("dirty")
        r = _run([
            "pipeline",
            "--instruction", "충분히 긴 테스트 지시문입니다",
            "--branch", "fix/test-dirty",
            "--dry-run",
        ])
        combined = r.stdout + r.stderr
        # uncommitted 변경이 있어도 dry-run은 진행돼야 함
        assert r.returncode == 0, (
            f"uncommitted 변경으로 abort됨 (abort 아닌 경고만 허용)\n"
            f"stdout: {r.stdout[-400:]}"
        )
        assert any(kw in combined for kw in ("uncommitted", "변경", "dirty", "⚠")), (
            f"uncommitted 경고 메시지 없음 — output: {combined[:300]}"
        )
    finally:
        dirty_file.unlink(missing_ok=True)


# ── dirty 워크트리 차단 / --allow-dirty ─────────────────────────────────

def test_dirty_without_flag_exits_nonzero(tmp_path: Path):
    """uncommitted 변경이 있고 --allow-dirty 없으면 exit 1 해야 한다.
    (dry-run 아닌 실제 실행 시뮬레이션 — 하지만 실제 LLM 호출은 없음)"""
    dirty_file = ROOT / "scripts" / "_test_dirty_flag_delete_me.txt"
    try:
        dirty_file.write_text("dirty for allow-dirty test")
        r = _run([
            "pipeline",
            "--instruction", "충분히 긴 테스트 지시문입니다",
            "--branch", "fix/dirty-block-test",
            # --dry-run 없음 → 실제 실행 경로 → dirty 차단 기대
            # 단, LLM 호출 전에 차단돼야 하므로 exit 1이 빠르게 떨어져야 함
        ])
        combined = r.stdout + r.stderr
        assert r.returncode != 0, (
            f"dirty 상태에서 --allow-dirty 없이 exit 0 반환됨 (차단 기대)\n"
            f"stdout: {r.stdout[-400:]}"
        )
        assert any(kw in combined for kw in ("allow-dirty", "allow_dirty", "dirty", "stash", "commit")), (
            f"dirty 차단 안내 메시지 없음 — output: {combined[:300]}"
        )
    finally:
        dirty_file.unlink(missing_ok=True)


def test_dirty_with_allow_dirty_flag_continues(tmp_path: Path):
    """--allow-dirty 플래그가 있으면 dirty 상태에서도 dry-run이 계속 진행돼야 한다."""
    dirty_file = ROOT / "scripts" / "_test_dirty_flag_delete_me.txt"
    try:
        dirty_file.write_text("dirty for allow-dirty test")
        r = _run([
            "pipeline",
            "--instruction", "충분히 긴 테스트 지시문입니다",
            "--branch", "fix/dirty-allow-test",
            "--allow-dirty",
            "--dry-run",
        ])
        assert r.returncode == 0, (
            f"--allow-dirty + --dry-run 인데 exit nonzero\n"
            f"stdout: {r.stdout[-400:]}"
        )
    finally:
        dirty_file.unlink(missing_ok=True)
