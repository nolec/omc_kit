#!/usr/bin/env python3
"""omc-pipeline-check.sh Codex PreToolUse (apply_patch / Write) 테스트 — stdlib only"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).parent.parent / ".agent-hooks" / "omc-pipeline-check.sh")

_FAKE_GUARD_BLOCK = """\
#!/usr/bin/env python3
import sys
print("[OMC BLOCK] 테스트용 강제 차단")
sys.exit(1)
"""


def _run(payload: dict) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        scripts_dir = Path(tmp) / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "omc_pipeline_guard.py").write_text(_FAKE_GUARD_BLOCK)
        result = subprocess.run(
            ["sh", SCRIPT],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=os.environ,
            cwd=tmp,
        )
        return result.returncode


class TestCodexApplyPatch(unittest.TestCase):
    def test_apply_patch_sensitive_path_blocked(self):
        """Codex apply_patch + scripts/ 경로 → exit 2."""
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"file_path": "scripts/omc_context.py", "patch": "..."},
        }
        code = _run(payload)
        self.assertEqual(code, 2, f"apply_patch 차단 기대 exit 2, 실제: {code}")

    def test_apply_patch_write_alias_blocked(self):
        """Codex matcher alias Write → exit 2."""
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "scripts/omc_utils.py"},
        }
        code = _run(payload)
        self.assertEqual(code, 2, f"Write 차단 기대 exit 2, 실제: {code}")

    def test_apply_patch_non_sensitive_allowed_by_guard(self):
        """src/ 경로 + 가드 허용 시 exit 0 (Write 분기 진입 후 가드 통과)."""
        allow_guard = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "omc_pipeline_guard.py").write_text(allow_guard)
            payload = {
                "tool_name": "apply_patch",
                "tool_input": {"file_path": "src/App.tsx"},
            }
            result = subprocess.run(
                ["sh", SCRIPT],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                env=os.environ,
                cwd=tmp,
            )
            self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
