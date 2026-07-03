from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "verification_checklist.md"


def test_verification_checklist_includes_expensive_flow_coverage_map() -> None:
    text = DOC.read_text(encoding="utf-8")
    required_markers = [
        "고비용 흐름 커버 맵",
        "과다 단계 진입",
        "과다 출력",
        "재진입 루프",
        "기존 커버",
        "갭",
        "다음 수정 파일",
        "omc_role_suggest.py",
        "test_omc_role_suggest.py",
        "test_omc_skill_benchmark.py",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing expensive flow coverage markers: {missing}"


def test_verification_checklist_includes_v4_observed_validation_checklist() -> None:
    text = DOC.read_text(encoding="utf-8")
    required_markers = [
        "V4 운영 observed 검증 체크리스트",
        "observed_request / observed_output 기준 multi-run 실행 샘플 20회 이상",
        "same-surface observed evidence",
        "distinct policy pair 2개 이상",
        "overview / collected summary / decision",
        "baseline_comparison_status",
        "next_kpi_blocker",
        "next_priority_recommendation",
        "ready / deferred 판정이 실제 observed dataset 누적에서도 안정적인지",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing V4 observed validation markers: {missing}"


def test_verification_checklist_keeps_scenario_20_block_before_v4_section() -> None:
    text = DOC.read_text(encoding="utf-8")
    scenario_tail = "\n".join(
        [
            "실제 추천: pipeline-status 또는 benchmark-report 확인",
            "PASS / FAIL: PASS",
            "메모: autopilot에 결과 확인 분기 추가",
        ]
    )
    scenario_tail_index = text.find(scenario_tail)
    v4_section_index = text.find("## V4 운영 observed 검증 체크리스트")
    assert scenario_tail_index != -1, "scenario 20 tail should remain intact"
    assert v4_section_index != -1, "V4 section should exist"
    assert scenario_tail_index < v4_section_index, "scenario 20 block should finish before V4 section"
