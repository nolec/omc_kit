"""
B항목 + C항목 테스트

B: 새 세션 시작(session-start 커맨드) 시 contract_confirmed 자동 초기화
C: RED 등록 없이 구현 파일에 코드 50줄+ 이면 "GREEN 먼저?" 경고 출력
"""
from __future__ import annotations

import io
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_pipeline_guard as guard


@pytest.fixture()
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / ".omc").mkdir()
    (tmp_path / "src").mkdir()
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# B: 세션 시작 시 contract_confirmed 초기화
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionResetClearsContract:
    def test_reset_clears_contract_confirmed(self, tmp_root: Path):
        """cmd_reset 실행 시 contract_confirmed가 False로 초기화돼야 한다."""
        guard.cmd_contract_done(tmp_root)
        assert guard._load_state(tmp_root).get("contract_confirmed") is True

        guard.cmd_reset(tmp_root)

        assert guard._load_state(tmp_root).get("contract_confirmed") is False

    def test_session_start_resets_contract(self, tmp_root: Path):
        """session_start 후 contract_confirmed가 False여야 한다."""
        guard.cmd_contract_done(tmp_root)

        result = guard.cmd_session_start(tmp_root)
        assert result == 0

        state = guard._load_state(tmp_root)
        assert state.get("contract_confirmed") is False

    def test_session_start_preserves_red_done_files(self, tmp_root: Path):
        """session_start 후에도 red_done_files는 유지돼야 한다."""
        test_file = tmp_root / "src" / "Foo.spec.ts"
        test_file.write_text("test('foo', () => {})")
        guard.cmd_red_done(tmp_root, str(test_file))

        guard.cmd_session_start(tmp_root)

        state = guard._load_state(tmp_root)
        norm = guard._normalize_path(tmp_root, str(test_file))
        norm_list = [guard._normalize_path(tmp_root, f) for f in state.get("red_done_tests", [])]
        assert norm in norm_list

    def test_session_start_idempotent(self, tmp_root: Path):
        """session_start를 여러 번 실행해도 동일하게 contract=False 유지."""
        guard.cmd_contract_done(tmp_root)
        guard.cmd_session_start(tmp_root)
        guard.cmd_session_start(tmp_root)
        assert guard._load_state(tmp_root).get("contract_confirmed") is False


# ─────────────────────────────────────────────────────────────────────────────
# C: 줄 수 기반 "GREEN 먼저?" 경고
# ─────────────────────────────────────────────────────────────────────────────

class TestLineLengthWarning:
    def _make_impl_file(self, tmp_root: Path, lines: int) -> Path:
        impl = tmp_root / "src" / "BigComponent.tsx"
        impl.write_text("\n".join([f"const line{i} = {i};" for i in range(lines)]))
        return impl

    def _run_check(self, tmp_root: Path, impl: Path) -> str:
        # 절대 경로에 'test_' 패턴이 포함될 수 있으므로 상대 경로 사용
        rel_path = impl.relative_to(tmp_root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            guard.cmd_check(tmp_root, str(rel_path))
        return buf.getvalue()

    def test_no_warning_for_small_file(self, tmp_root: Path):
        """50줄 미만 파일은 경고 없이 통과."""
        impl = self._make_impl_file(tmp_root, 30)
        output = self._run_check(tmp_root, impl)
        assert "GREEN 먼저" not in output

    def test_warning_for_large_file_without_red(self, tmp_root: Path):
        """50줄 이상 파일이 RED 없이 check되면 경고를 출력해야 한다."""
        impl = self._make_impl_file(tmp_root, 60)
        output = self._run_check(tmp_root, impl)
        assert "GREEN 먼저" in output, (
            f"60줄 구현 파일에 RED 없으면 'GREEN 먼저' 경고 출력 필요. 실제: {repr(output)}"
        )

    def test_no_warning_for_large_file_with_red(self, tmp_root: Path):
        """RED가 등록된 파일은 줄 수 경고 없이 통과."""
        impl = self._make_impl_file(tmp_root, 60)
        test_file = tmp_root / "src" / "BigComponent.spec.tsx"
        test_file.write_text("test('big', () => {})")
        guard.cmd_red_done(tmp_root, "src/BigComponent.spec.tsx")
        guard.cmd_allow(tmp_root, "src/BigComponent.tsx", reason="RED 완료 후 구현")
        output = self._run_check(tmp_root, impl)
        assert "GREEN 먼저" not in output

    def test_warning_exits_zero(self, tmp_root: Path):
        """줄 수 경고는 exit 0 (차단 아님)."""
        impl = self._make_impl_file(tmp_root, 60)
        rel_path = impl.relative_to(tmp_root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = guard.cmd_check(tmp_root, str(rel_path))
        assert result == 0
