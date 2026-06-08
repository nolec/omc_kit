#!/usr/bin/env python3
"""omc-pipeline-check.sh OMC_BLOCK_EXIT 환경변수 테스트 — stdlib only

가짜 omc_pipeline_guard.py를 주입해 실제 차단 동작을 유발하고
OMC_BLOCK_EXIT에 따라 exit code가 바뀌는지 검증한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).parent.parent / ".agent-hooks" / "omc-pipeline-check.sh")

_FAKE_GUARD_BLOCK = """\
#!/usr/bin/env python3
# 항상 차단하는 가짜 파이프라인 가드
import sys
print("[OMC BLOCK] 테스트용 강제 차단")
sys.exit(1)
"""

_FAKE_GUARD_ALLOW = """\
#!/usr/bin/env python3
# 항상 허용하는 가짜 파이프라인 가드
import sys
sys.exit(0)
"""


def _run_check(payload: dict, fake_guard: str, env_extra: dict | None = None) -> int:
    """가짜 가드를 주입하고 파이프라인 체크 스크립트를 실행해 exit code를 반환한다."""
    with tempfile.TemporaryDirectory() as tmp:
        scripts_dir = Path(tmp) / "scripts"
        scripts_dir.mkdir()
        guard = scripts_dir / "omc_pipeline_guard.py"
        guard.write_text(fake_guard, encoding="utf-8")

        env = {
            **os.environ,
            **(env_extra or {}),
        }

        result = subprocess.run(
            ["sh", SCRIPT],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp,
        )
        return result.returncode


class TestBlockExitCode(unittest.TestCase):
    def _write_payload(self) -> dict:
        return {"tool_name": "Write", "tool_input": {"file_path": "scripts/foo.py"}}

    def test_default_block_exit_is_2(self):
        """OMC_BLOCK_EXIT 미설정 시 차단 exit code는 2 (Claude Code 기본)."""
        code = _run_check(self._write_payload(), _FAKE_GUARD_BLOCK)
        self.assertEqual(code, 2, f"기본 차단 코드는 2여야 함, 실제: {code}")

    def test_block_exit_1_when_env_set(self):
        """OMC_BLOCK_EXIT=1 설정 시 차단 exit code가 1이 된다 (Codex 호환)."""
        code = _run_check(self._write_payload(), _FAKE_GUARD_BLOCK, {"OMC_BLOCK_EXIT": "1"})
        self.assertEqual(code, 1, f"OMC_BLOCK_EXIT=1이면 exit 1이어야 함, 실제: {code}")

    def test_allow_is_always_exit_0(self):
        """가드가 허용하면 OMC_BLOCK_EXIT 값과 무관하게 exit 0이다."""
        for env_extra in [{}, {"OMC_BLOCK_EXIT": "1"}]:
            with self.subTest(env=env_extra):
                code = _run_check(self._write_payload(), _FAKE_GUARD_ALLOW, env_extra)
                self.assertEqual(code, 0)


    def test_confirmed_session_allows_edit(self):
        """confirmed + contract_confirmed=True 일 때 파일 수정 exit 0이어야 한다."""
        import json, os, subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path as P
            root = P(tmp)
            (root / ".omc" / "state").mkdir(parents=True)
            (root / ".omc" / "policy.json").write_text(
                json.dumps({"enforce_confirm": True}), encoding="utf-8"
            )
            latest = {
                "latest_confirmation": {"status": "confirmed"},
                "latest_confirmed_request": "test task",
            }
            (root / ".omc" / "state" / "latest.json").write_text(
                json.dumps(latest), encoding="utf-8"
            )
            # pipeline_session.json — contract_confirmed=True
            (root / ".omc" / "pipeline_session.json").write_text(
                json.dumps({"contract_confirmed": True, "red_done_tests": [], "allowed_impl_files": []}),
                encoding="utf-8"
            )
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "omc_pipeline_guard.py").write_text(
                _FAKE_GUARD_ALLOW, encoding="utf-8"
            )
            payload = {"tool_name": "Edit",
                       "tool_input": {"file_path": "scripts/omc_state.py"}}
            env = {**os.environ, "OMC_BLOCK_EXIT": "2"}
            result = subprocess.run(
                ["sh", SCRIPT], input=json.dumps(payload),
                capture_output=True, text=True, env=env, cwd=str(root),
            )
            self.assertEqual(
                result.returncode, 0,
                f"confirmed+contract_confirmed=True → exit 0. stdout={result.stdout!r}"
            )

    def test_confirmed_no_contract_blocks_edit(self):
        """confirmed 상태이지만 contract_confirmed 없으면 차단(exit 2)이어야 한다."""
        import json, os, subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path as P
            root = P(tmp)
            (root / ".omc" / "state").mkdir(parents=True)
            (root / ".omc" / "policy.json").write_text(
                json.dumps({"enforce_confirm": True}), encoding="utf-8"
            )
            latest = {
                "latest_confirmation": {"status": "confirmed"},
                "latest_confirmed_request": "test task",
            }
            (root / ".omc" / "state" / "latest.json").write_text(
                json.dumps(latest), encoding="utf-8"
            )
            # pipeline_session.json 없음 → contract_confirmed=False
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "omc_pipeline_guard.py").write_text(
                _FAKE_GUARD_ALLOW, encoding="utf-8"
            )
            payload = {"tool_name": "Edit",
                       "tool_input": {"file_path": "scripts/omc_state.py"}}
            env = {**os.environ, "OMC_BLOCK_EXIT": "2"}
            result = subprocess.run(
                ["sh", SCRIPT], input=json.dumps(payload),
                capture_output=True, text=True, env=env, cwd=str(root),
            )
            self.assertEqual(
                result.returncode, 2,
                f"confirmed+no_contract → exit 2. stdout={result.stdout!r}"
            )


    def test_empty_status_allows_edit(self):
        """status가 빈 문자열(첫 설치 등)이면 exit 0이어야 한다."""
        import json, os, subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path as P
            root = P(tmp)
            (root / ".omc" / "state").mkdir(parents=True)
            (root / ".omc" / "policy.json").write_text(
                json.dumps({"enforce_confirm": True}), encoding="utf-8"
            )
            # status 없음 — 첫 설치 직후 상태
            latest = {"latest_confirmed_request": ""}
            (root / ".omc" / "state" / "latest.json").write_text(
                json.dumps(latest), encoding="utf-8"
            )
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "omc_pipeline_guard.py").write_text(
                _FAKE_GUARD_ALLOW, encoding="utf-8"
            )
            payload = {"tool_name": "Edit",
                       "tool_input": {"file_path": "scripts/omc_state.py"}}
            env = {**os.environ, "OMC_BLOCK_EXIT": "2"}
            result = subprocess.run(
                ["sh", SCRIPT], input=json.dumps(payload),
                capture_output=True, text=True, env=env, cwd=str(root),
            )
            self.assertEqual(
                result.returncode, 0,
                f"status='' → exit 0이어야 함. stdout={result.stdout!r}"
            )

    def test_empty_payload_always_exits_0(self):
        """tool_name 없는 빈 payload는 항상 exit 0이다."""
        code = _run_check({}, _FAKE_GUARD_BLOCK)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
