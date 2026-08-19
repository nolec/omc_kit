"""
omc-prompt-inject.sh 단위 테스트
- 3-state (active / ask / skip) 로직 검증
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPT = str(ROOT / "templates" / ".agent-hooks" / "omc-prompt-inject.sh")
OMC = str(ROOT / "scripts" / "omc.py")


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
        env = {**os.environ, "PROMPT": prompt, "OMC_CLI_SCRIPT": OMC}
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

    def test_missing_latest_session_id_is_ask(self):
        """latest_confirmed_session_id가 비어있으면 contract_confirmed여도 ask 상태여야 한다."""
        result = _run(
            prompt="진행하자",
            latest={
                "latest_confirmation": {"status": "confirmed"},
                "latest_confirmed_session_id": "",  # 비어있음
                "latest_skill": "omc-task",
            },
            pipeline={
                "contract_confirmed": True,
                "session_id": "sess-A",
            },
        )
        self.assertIn("[OMC] 모호한 진행 요청", result.stdout,
                         "latest_confirmed_session_id 없으면 active가 아니라 ask여야 한다")

    def test_pending_local_commit_selection_inherits_acknowledgement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "omc-test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "OMC Test"], cwd=root, check=True)
            (root / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
            subprocess.run(
                [sys.executable, OMC, "state", "init", "--target", str(root)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    OMC,
                    "state",
                    "sync-session",
                    "--target",
                    str(root),
                    "--mode",
                    "autopilot",
                    "--title",
                    "omc-ship",
                    "--request",
                    "commit selected group",
                    "--roles",
                    "directive",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    OMC,
                    "state",
                    "decision-open",
                    "--target",
                    str(root),
                    "--decision-id",
                    "commit-group",
                    "--action",
                    "local_commit",
                    "--options-json",
                    json.dumps(
                        [{"id": "1", "aliases": ["1", "1번"], "value": "selected-group"}],
                        ensure_ascii=False,
                    ),
                ],
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                ["sh", SCRIPT],
                cwd=root,
                env={**os.environ, "PROMPT": "1번", "OMC_CLI_SCRIPT": OMC},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertIn("[OMC] 승인된 현재 작업 계속 실행", result.stdout)
        self.assertNotIn("[OMC] 모호한 진행 요청", result.stdout)

    def test_short_ack_without_pending_decision_still_clarifies(self):
        result = _run(
            prompt="응",
            latest={
                "latest_confirmation": {"status": "confirmed"},
                "latest_confirmed_session_id": "sess-no-decision",
                "latest_skill": "omc-review",
            },
        )
        self.assertIn("[OMC] 모호한 진행 요청", result.stdout)


if __name__ == "__main__":
    unittest.main()
