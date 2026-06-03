#!/usr/bin/env python3
"""omc-post-file-check.sh PostToolUse 소프트 가드 테스트 — stdlib only

Codex PostToolUse: 파일 수정 직후 세션 미확인이면 경고를 stdout/stderr에 출력.
차단(exit 2)이 아닌 메시지 출력(exit 0) → Codex가 컨텍스트로 받아 스스로 인식.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).parent.parent / ".agent-hooks" / "omc-post-file-check.sh")

# 세션 없음(pending) 상태를 시뮬레이션하는 가짜 policy + latest
_POLICY_ENFORCE = '{"enforce_confirm": true}'
_LATEST_PENDING = '{"latest_confirmation": {"status": "pending"}, "latest_confirmed_request": "test"}'
_LATEST_CONFIRMED = '{"latest_confirmation": {"status": "confirmed"}, "latest_confirmed_request": "test"}'


def _run(payload: dict, policy: str, latest: str) -> tuple[int, str, str]:
    """post-file-check.sh 실행 → (returncode, stdout, stderr)"""
    with tempfile.TemporaryDirectory() as tmp:
        omc_dir = Path(tmp) / ".omc"
        omc_dir.mkdir()
        (omc_dir / "policy.json").write_text(policy)
        state_dir = omc_dir / "state"
        state_dir.mkdir()
        (state_dir / "latest.json").write_text(latest)

        result = subprocess.run(
            ["sh", SCRIPT],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=os.environ,
            cwd=tmp,
        )
        return result.returncode, result.stdout, result.stderr


def _payload(tool_name: str = "apply_patch", file_path: str = "scripts/foo.py") -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "tool_result": {"success": True},
    }


class TestPostFileCheck(unittest.TestCase):
    def test_pending_session_emits_warning(self):
        """세션 미확인(pending) 상태에서 파일 수정 → 경고 메시지 출력."""
        code, out, err = _run(_payload(), _POLICY_ENFORCE, _LATEST_PENDING)
        combined = out + err
        self.assertEqual(code, 0, "PostToolUse는 차단 아닌 경고만 (exit 0)")
        self.assertTrue(
            any(kw in combined for kw in ("[OMC WARNING]", "CONTRACT", "OMC")),
            f"경고 메시지 없음, 실제 출력: {combined!r}",
        )

    def test_confirmed_session_no_warning(self):
        """세션 확인됨(confirmed) → 경고 없음, exit 0."""
        code, out, err = _run(_payload(), _POLICY_ENFORCE, _LATEST_CONFIRMED)
        combined = out + err
        self.assertEqual(code, 0)
        self.assertNotIn("[OMC WARNING]", combined)

    def test_no_policy_file_is_silent(self):
        """policy.json 없으면 조용히 exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["sh", SCRIPT],
                input=json.dumps(_payload()),
                capture_output=True,
                text=True,
                env=os.environ,
                cwd=tmp,
            )
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("[OMC WARNING]", result.stdout + result.stderr)

    def test_enforce_false_is_silent(self):
        """enforce_confirm=false → 경고 없음."""
        code, out, err = _run(_payload(), '{"enforce_confirm": false}', _LATEST_PENDING)
        self.assertEqual(code, 0)
        self.assertNotIn("[OMC WARNING]", out + err)

    def test_warning_includes_file_path(self):
        """경고 메시지에 수정된 파일 경로 포함."""
        code, out, err = _run(
            _payload(file_path="scripts/omc_context.py"),
            _POLICY_ENFORCE,
            _LATEST_PENDING,
        )
        combined = out + err
        self.assertIn("omc_context.py", combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
