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
    assert "`decision_policy_entry` helper 추출로 failure-class별 decision 규칙을 공통 엔진으로 옮길 준비를 마쳤다." in text
    assert "critique/review failure step runtime 소비가 `_failure_step_decision` helper를 통해 공통 decision 엔진을 직접 사용하도록 정리됐다." in text
    assert "`task_retry`, `plan_retry` 실패 payload도 `_retry_step_payload` helper로 정리돼 retry runtime decision 하드코딩이 제거됐다." in text
    assert "`ambiguous_response`, `branch_setup_failed`도 `orchestration_failure`로 승격돼 persisted decision이 explicit hold로 수렴하도록 정리됐다." in text
    assert "Operator Experience | 진행중" in text
    assert "`2-2 reroute / delay UX`와 `role_suggest` 시작 스킬 오판 패턴 보강까지 반영됐다." in text
    assert "실제 profile 선택에 반영된다" in text
    assert "완료" in text
    assert "진행중" in text
    assert "미착수" in text
    assert "이번 분기 핵심 목표는 아래 3개" in text
    assert "원래 분기 목표선은 V3-1 완료까지였지만, 현재 구현은 V3-2 일부까지 선행 진입한 상태다." in text
    assert "## 바로 다음 작업 계획" in text
    assert "next-action 품질 보강 3차" in text
    assert "## Decision Engine Spec" in text
    assert "`failure_class / escalation_policy / retry_count / reason_codes` 조합으로 결정한다." in text
    assert "`execution_failure` + default policy + threshold 미만" in text
    assert "`quality_failure` + default policy" in text
    assert "`orchestration_failure`" in text
    assert "`benchmark-report`에 `had_reroute`, `recovered_after_retry`, `total_cost_usd`, `total_tokens` 같은 single-run telemetry가 반영됐다." in text
    assert "decision engine 잔여 예외 감사는 완료됐고, 추가 코드 gap은 발견되지 않았다." in text
    assert "decision engine 일반화 2차" not in text
    assert "next-action 품질 보강 3차" in text
    assert "autopilot 작업 단위 정리" in text
    assert "failure path 일반화에서 최소 orchestration failure shape와 single-run telemetry가 안정된 뒤 multi-run KPI summary를 붙였다." in text
    assert "최근 반영된 1차 변화:" in text
    assert "`omc-plan` 출력 contract에 `decision / risk / next_action` 의미를 plan 문맥으로 고정" in text
    assert "`omc-review` 출력 contract에 `decision / risk / next_action` 의미를 review 문맥으로 고정" in text
    assert "`omc-task` 출력 contract에 `decision / risk / next_action` 의미를 task 문맥으로 고정" in text
    assert "최근 반영된 2차 변화:" in text
    assert "`2-1 next_action 공통화`" in text
    assert "`2-2 reroute / delay UX`" in text
    assert "`omc-critique`, `omc-status` 출력 contract에 조건부 `reroute 이유 / delay 이유 / 재개 조건` 설명 규칙 반영" in text
    assert "`role_suggest`가 `변경 상태 검토`와 `plan 검증` 요청을 각각 `review`, `critique`로 더 정확히 라우팅" in text
    assert "`omc-status`가 `현재 병목 > 기본 파이프라인` 우선순위를 직접 따르도록 추천 규칙을 보강" in text
    assert "`omc-plan`, `omc-review`도 같은 우선순위를 다음 추천 규칙에 직접 반영해 plan/review/status 3축을 맞췄다." in text
    assert "공통 결정표" in text
    assert "ship_intent_explicit" in text
    assert "남은 2차 변화:" in text
    assert "`omc-plan`, `omc-review`, `omc-task` 1차 계약 보강은 끝났다." in text
    assert "`2-1 next_action 공통화`도 끝났다." in text
    assert "`2-2 reroute / delay UX`도 1차 완료됐다." in text
    assert "next-action 품질 보강 3차의 plan/review/status 병목 우선 추천은 반영 완료다." in text
    assert "다음 남은 조각은 fixture/benchmark에도 이 우선순위를 더 직접 반영해 실제 기대 추천과의 오차를 줄이는 것이다." in text
    assert "next-action 품질 보강 3차" in text
