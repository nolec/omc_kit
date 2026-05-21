#!/usr/bin/env python3
"""
test_omc_hook_block.py — 훅 차단 로직 단위 테스트

검증 항목:
  1. omc-prompt-inject.sh: confirmed 상태 + 긴 프롬프트 → 세션 경고 포함
  2. omc-prompt-inject.sh: 30자 미만 프롬프트 → 세션 경고 없음(스킵)
  3. omc-prompt-inject.sh: pending 상태 → 경고 없음
  4. .cursor/hooks/omc-pipeline-check.sh: confirmed + create_file → permission:deny
  5. .cursor/hooks/omc-pipeline-check.sh: pending + create_file → permission:allow
  6. .agent-hooks/omc-pipeline-check.sh: confirmed + Write → exit 2
  7. .agent-hooks/omc-pipeline-check.sh: pending + Write → exit 0
  8. omc_doctor.py: 훅 차단 로직 검사 항목(hook_block) 존재
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ─────────────────────────────────────────────────────────────
# 헬퍼: 임시 .omc 디렉터리 세팅
# ─────────────────────────────────────────────────────────────

def _make_omc_state(tmp: Path, *, status: str, request: str = "테스트 작업") -> str:
    """tmp 디렉터리 아래에 .omc/policy.json + .omc/state/latest.json 생성.
    매 호출마다 고유 session_id를 생성해 /tmp warned 플래그 충돌을 방지한다."""
    session_id = f"test-{uuid.uuid4().hex[:8]}"
    omc = tmp / ".omc"
    (omc / "state").mkdir(parents=True, exist_ok=True)
    (omc / "policy.json").write_text(
        json.dumps({"enforce_confirm": True}), encoding="utf-8"
    )
    (omc / "state" / "latest.json").write_text(
        json.dumps({
            "latest_session_id": session_id,
            "latest_confirmed_session_id": session_id,
            "latest_confirmed_request": request,
            "latest_confirmation": {"status": status},
        }),
        encoding="utf-8",
    )
    return session_id


def _run_hook(hook_path: Path, *, prompt: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PROMPT"] = prompt
    env["PYTHON_BIN"] = sys.executable
    return subprocess.run(
        ["bash", str(hook_path)],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,  # stdin을 닫아 블로킹 방지, PROMPT 환경변수로 전달
        capture_output=True,
        text=True,
        timeout=15,
    )


def _run_pipeline_check(hook_path: Path, *, tool_name: str, file_path: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    tool_input = json.dumps({
        "tool_name": tool_name,
        "tool": tool_name,
        "tool_input": {"file_path": file_path},
        "params": {"target_file": file_path},
    })
    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    return subprocess.run(
        ["bash", str(hook_path)],
        cwd=str(cwd),
        env=env,
        input=tool_input,
        capture_output=True,
        text=True,
        timeout=15,
    )


# ─────────────────────────────────────────────────────────────
# 테스트 함수
# ─────────────────────────────────────────────────────────────

def test_prompt_inject_warns_on_confirmed(tmp_path: Path) -> None:
    """confirmed 상태 + 긴 프롬프트 → stdout에 [OMC] 또는 세션 경고 포함."""
    _make_omc_state(tmp_path, status="confirmed")
    hook = ROOT / ".agent-hooks" / "omc-prompt-inject.sh"
    result = _run_hook(hook, prompt="새로운 기능을 추가해 주세요. 충분히 긴 프롬프트입니다.", cwd=tmp_path)
    combined = result.stdout + result.stderr
    assert "OMC" in combined or "세션" in combined or "작업" in combined, (
        f"confirmed 상태에서 경고가 없음\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_prompt_inject_skips_short_prompt(tmp_path: Path) -> None:
    """30자 미만 프롬프트 → 세션 경고 없음."""
    _make_omc_state(tmp_path, status="confirmed")
    hook = ROOT / ".agent-hooks" / "omc-prompt-inject.sh"
    result = _run_hook(hook, prompt="응", cwd=tmp_path)
    combined = result.stdout + result.stderr
    assert "[OMC BLOCK]" not in combined and "활성 세션 없음" not in combined, (
        f"짧은 프롬프트에 경고가 나와서는 안 됨\nstdout={result.stdout!r}"
    )


def test_prompt_inject_no_warn_on_pending(tmp_path: Path) -> None:
    """pending 상태 → 세션 경고 없음."""
    _make_omc_state(tmp_path, status="pending")
    hook = ROOT / ".agent-hooks" / "omc-prompt-inject.sh"
    result = _run_hook(hook, prompt="새로운 기능을 추가해 주세요. 충분히 긴 프롬프트입니다.", cwd=tmp_path)
    combined = result.stdout + result.stderr
    assert "[OMC BLOCK]" not in combined and "활성 세션 없음" not in combined, (
        f"pending 상태에서 차단 경고가 나와서는 안 됨\nstdout={result.stdout!r}"
    )


def test_cursor_hook_denies_on_confirmed(tmp_path: Path) -> None:
    """Cursor 훅: confirmed + create_file → permission:deny."""
    _make_omc_state(tmp_path, status="confirmed")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "omc_pipeline_guard.py").write_text(
        "import sys; sys.exit(0)\n", encoding="utf-8"
    )
    hook = ROOT / ".cursor" / "hooks" / "omc-pipeline-check.sh"
    result = _run_pipeline_check(hook, tool_name="create_file", file_path="src/new.ts", cwd=tmp_path)
    combined = result.stdout + result.stderr
    assert '"permission": "deny"' in combined or "deny" in combined, (
        f"confirmed 상태에서 create_file이 차단되어야 함\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cursor_hook_allows_on_pending(tmp_path: Path) -> None:
    """Cursor 훅: pending + create_file → permission:allow."""
    _make_omc_state(tmp_path, status="pending")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "omc_pipeline_guard.py").write_text(
        "import sys; sys.exit(0)\n", encoding="utf-8"
    )
    hook = ROOT / ".cursor" / "hooks" / "omc-pipeline-check.sh"
    result = _run_pipeline_check(hook, tool_name="create_file", file_path="src/new.ts", cwd=tmp_path)
    assert '"permission": "allow"' in result.stdout or result.returncode == 0, (
        f"pending 상태에서 create_file이 허용되어야 함\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_claude_hook_exits2_on_confirmed(tmp_path: Path) -> None:
    """.agent-hooks/omc-pipeline-check.sh: confirmed + Write → exit 2."""
    _make_omc_state(tmp_path, status="confirmed")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "omc_pipeline_guard.py").write_text(
        "import sys; sys.exit(0)\n", encoding="utf-8"
    )
    hook = ROOT / ".agent-hooks" / "omc-pipeline-check.sh"
    result = _run_pipeline_check(hook, tool_name="Write", file_path="src/new.py", cwd=tmp_path)
    assert result.returncode == 2, (
        f"confirmed 상태에서 Write는 exit 2로 차단되어야 함 (got {result.returncode})\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_claude_hook_allows_on_pending(tmp_path: Path) -> None:
    """.agent-hooks/omc-pipeline-check.sh: pending + Write → exit 0."""
    _make_omc_state(tmp_path, status="pending")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "omc_pipeline_guard.py").write_text(
        "import sys; sys.exit(0)\n", encoding="utf-8"
    )
    hook = ROOT / ".agent-hooks" / "omc-pipeline-check.sh"
    result = _run_pipeline_check(hook, tool_name="Write", file_path="src/new.py", cwd=tmp_path)
    assert result.returncode == 0, (
        f"pending 상태에서 Write는 exit 0으로 허용되어야 함 (got {result.returncode})\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_doctor_has_hook_block_check() -> None:
    """omc_doctor.py에 훅 차단 로직 검사 항목이 존재해야 함."""
    doctor_path = ROOT / "scripts" / "omc_doctor.py"
    assert doctor_path.exists(), "scripts/omc_doctor.py 없음"
    content = doctor_path.read_text(encoding="utf-8")
    assert "hook_block" in content or "세션 없음" in content or "차단 로직" in content, (
        "omc_doctor.py에 훅 차단 로직 검사 항목이 없음 — 'hook_block' 또는 '차단 로직' 키워드 필요"
    )


# ─────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        test_prompt_inject_warns_on_confirmed,
        test_prompt_inject_skips_short_prompt,
        test_prompt_inject_no_warn_on_pending,
        test_cursor_hook_denies_on_confirmed,
        test_cursor_hook_allows_on_pending,
        test_claude_hook_exits2_on_confirmed,
        test_claude_hook_allows_on_pending,
        test_doctor_has_hook_block_check,
    ]
    passed = 0
    failed = 0
    for fn in tests:
        name = fn.__name__
        if fn.__code__.co_varnames[:1] == ("tmp_path",):
            with tempfile.TemporaryDirectory() as d:
                arg = Path(d)
                try:
                    fn(arg)
                    print(f"  PASS  {name}")
                    passed += 1
                except (AssertionError, Exception) as exc:
                    print(f"  FAIL  {name}\n        {exc}")
                    failed += 1
        else:
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except (AssertionError, Exception) as exc:
                print(f"  FAIL  {name}\n        {exc}")
                failed += 1

    print(f"\n{'='*50}")
    print(f"결과: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
