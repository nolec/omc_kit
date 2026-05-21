"""
P0-1 CONTRACT 강제 기능 테스트 — RED 단계

테스트 목적:
  edit_file(기존 파일 수정) 호출 시 contract_confirmed 플래그를 확인해
  계획 없는 구현을 차단하는 로직을 검증한다.

현재 상태(RED): 아래 테스트들은 모두 FAIL 해야 한다.
  - cmd_check_edit 함수가 아직 존재하지 않음
  - contract-done 서브커맨드가 아직 존재하지 않음
  - is_contract_confirmed 함수가 아직 존재하지 않음
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_pipeline_guard as guard


@pytest.fixture()
def tmp_root(tmp_path: Path) -> Path:
    """격리된 임시 프로젝트 루트 픽스처."""
    (tmp_path / ".omc").mkdir()
    (tmp_path / "src").mkdir()
    # 기존 파일 시뮬레이션
    (tmp_path / "src" / "Button.tsx").write_text("export const Button = () => null;")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. contract_confirmed=False → edit_file 차단
# ---------------------------------------------------------------------------

class TestContractCheckFalse:
    def test_edit_existing_file_blocked_when_contract_not_confirmed(self, tmp_root: Path):
        """CONTRACT 미확인 상태에서 기존 파일 수정 시도 → 차단(exit 1)."""
        state = guard._load_state(tmp_root)
        assert state.get("contract_confirmed", False) is False, "초기 상태는 contract_confirmed=False여야 함"

        result = guard.cmd_check_edit(tmp_root, "src/Button.tsx")
        assert result == 1, "CONTRACT 미확인 → edit 차단 반환값은 1이어야 함"

    def test_bypass_file_passes_even_without_contract(self, tmp_root: Path):
        """예외 파일(*.spec.tsx)은 CONTRACT 미확인이라도 통과."""
        (tmp_root / "src" / "Button.spec.tsx").write_text("it('todo', () => {})")
        result = guard.cmd_check_edit(tmp_root, "src/Button.spec.tsx")
        assert result == 0, "테스트 파일은 CONTRACT 체크 없이 통과해야 함"

    def test_non_impl_file_passes_without_contract(self, tmp_root: Path):
        """구현 파일이 아닌 파일(json, md 등)은 통과."""
        (tmp_root / "README.md").write_text("# README")
        result = guard.cmd_check_edit(tmp_root, "README.md")
        assert result == 0, "비구현 파일은 CONTRACT 체크 없이 통과해야 함"


# ---------------------------------------------------------------------------
# 2. contract_confirmed=True → edit_file 허용
# ---------------------------------------------------------------------------

class TestContractCheckTrue:
    def test_edit_allowed_after_contract_done(self, tmp_root: Path):
        """contract-done 등록 후 기존 파일 수정 → 허용(exit 0)."""
        guard.cmd_contract_done(tmp_root)

        result = guard.cmd_check_edit(tmp_root, "src/Button.tsx")
        assert result == 0, "CONTRACT 확인 후 edit는 허용돼야 함"

    def test_contract_confirmed_flag_persisted(self, tmp_root: Path):
        """contract-done 후 state에 contract_confirmed=True가 저장됨."""
        guard.cmd_contract_done(tmp_root)
        state = guard._load_state(tmp_root)
        assert state.get("contract_confirmed") is True, "contract_confirmed 플래그가 저장돼야 함"


# ---------------------------------------------------------------------------
# 3. contract-done 명령
# ---------------------------------------------------------------------------

class TestContractDone:
    def test_contract_done_returns_zero(self, tmp_root: Path):
        """contract-done 명령은 성공(0) 반환."""
        result = guard.cmd_contract_done(tmp_root)
        assert result == 0

    def test_contract_done_idempotent(self, tmp_root: Path):
        """contract-done 중복 호출해도 상태 일관됨."""
        guard.cmd_contract_done(tmp_root)
        guard.cmd_contract_done(tmp_root)
        state = guard._load_state(tmp_root)
        assert state.get("contract_confirmed") is True


# ---------------------------------------------------------------------------
# 4. CONTRACT TTL 만료 → False로 초기화
# ---------------------------------------------------------------------------

class TestContractTTL:
    def test_contract_confirmed_expires(self, tmp_root: Path, monkeypatch):
        """contract_confirmed_at이 TTL을 초과하면 False로 처리."""
        guard.cmd_contract_done(tmp_root)

        # TTL 초과 시뮬레이션: started_at을 오래된 시간으로 조작
        state = guard._load_state(tmp_root)
        state["started_at"] = time.time() - guard._SESSION_TTL_SECONDS - 1
        guard._save_state(tmp_root, state)

        # TTL 초과 후 로드하면 빈 상태(contract_confirmed=False)
        fresh_state = guard._load_state(tmp_root)
        assert fresh_state.get("contract_confirmed", False) is False, "TTL 초과 후 contract_confirmed는 False여야 함"

    def test_edit_blocked_after_contract_ttl_expires(self, tmp_root: Path):
        """TTL 만료 후 edit 시도 → 차단."""
        guard.cmd_contract_done(tmp_root)
        state = guard._load_state(tmp_root)
        state["started_at"] = time.time() - guard._SESSION_TTL_SECONDS - 1
        guard._save_state(tmp_root, state)

        result = guard.cmd_check_edit(tmp_root, "src/Button.tsx")
        assert result == 1, "TTL 만료 후 edit는 차단돼야 함"


# ---------------------------------------------------------------------------
# 5. 기존 cmd_check(create_file) 동작 회귀 없음
# ---------------------------------------------------------------------------

class TestExistingCheckNotBroken:
    def test_new_file_still_blocked_without_red(self, tmp_root: Path):
        """기존 신규 파일 차단 동작이 contract 추가 후에도 유지됨."""
        result = guard.cmd_check(tmp_root, "src/NewComponent.tsx")
        assert result == 1, "RED 없이 신규 파일 생성은 여전히 차단돼야 함"

    def test_new_file_allowed_after_red(self, tmp_root: Path):
        """RED 등록 후 신규 파일 생성은 여전히 허용됨."""
        guard.cmd_red_done(tmp_root, "src/NewComponent.spec.tsx")
        result = guard.cmd_check(tmp_root, "src/NewComponent.tsx")
        assert result == 0, "RED 등록 후 신규 파일 생성은 허용돼야 함"
