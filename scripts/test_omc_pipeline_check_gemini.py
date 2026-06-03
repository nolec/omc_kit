#!/usr/bin/env python3
"""omc-pipeline-check.sh Gemini BeforeTool 입력 형식 테스트 — stdlib only

Gemini CLI BeforeTool 훅 stdin 형식:
{
  "session_id": "...",
  "cwd": "/path/to/project",
  "hook_event_name": "BeforeTool",
  "tool_name": "write_file",
  "tool_input": { "file_path": "...", "content": "..." }
}
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).parent.parent / ".agent-hooks" / "omc-pipeline-check.sh")

_FAKE_GUARD_BLOCK = """\
#!/usr/bin/env python3
import sys
print("[OMC BLOCK] 테스트용 강제 차단")
sys.exit(1)
"""

_FAKE_GUARD_ALLOW = """\
#!/usr/bin/env python3
import sys
sys.exit(0)
"""


def _run(payload: dict, fake_guard: str, env_extra: dict | None = None) -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        scripts_dir = Path(tmp) / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "omc_pipeline_guard.py").write_text(fake_guard)
        env = {**os.environ, **(env_extra or {})}
        result = subprocess.run(
            ["sh", SCRIPT],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp,
        )
        return result.returncode


def _gemini_payload(tool_name: str, file_path: str = "src/foo.py") -> dict:
    """Gemini BeforeTool 표준 stdin 페이로드."""
    return {
        "session_id": "test-session-123",
        "cwd": "/tmp/testproject",
        "hook_event_name": "BeforeTool",
        "timestamp": "2026-06-03T22:00:00Z",
        "tool_name": tool_name,
        "tool_input": {
            "file_path": file_path,
            "content": "test content",
        },
    }


class TestGeminiBeforeTool(unittest.TestCase):
    def test_write_file_is_blocked(self):
        """Gemini write_file 호출 → 차단(exit 2)."""
        code = _run(_gemini_payload("write_file"), _FAKE_GUARD_BLOCK)
        self.assertEqual(code, 2, f"write_file은 차단돼야 함, 실제: {code}")

    def test_replace_sensitive_path_is_blocked(self):
        """Gemini replace on sensitive path(scripts/) → 차단(exit 2)."""
        code = _run(_gemini_payload("replace", "scripts/omc_context.py"), _FAKE_GUARD_BLOCK)
        self.assertEqual(code, 2, f"scripts/ replace는 차단돼야 함, 실제: {code}")

    def test_replace_non_sensitive_path_is_allowed(self):
        """Gemini replace on non-sensitive path(src/) → 허용(exit 0)."""
        code = _run(_gemini_payload("replace", "src/components/Button.tsx"), _FAKE_GUARD_BLOCK)
        self.assertEqual(code, 0, f"src/ replace는 허용돼야 함, 실제: {code}")

    def test_read_file_is_allowed(self):
        """Gemini read_file 호출 → 허용(exit 0) (읽기 전용)."""
        payload = {
            "session_id": "test-session-123",
            "hook_event_name": "BeforeTool",
            "tool_name": "read_file",
            "tool_input": {"file_path": "src/foo.py"},
        }
        code = _run(payload, _FAKE_GUARD_BLOCK)
        self.assertEqual(code, 0, f"read_file은 허용돼야 함, 실제: {code}")

    def test_write_file_allow_when_guard_passes(self):
        """가드가 허용하면 write_file도 exit 0이다."""
        code = _run(_gemini_payload("write_file"), _FAKE_GUARD_ALLOW)
        self.assertEqual(code, 0)

    def test_write_file_uses_omcblockexit_when_set(self):
        """OMC_BLOCK_EXIT=1 설정 시 Gemini write_file도 exit 1로 차단."""
        code = _run(_gemini_payload("write_file"), _FAKE_GUARD_BLOCK, {"OMC_BLOCK_EXIT": "1"})
        self.assertEqual(code, 1, f"OMC_BLOCK_EXIT=1이면 exit 1이어야 함, 실제: {code}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
