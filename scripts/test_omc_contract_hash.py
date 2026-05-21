"""
N3 — CONTRACT 해시 RED 테스트
아직 해시 기능이 없으므로 관련 테스트는 FAIL 예상
"""
import json
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
GUARD_SCRIPT = ROOT / "scripts" / "omc_pipeline_guard.py"


def _run(*args, env_extra=None):
    import os
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT), *args],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )


def _state_path(root=ROOT):
    return root / ".omc" / "pipeline_session.json"


def _load_state(root=ROOT):
    p = _state_path(root)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


class TestContractHashStored:
    def test_contract_done_stores_hash_field(self):
        """contract-done 실행 후 pipeline_session.json에 contract_hash 필드가 저장되어야 한다"""
        _run("contract-done")
        state = _load_state()
        assert "contract_hash" in state, (
            f"contract_hash 필드 없음. 현재 state keys: {list(state.keys())}"
        )

    def test_contract_hash_is_nonempty_string(self):
        """contract_hash는 비어있지 않은 문자열이어야 한다"""
        _run("contract-done")
        state = _load_state()
        h = state.get("contract_hash", "")
        assert isinstance(h, str) and len(h) > 0, (
            f"contract_hash가 비어있음: {repr(h)}"
        )

    def test_contract_hash_looks_like_sha256(self):
        """contract_hash는 64자 hex 문자열(SHA-256)이어야 한다"""
        _run("contract-done")
        state = _load_state()
        h = state.get("contract_hash", "")
        assert len(h) == 64 and all(c in "0123456789abcdef" for c in h), (
            f"SHA-256 형식이 아님: {repr(h)}"
        )


class TestContractHashWithContent:
    def test_contract_done_with_content_arg(self):
        """contract-done --content '...' 로 내용 해시를 저장할 수 있어야 한다"""
        content = "목표: 테스트 | 범위: scripts/ | DoD: PASS"
        result = _run("contract-done", "--content", content)
        assert result.returncode == 0, f"오류: {result.stderr}"
        state = _load_state()
        assert "contract_hash" in state

    def test_same_content_produces_same_hash(self):
        """동일한 content는 항상 같은 해시를 생성해야 한다"""
        content = "목표: 동일 내용 테스트"
        _run("contract-done", "--content", content)
        state1 = _load_state()
        h1 = state1.get("contract_hash", "")

        _run("contract-done", "--content", content)
        state2 = _load_state()
        h2 = state2.get("contract_hash", "")

        assert h1 == h2, f"같은 content인데 해시가 다름: {h1} vs {h2}"

    def test_different_content_produces_different_hash(self):
        """다른 content는 다른 해시를 생성해야 한다"""
        _run("contract-done", "--content", "내용A")
        h1 = _load_state().get("contract_hash", "")

        _run("contract-done", "--content", "내용B")
        h2 = _load_state().get("contract_hash", "")

        assert h1 != h2, "다른 content인데 해시가 같음"


class TestContractHashReset:
    def test_session_start_clears_hash(self):
        """session-start 실행 시 contract_hash도 초기화되어야 한다"""
        _run("contract-done", "--content", "세션 리셋 테스트")
        state_before = _load_state()
        assert "contract_hash" in state_before

        _run("session-start")
        state_after = _load_state()
        h = state_after.get("contract_hash", "")
        assert h == "" or h is None or "contract_hash" not in state_after, (
            f"session-start 후에도 contract_hash가 남아있음: {repr(h)}"
        )
