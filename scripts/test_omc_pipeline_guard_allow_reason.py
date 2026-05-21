"""
P1-1 allow --reason 필수화 테스트 — RED 단계

테스트 목적:
  omc_pipeline_guard.py allow 명령에서 --reason이 없으면
  경고가 아닌 exit 1(차단)로 처리하는 로직을 검증한다.

현재 상태(RED):
  - reason 없어도 경고만 출력하고 exit 0 반환
  - 아래 테스트들은 모두 FAIL 해야 한다
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_pipeline_guard as guard


@pytest.fixture()
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / ".omc").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Button.tsx").write_text("export const Button = () => null;")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. --reason 없으면 exit 1 (차단)
# ---------------------------------------------------------------------------

class TestAllowReasonRequired:
    def test_allow_without_reason_returns_one(self, tmp_root: Path):
        """--reason 없으면 exit 1 반환."""
        result = guard.cmd_allow(tmp_root, "src/Button.tsx", reason="")
        assert result == 1, "--reason 없으면 exit 1이어야 함 (현재 exit 0 반환 — RED)"

    def test_allow_without_reason_does_not_add_to_allowed_list(self, tmp_root: Path):
        """--reason 없으면 allowed_impl_files에 추가되지 않음."""
        guard.cmd_allow(tmp_root, "src/Button.tsx", reason="")
        state = guard._load_state(tmp_root)
        norm_file = guard._normalize_path(tmp_root, "src/Button.tsx")
        norm_allowed = [guard._normalize_path(tmp_root, f) for f in state["allowed_impl_files"]]
        assert norm_file not in norm_allowed, \
            "--reason 없으면 allowed 목록에 추가되지 않아야 함"

    def test_allow_without_reason_does_not_write_audit_log(self, tmp_root: Path):
        """--reason 없으면 감사 로그에도 기록하지 않음."""
        guard.cmd_allow(tmp_root, "src/Button.tsx", reason="")
        log_path = guard._allow_log_path(tmp_root)
        if log_path.exists():
            entries = [l for l in log_path.read_text().splitlines() if l.strip()]
            assert len(entries) == 0, "--reason 없으면 감사 로그에 기록되지 않아야 함"


# ---------------------------------------------------------------------------
# 2. --reason 있으면 기존과 동일하게 동작
# ---------------------------------------------------------------------------

class TestAllowWithReasonWorks:
    def test_allow_with_reason_returns_zero(self, tmp_root: Path):
        """--reason 있으면 exit 0 반환."""
        result = guard.cmd_allow(tmp_root, "src/Button.tsx", reason="리팩터링 — 테스트 없는 기존 파일")
        assert result == 0, "--reason 있으면 exit 0이어야 함"

    def test_allow_with_reason_adds_to_allowed_list(self, tmp_root: Path):
        """--reason 있으면 allowed_impl_files에 추가됨."""
        guard.cmd_allow(tmp_root, "src/Button.tsx", reason="리팩터링")
        state = guard._load_state(tmp_root)
        norm_file = guard._normalize_path(tmp_root, "src/Button.tsx")
        norm_allowed = [guard._normalize_path(tmp_root, f) for f in state["allowed_impl_files"]]
        assert norm_file in norm_allowed, "--reason 있으면 allowed 목록에 추가돼야 함"

    def test_allow_with_reason_writes_audit_log(self, tmp_root: Path):
        """--reason 있으면 감사 로그에 기록됨."""
        reason = "리팩터링 — 테스트 없는 기존 파일"
        guard.cmd_allow(tmp_root, "src/Button.tsx", reason=reason)
        log_path = guard._allow_log_path(tmp_root)
        assert log_path.exists(), "감사 로그 파일이 생성돼야 함"
        import json
        entries = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
        assert len(entries) == 1, "감사 로그에 1건 기록돼야 함"
        assert entries[0]["reason"] == reason, "로그에 reason이 정확히 기록돼야 함"

    def test_allow_with_reason_check_passes(self, tmp_root: Path):
        """--reason으로 allow 등록 후 cmd_check에서 통과."""
        guard.cmd_allow(tmp_root, "src/Button.tsx", reason="리팩터링")
        result = guard.cmd_check(tmp_root, "src/Button.tsx")
        assert result == 0, "allow 등록 후 check는 통과해야 함"


# ---------------------------------------------------------------------------
# 3. whitespace-only reason은 빈 reason과 동일하게 처리
# ---------------------------------------------------------------------------

class TestAllowWhitespaceReason:
    def test_whitespace_reason_treated_as_empty(self, tmp_root: Path):
        """공백만 있는 reason은 빈 reason으로 처리 → exit 1."""
        result = guard.cmd_allow(tmp_root, "src/Button.tsx", reason="   ")
        assert result == 1, "공백만 있는 reason은 exit 1이어야 함"
