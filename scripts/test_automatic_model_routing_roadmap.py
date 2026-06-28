from __future__ import annotations

from pathlib import Path


def test_roadmap_includes_status_board_and_operator_experience_track() -> None:
    text = Path("docs/automatic_model_routing_roadmap.md").read_text(encoding="utf-8")

    assert "## 로드맵 상태판" in text
    assert "## Operator Experience" in text
    assert "plan / task / review" in text
    assert "V2. Step-level Routing | 완료" in text
    assert "V3. Failure-driven Escalation | 진행중" in text
    assert "`retry_exhausted`와 `failed_critique_loop` 경로의 `escalation_policy` decision persistence 연결 및 critique 경로 runtime consumption이 완료됐다." in text
    assert "현재 상태는 `V3-1 완료 / V3-2 critique 경로 연결 완료`에 가깝다." in text
    assert "Operator Experience | 진행중" in text
    assert "실제 profile 선택에 반영된다" in text
    assert "완료" in text
    assert "진행중" in text
    assert "미착수" in text
    assert "이번 분기 핵심 목표는 아래 3개" in text
    assert "원래 분기 목표선은 V3-1 완료까지였지만, 현재 구현은 V3-2 일부까지 선행 진입한 상태다." in text
    assert "## 바로 다음 작업 계획" in text
    assert "failure path 일반화" in text
    assert "## Decision Engine Spec" in text
    assert "`failure_class / escalation_policy / retry_count / reason_codes` 조합으로 결정한다." in text
    assert "`execution_failure` + default policy + threshold 미만" in text
    assert "`quality_failure` + default policy" in text
    assert "`orchestration_failure`" in text
    assert "failure path 일반화 완료 후 telemetry report 최소 MVP를 붙인다." in text
    assert "telemetry report 최소 MVP" in text
    assert "autopilot 작업 단위 정리" in text
