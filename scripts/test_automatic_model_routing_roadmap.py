from __future__ import annotations

from pathlib import Path


def test_roadmap_includes_status_board_and_operator_experience_track() -> None:
    text = Path("docs/automatic_model_routing_roadmap.md").read_text(encoding="utf-8")

    assert "## 로드맵 상태판" in text
    assert "## Operator Experience" in text
    assert "plan / task / review" in text
    assert "V2. Step-level Routing | 완료" in text
    assert "V3. Failure-driven Escalation | 진행중" in text
    assert "V4. Telemetry-driven Tuning | 진행중" in text
    assert "`retry_exhausted`와 `failed_critique_loop` 경로의 `escalation_policy` decision persistence 연결 및 critique 경로 runtime consumption이 완료됐다." in text
    assert "`task_retry` / `plan_retry` 성공 경로, `timeout` 경로, `failed` 계열 주요 경로의 decision payload shape 일반화와 `orchestration_failure` 1차 decision policy 연결이 반영됐다." in text
    assert "추가로 `.omc/runs/` 기준 `reroute_rate`, `retry_to_success_rate`, `cost_per_successful_task` multi-run KPI summary와 current-path 중복 제거까지 반영됐다." in text
    assert "현재 상태는 `V3-2 주요 failure path 일반화 완료 + V4 multi-run KPI summary 1차 완료`에 가깝다." in text
    assert "Operator Experience | 진행중" in text
    assert "다만 `omc-plan` 출력 contract에 `decision / risk / next_action` 의미를 plan 문맥으로 고정하는 1차 보강은 반영됐다." in text
    assert "실제 profile 선택에 반영된다" in text
    assert "완료" in text
    assert "진행중" in text
    assert "미착수" in text
    assert "이번 분기 핵심 목표는 아래 3개" in text
    assert "원래 분기 목표선은 V3-1 완료까지였지만, 현재 구현은 V3-2 일부까지 선행 진입한 상태다." in text
    assert "## 바로 다음 작업 계획" in text
    assert "operator experience 보강 2차" in text
    assert "## Decision Engine Spec" in text
    assert "`failure_class / escalation_policy / retry_count / reason_codes` 조합으로 결정한다." in text
    assert "`execution_failure` + default policy + threshold 미만" in text
    assert "`quality_failure` + default policy" in text
    assert "`orchestration_failure`" in text
    assert "`benchmark-report`에 `had_reroute`, `recovered_after_retry`, `total_cost_usd`, `total_tokens` 같은 single-run telemetry가 반영됐다." in text
    assert "이번에 `orchestration_failure` 1차 decision policy는 들어갔다." in text
    assert "decision engine 일반화 2차" in text
    assert "autopilot 작업 단위 정리" in text
    assert "failure path 일반화에서 최소 orchestration failure shape와 single-run telemetry가 안정된 뒤 multi-run KPI summary를 붙였다." in text
    assert "최근 반영된 1차 변화:" in text
    assert "`omc-plan` 출력 contract에 `decision / risk / next_action` 의미를 plan 문맥으로 고정" in text
    assert "남은 2차 변화:" in text
    assert "`omc-review`, `omc-task`에도 같은 수준의 의미 고정 적용" in text
