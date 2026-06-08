"""
omc-prompt-inject.sh 단위 테스트
- 3-state (active / ask / skip) 로직 검증
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).parent.parent / "templates" / ".agent-hooks" / "omc-prompt-inject.sh")


def _run(prompt: str, latest: dict, pipeline: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(cwd) if cwd else Path(tmp)
        (root / ".omc" / "state").mkdir(parents=True, exist_ok=True)
        (root / ".omc" / "policy.json").write_text(
            json.dumps({"enforce_confirm": True}), encoding="utf-8"
        )
        (root / ".omc" / "state" / "latest.json").write_text(
            json.dumps(latest), encoding="utf-8"
        )
        if pipeline is not None:
            (root / ".omc" / "pipeline_session.json").write_text(
                json.dumps(pipeline), encoding="utf-8"
            )
        env = {**os.environ, "PROMPT": prompt}
        result = subprocess.run(
            ["sh", SCRIPT],
            capture_output=True, text=True,
            env=env, cwd=str(root),
        )
        return result


class TestPromptInjectThreeState(unittest.TestCase):

    def test_active_pipeline_no_ambiguous_inject(self):
        """파이프라인 진행 중(active)이면 '모호 메시지'여도 확인 질문 주입 안 함."""
        result = _run(
            prompt="응",
            latest={
                "latest_confirmation": {"status": "confirmed"},
                "latest_confirmed_session_id": "sess-X",
                "latest_skill": "omc-task",
            },
            pipeline={
                "contract_confirmed": True,
                "session_id": "sess-X",
            },
        )
        self.assertNotIn("[OMC] 모호한 진행 요청", result.stdout,
                         "active 상태에서는 확인 질문을 주입하면 안 된다")

    def test_ask_state_injects_clarification(self):
        """confirmed + pipeline 비활성(ask)이면 확인 질문을 주입한다."""
        result = _run(
            prompt="진행하자",
            latest={
                "latest_confirmation": {"status": "confirmed"},
                "latest_confirmed_session_id": "sess-Y",
                "latest_skill": "omc-critique",
            },
            pipeline=None,  # pipeline_session.json 없음
        )
        self.assertIn("[OMC] 모호한 진행 요청", result.stdout,
                      "ask 상태에서는 확인 질문을 주입해야 한다")

    def test_session_id_mismatch_is_ask(self):
        """session_id 불일치(이전 파이프라인 잔재)도 ask 상태로 처리한다."""
        result = _run(
            prompt="계속",
            latest={
                "latest_confirmation": {"status": "confirmed"},
                "latest_confirmed_session_id": "sess-NEW",
                "latest_skill": "omc-plan",
            },
            pipeline={
                "contract_confirmed": True,
                "session_id": "sess-OLD",
            },
        )
        self.assertIn("[OMC] 모호한 진행 요청", result.stdout,
                      "session_id 불일치는 ask 상태여야 한다")

    def test_pending_status_skips_inject(self):
        """pending 상태에서는 확인 질문 주입 안 함."""
        result = _run(
            prompt="응",
            latest={
                "latest_confirmation": {"status": "pending"},
                "latest_confirmed_session_id": "sess-Z",
            },
        )
        self.assertNotIn("[OMC] 모호한 진행 요청", result.stdout,
                         "pending 상태에서는 주입하면 안 된다")


if __name__ == "__main__":
    unittest.main()
