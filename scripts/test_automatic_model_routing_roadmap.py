from __future__ import annotations

from pathlib import Path


def test_roadmap_includes_status_board_and_operator_experience_track() -> None:
    text = Path("docs/automatic_model_routing_roadmap.md").read_text(encoding="utf-8")

    assert "## 로드맵 상태판" in text
    assert "## Operator Experience" in text
    assert "plan / task / review" in text
    assert "V2. Step-level Routing | 완료" in text
    assert "V3. Failure-driven Escalation | 완료" in text
    assert "V4. Telemetry-driven Tuning | 완료" in text
    assert "V3 승격 규칙 연결도 완료되어" in text
    assert "선택 근거 기록 고도화" in text
    assert "V4 KPI 2차의 baseline/timebox 기준도 고정됐다" in text
    assert "`retry_exhausted`와 `failed_critique_loop` 경로의 `escalation_policy` decision persistence 연결 및 critique 경로 runtime consumption이 완료됐다." in text
    assert "`task_retry` / `plan_retry` 성공 경로, `timeout` 경로, `failed` 계열 주요 경로의 decision payload shape 일반화와 `orchestration_failure` decision policy 연결이 반영됐다." in text
    assert "추가로 `.omc/runs/` 기준 `reroute_rate`, `retry_to_success_rate`, `cost_per_successful_task` multi-run KPI summary와 current-path 중복 제거까지 반영됐다." in text
    assert "overview KPI에 `readiness_same_surface`가 노출되고" in text
    assert "`readiness_status_line`, `baseline_comparison_status`, `next_kpi_blocker`, `baseline_comparison_line`까지 실려 baseline 비교 가능 여부를 바로 읽을 수 있게 됐다." in text
    assert "autopilot overview에도 `readiness_status` / `next_kpi_blocker`를 노출해 샘플 부족 이유를 콘솔에서 바로 읽게 했고" in text
    assert "autopilot overview에도 collected summary surface(`baseline_line`, `policy_summary`, `reason_signal`)를 직접 노출해" in text
    assert "overview에도 `next_priority_recommendation`, `next_priority_reason`가 직접 surfaced되도록 연결해" in text
    assert "rejection count와 reason map이 summary에 남도록 보강했다." in text
    assert "현재 상태는 `V3 완료 + V4 multi-run KPI summary 1차 완료`에 가깝다." in text
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
    assert "현재 구현은 이미 그 선을 넘었고, 이제는 V4 데이터 축적과 Operator Experience 정교화가 다음 우선순위다." in text
    assert "## 바로 다음 작업 계획" in text
    assert "next-action 품질 보강 3차 - 완료" in text
    assert "`next-action 품질 보강 3차`는 완료됐고, 이제 다음 우선순위는 아래와 같다." in text
    assert "response-mode fixture는 29 cases까지 늘었고 observed_request / expected_next_action 케이스도 더 촘촘히 고정됐다." in text
    assert "## Decision Engine Spec" in text
    assert "`failure_class / escalation_policy / retry_count / reason_codes` 조합으로 결정한다." in text
    assert "`execution_failure` + default policy + threshold 미만" in text
    assert "`quality_failure` + default policy" in text
    assert "`orchestration_failure`" in text
    assert "`benchmark-report`에 `had_reroute`, `recovered_after_retry`, `total_cost_usd`, `total_tokens` 같은 single-run telemetry가 반영됐다." in text
    assert "neutral observed seed를 readiness 입력에서 제외하고, observed_output run도 별도 case로 수집하되 `mode_accuracy`/`task_start_delay` decision metric은 왜곡하지 않도록 exclusion 규칙을 넣었다." in text
    assert "observed_output producer도 partial metadata를 그대로 저장하지 않도록 schema/backfill 규칙을 고정해, 실제 샘플이 benchmark 입력으로 들어가기 전 shape을 먼저 안정화했다." in text
    assert "decision engine 잔여 예외 감사는 완료됐고, 추가 코드 gap은 발견되지 않았다." in text
    assert "V3 완료 + V4 multi-run KPI summary 1차 완료" in text
    assert "decision engine 일반화 2차" not in text
    assert "next-action 품질 보강 3차 - 완료" in text
    assert "telemetry report 정리 2차 - 완료" in text
    assert "## 다음 순환 목표" in text
    assert "현재 기준 요약은 아래와 같다." in text
    assert "즉, 구현 로드맵 기준으로는 `V4-A 구현 마감`뿐 아니라 `V4-B 운영 완료 판정 1차`까지 닫혔고" in text
    assert "V4 multi-run KPI summary 2차" in text
    assert "V4-A 구현 마감" in text
    assert "V4-B 운영 완료 판정" in text
    assert "V4-B 운영 검증 실행 준비" in text
    assert "Operator Experience 4차" in text
    assert "Learned orchestrator 진입 조건 정리" in text
    assert "readiness/baseline 상태 문구는 이미 report와 overview에 실리므로" in text
    assert "invalid observed_output이 조용히 사라지지 않도록 rejection summary는 들어갔고, 1차로 collected observed summary에서도 rejection reason 병목을 함께 읽게 만들었다." in text
    assert "observed_request / observed_output 기준 multi-run 실행 샘플 20회 이상" in text
    assert "neutral observed seed는 수집량으로만 보이고 readiness 입력에서는 제외된다" in text
    assert "observed_output은 `comparison_scope`, response sample을 보존하되 `mode_accuracy` / `task_start_delay` decision metric을 공짜로 밀어 올리지 않는다" in text
    assert "observed_output producer는 partial metadata를 허용하지 않고, task metadata backfill 후에도 필수 schema가 비면 benchmark payload를 남기지 않는다" in text
    assert "distinct policy pair 2개 이상" in text
    assert "`reroute rate`, `retry-to-success rate`, `cost per successful task` 3개 KPI가 모두 표에 노출" in text
    assert "### 최소 KPI 기준" in text
    assert "baseline은 직전 정책 또는 고정 기준값 대비로 정의된다" in text
    assert "timebox로만 허용한다" in text
    assert "failure path 일반화에서 최소 orchestration failure shape와 single-run telemetry가 안정된 뒤 multi-run KPI summary를 붙였다." in text
    assert "same-surface observed evidence를 더 누적한다." in text
    assert "collected observed summary에도 `observed_data_bottleneck_summary`를 넣어 샘플 부족과 rejected observed_output reason을 함께 읽게 만들었다." in text
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
    assert "telemetry report 정리 2차 - 완료" in text
    assert "정책 비교 리포트는 1차 자동화가 들어갔고, benchmark/report 출력도 비교 가능한 형태로 정리됐다." in text
    assert "next-action 품질 보강 3차 - 완료" in text
    assert "### 운영 유지 체크포인트" in text
    assert "dry-run completion은 운영 완료 샘플에 포함하지 않는다" in text
    assert "`operational_validation_readiness=start-ready`가 overview / collected summary / decision surface에서 같이 보여야 한다" in text
    assert "`next_priority_recommendation`과 `next_priority_reason`은 ready 이후에도 operator follow-up 문맥을 잃지 않아야 한다" in text
    assert "`wrong_next_step`이 주 병목이 아니면 `next_priority_recommendation`은 `compress_operator_outputs`로 바로 바뀌지 않는다" in text
    assert "## V5 후보 트랙 구체화" in text
    assert "### 1. Next-step Decision Engine 일반화" in text
    assert "### 2. Cost-Quality Policy Layer" in text
    assert "policy decision input SSOT:" in text
    assert "- `failure_cost`" in text
    assert "- `ambiguity`" in text
    assert "- `operator_goal`" in text
    assert "3. skill adapter 이관 - 사실상 완료 (operator priority / output_bloat validation / operator explanation / overview next_priority parity 고정)" in text
    assert "4. fixture 확대 - 대표 반례 마감 단계 (새 observed failure가 다시 잡힐 때만 추가 확장)" in text
    assert "입력 축은 3개로 시작한다." in text
    assert "`confidence=low`이면 `balanced + user_selection_needed=yes`로 고정한다." in text
    assert "- benchmark/report surface에도 `recommended_policy_profile / policy_reason_summary / policy_confidence / user_selection_needed`가 직접 노출되고, 관련 회귀 테스트로 summary 계약이 고정됐다." in text
    assert "Layer boundary:" in text
    assert "- Cost-Quality Policy Layer: 정책 프로필 추천과 설명만 담당" in text
    assert "- Executor Recommendation Surface: 실행기/모델 매핑만 담당" in text
    assert "- Reroute Layer: 실패 후 fallback / retry / delay만 담당" in text
    assert "### 3. Executor Recommendation Surface" in text
    assert "## Learned Orchestrator 진입 게이트" in text
    assert "### 진입 조건" in text
    assert "### 보류 조건" in text
    assert "### 시작 전 금지선" in text
    assert "`추천 엔진 3축이 먼저, learned layer는 맨 마지막`" in text
    assert "`Decision Engine 일반화`, `Cost-Quality Policy Layer`, `Executor Recommendation Surface`의 추천-only surface가 먼저 정리된다" in text
    assert "사람 승인 없는 자동 executor 전환 없이도 policy/executor 추천 품질을 설명 가능하게 유지한다" in text
    assert "learned orchestrator를 runtime closed-loop auto-switch로 바로 연결하지 않는다" in text
    assert "## Fugu식 기능 MVP 설계" in text
    assert "### MVP 1. Decision Engine Core" in text
    assert "### MVP 2. Policy Profile 3종" in text
    assert "### MVP 3. Executor Recommendation Surface" in text
    assert "## 토큰 대비 효과 점수표" in text
    assert "| Decision Engine 일반화 | 5 | 3 | 4 | 2 | 가장 먼저 |" in text
    assert "1. `Decision Engine 일반화`" in text
    assert "2. `Cost-Quality Policy Layer`" in text
    assert "3. `Executor Recommendation Surface`" in text
    assert "`escalation_policy`를 V3 승격 엔진과 연결" not in text


def test_roadmap_includes_evidence_based_validation_matrix_for_fugu_alignment() -> None:
    text = Path("docs/automatic_model_routing_roadmap.md").read_text(encoding="utf-8")

    required_markers = [
        "## 로드맵 검증 매트릭스",
        "로드맵 완료 항목",
        "실제 반영 증거",
        "Fugu 비교에 쓰는 축",
        "판정 규칙",
        "`문서만 반영`",
        "`반영 확인`",
        "`체감 개선 확인`",
        "Fugu 비교 문구는 `현재 상태 참조`와 `반영 검증 완료`를 구분한다.",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert not missing, f"missing roadmap validation matrix markers: {missing}"
