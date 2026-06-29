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
