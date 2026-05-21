"""
Tier 0 check-edit 테스트
Tier 0 파일(*.styled.ts, *.constants.ts 등)은 CONTRACT 없이도 check-edit PASS
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import omc_pipeline_guard as guard


def _make_state(tmp_path: Path, contract_confirmed: bool = False) -> Path:
    omc_dir = tmp_path / ".omc"
    omc_dir.mkdir()
    state_file = omc_dir / "pipeline_session.json"
    import json
    state = guard._empty_state()
    state["contract_confirmed"] = contract_confirmed
    state_file.write_text(json.dumps(state), encoding="utf-8")
    return tmp_path


class TestTier0FilesPassWithoutContract:
    """Tier 0 파일은 CONTRACT 미확인 상태에서도 check-edit 통과해야 한다."""

    def test_styled_ts_passes_without_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/Button/Button.styled.ts")
        assert result == 0, "*.styled.ts는 Tier 0 — CONTRACT 없이 통과해야 함"

    def test_styled_tsx_passes_without_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/Modal/Modal.styled.tsx")
        assert result == 0, "*.styled.tsx는 Tier 0 — CONTRACT 없이 통과해야 함"

    def test_constants_ts_passes_without_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/order/constants/columns.ts")
        assert result == 0, "constants/ 파일은 Tier 0 — CONTRACT 없이 통과해야 함"

    def test_config_file_passes_without_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "jest.config.ts")
        assert result == 0, "*.config.ts는 Tier 0 — CONTRACT 없이 통과해야 함"

    def test_types_file_passes_without_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/order/types.ts")
        assert result == 0, "types.ts는 Tier 0 — CONTRACT 없이 통과해야 함"

    def test_index_file_passes_without_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/order/index.ts")
        assert result == 0, "index.ts는 Tier 0 — CONTRACT 없이 통과해야 함"

    def test_stories_file_passes_without_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/Button/Button.stories.tsx")
        assert result == 0, "*.stories.tsx는 Tier 0 — CONTRACT 없이 통과해야 함"

    def test_constants_ts_file_pattern_passes_without_contract(self, tmp_path):
        """VisitorPageChart.constants.ts 같이 파일명에 .constants.ts가 있는 경우도 Tier 0"""
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(
            root,
            "apps/crm/components/statistics/chart/VisitorPageChart/VisitorPageChart.constants.ts",
        )
        assert result == 0, "*.constants.ts 파일명 패턴은 Tier 0 — CONTRACT 없이 통과해야 함"

    def test_constants_tsx_file_pattern_passes_without_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/Modal/Modal.constants.tsx")
        assert result == 0, "*.constants.tsx 파일명 패턴은 Tier 0"


class TestTier1FilesBlockWithoutContract:
    """Tier 1 파일은 CONTRACT 미확인 상태에서 차단되어야 한다."""

    def test_new_component_blocks_without_contract(self, tmp_path, capsys):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/order/OrderTable.tsx")
        assert result == 1, "신규 컴포넌트 .tsx는 Tier 1 — CONTRACT 없으면 차단"

    def test_new_hook_blocks_without_contract(self, tmp_path, capsys):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "src/hooks/useOrderFilter.ts")
        assert result == 1, "훅 .ts는 Tier 1 — CONTRACT 없으면 차단"

    def test_python_impl_blocks_without_contract(self, tmp_path, capsys):
        root = _make_state(tmp_path, contract_confirmed=False)
        result = guard.cmd_check_edit(root, "scripts/new_feature.py")
        assert result == 1, ".py 구현 파일은 Tier 1 — CONTRACT 없으면 차단"


class TestTier1PassesWithContract:
    """Tier 1 파일도 CONTRACT 확인 후에는 통과해야 한다."""

    def test_component_passes_with_contract(self, tmp_path):
        root = _make_state(tmp_path, contract_confirmed=True)
        result = guard.cmd_check_edit(root, "src/order/OrderTable.tsx")
        assert result == 0, "CONTRACT 확인 완료 후 Tier 1도 통과"


class TestTier0MessageContainsTierInfo:
    """차단 메시지에 Tier 안내가 포함되어야 한다 (선택적 — 구현 후 확인)."""

    def test_block_message_mentions_tier(self, tmp_path, capsys):
        root = _make_state(tmp_path, contract_confirmed=False)
        guard.cmd_check_edit(root, "src/NewFeature.tsx")
        out = capsys.readouterr().out
        assert "Tier" in out or "tier" in out.lower() or "CONTRACT" in out, \
            "차단 메시지에 Tier 또는 CONTRACT 안내 포함 필요"
