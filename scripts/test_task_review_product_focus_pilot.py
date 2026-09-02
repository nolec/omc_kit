from pathlib import Path


CONTRACT_PATH = Path("docs/task_review_product_focus_pilot.md")


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_contract_freezes_selection_window_and_common_inputs() -> None:
    text = _contract()

    assert "T0" in text
    assert "최대 7일" in text
    assert "chronological first eligible 3" in text
    assert "최소 2개 저장소" in text
    assert "교체하지 않는다" in text
    for field in (
        "request",
        "base commit",
        "DoD",
        "verification",
        "provider",
        "model",
        "reasoning",
        "timeout",
    ):
        assert field in text


def test_contract_defines_symmetric_isolated_arms() -> None:
    text = _contract()

    assert "$omc-task" in text
    assert "$omc-review" in text
    assert "native Codex review" in text
    assert "OMC skill/state injection 없이" in text
    assert "arm별 재시도 1회" in text
    assert "case 1·3은 OMC 먼저" in text
    assert "case 2는 Baseline 먼저" in text
    assert "상대 arm의 출력" in text
    assert "별도 격리 clone" in text


def test_contract_defines_metrics_and_terminal_outcomes() -> None:
    text = _contract()

    for requirement in (
        "completion",
        "end-to-end wall-clock",
        "workflow time",
        "user intervention",
        "rework",
        "fatal violation",
        "INCONCLUSIVE",
        "CONTINUE",
        "REDUCE",
        "STOP",
    ):
        assert requirement in text

    assert "materialization부터 terminal receipt" in text
    assert "실제 사용자 응답 turn" in text
    assert "첫 구현 응답 이후" in text
    assert "통계적 우월성" in text
    assert "자동 적용하지 않는다" in text
    assert "`APPROVE` 또는 `APPROVE WITH NOTES`" in text
    assert "case별 최대 3회" in text
    assert "3회를 초과하면 `REDUCE`" in text


def test_contract_keeps_execution_and_adoption_human_gated() -> None:
    text = _contract()

    assert "pilot-start receipt" in text
    assert "사용자 승인" in text
    assert "선택한 arm만" in text
    assert "별도 작업" in text
    assert "provider 실행은 이 문서 작성 범위에 포함하지 않는다" in text
