# Automatic Model Routing Roadmap

## 목표

OMC를 `스킬 기반 규칙 라우팅`에서 `완전 자동 모델 전환 제품`으로 키운다.
핵심은 사용자가 모델을 직접 고르지 않아도, 요청 난이도와 실패 신호에 따라 적절한 모델 강도가 자동으로 선택되는 것이다.

## 현재 위치

현재 OMC는 다음 수준에 가깝다.

- 요청 또는 스킬 종류를 보고 `task kind`를 정한다.
- `balanced / cost_saver / quality_first` 정책을 읽는다.
- `mini_default / mini_high / full_default` 중 하나를 규칙 기반으로 고른다.

즉, 지금은 `rule-based orchestration v1`이다.

## 제품 목표 상태

최종 목표는 아래 다섯 가지를 만족하는 것이다.

1. 사용자가 모델을 직접 고르지 않아도 된다.
2. 한 요청 안에서도 step별로 모델 강도가 다를 수 있다.
3. 실패가 반복되면 더 강한 모델로 자동 승격된다.
4. 성공/실패/비용 로그가 쌓여 정책이 계속 개선된다.
5. 필요하면 사용자가 override할 수 있고, 선택 이유를 설명할 수 있다.

## 로드맵 상태판

현재 구현 기준으로 보면 상태는 아래에 가깝다.

| 트랙 | 상태 | 근거 | 다음 빈칸 |
|---|---|---|---|
| V1. Skill-based Routing | 완료 | `resolve_task_routing()`, `OMC_ROUTING_POLICY`, `role_suggest`의 `task_kind_hint`, `autopilot`의 `task_kind` 전달이 이미 연결되어 있다. | 운영값 미세조정 |
| V2. Step-level Routing | 완료 | `autopilot` step metadata schema와 normalization이 있고, `complexity/risk/preferred_profile/sensitive_paths`가 실제 profile 선택에 반영된다. V3 승격 규칙 연결도 완료되어, 최근에는 `routing_reason_codes`, `routing_reason_summary`가 step state와 overview surface까지 연결돼 step별 선택 근거를 바로 읽을 수 있게 됐다. 이는 기존 `선택 근거 기록 고도화` 빈칸을 구현 측면에서 닫은 상태다. | 운영 surface 미세조정 |
| V3. Failure-driven Escalation | 완료 | V3-1 수준의 failure class 분리와 persistence/report 반영은 들어갔고, `retry_exhausted`와 `failed_critique_loop` 경로의 `escalation_policy` decision persistence 연결 및 critique 경로 runtime consumption이 완료됐다. 추가로 `task_retry` / `plan_retry` 성공 경로, `timeout` 경로, `failed` 계열 주요 경로의 decision payload shape 일반화와 `orchestration_failure` decision policy 연결이 반영됐다. 복구 경로도 `_recovery_target_from_decision` 헬퍼로 공통화해 retry/plan 재진입 우선순위를 한 곳에서 보게 만들었고, task 단계 orchestration_failure(bad_entry_skill/metadata_missing)와 review 단계 quality failure도 같은 recovery 엔진을 타고 plan_retry로 수렴한다. `VERDICT: BLOCK`만 있고 reason code가 비어도 `block_without_reason_code`로 승격해 silent completion을 막는다. resume 이후 `task_stage_plan_retry_count`도 더 이상 중복 소비되지 않도록 정리됐다. 이제 `timeout` 경로도 공통 decision 엔진(`_decision_policy_entry`)으로 직접 소비한다. 남은 축은 telemetry 기반 정책 비교와 multi-run tuning이다. | telemetry report MVP + multi-run KPI 고도화 |
| V4. Telemetry-driven Tuning | 완료 | step state에 `token_usage`, `cost_estimate` 저장은 들어갔고, `benchmark-report`에 `had_reroute`, `recovered_after_retry`, `total_cost_usd`, `total_tokens` 같은 single-run telemetry가 반영됐다. 추가로 `.omc/runs/` 기준 `reroute_rate`, `retry_to_success_rate`, `cost_per_successful_task` multi-run KPI summary와 current-path 중복 제거까지 반영됐다. 정책 비교 리포트 자동화 1차와 telemetry report 정리 2차까지 들어갔고, V4 KPI 2차의 baseline/timebox 기준도 고정됐다. 최근에는 neutral observed seed를 readiness 입력에서 제외하고, observed_output run도 별도 case로 수집하되 `mode_accuracy`/`task_start_delay` decision metric은 왜곡하지 않도록 exclusion 규칙을 넣었다. 추가로 observed_output producer도 partial metadata를 그대로 저장하지 않도록 schema/backfill 규칙을 고정해, 실제 샘플이 benchmark 입력으로 들어가기 전 shape을 먼저 안정화했다. 이어서 readiness coverage 1차로 overview KPI에 `readiness_same_surface`가 노출되고, 2차로 benchmark report payload에 `readiness_status_line`, `baseline_comparison_status`, `next_kpi_blocker`, `baseline_comparison_line`까지 실려 baseline 비교 가능 여부를 바로 읽을 수 있게 됐다. 최근에는 autopilot overview에도 `readiness_status` / `next_kpi_blocker`를 노출해 샘플 부족 이유를 콘솔에서 바로 읽게 했고, observed sample contract 1차로 partial metadata observed_output이 버려질 때도 rejection count와 reason map이 summary에 남도록 보강했다. 이어서 policy comparison summary 1차를 넣어 deferred/ready 상태를 decision payload 한 줄로 바로 읽게 만들었다. 추가로 response-mode threshold 정책 검증 1차로 collection summary와 comparison summary의 ready/pending 의미를 브리지 테스트로 고정했고, threshold taxonomy(`ready / pending / ambiguous`)와 candidate 비교도 count-aware benchmark check로 보강했다. 여기에 더해 `state status`가 `.omc/state/runs`와 `.omc/runs`를 함께 집계하도록 복구해 실제 observed run 축적량이 status 화면에서 축소 표기되지 않게 했고, `pipeline_history_runs(.omc/runs)` 라벨까지 노출해 어떤 저장소에서 온 관측치인지 바로 읽게 만들었다. 또 `observed-collect` clean-scope 전제조건이 로컬 `.omc/tasks` 수정에만 머무르지 않도록 `templates/shared_tasks` 설치 경로를 추가해 `setup --force` 대상 저장소까지 동일 정책이 전파되게 맞췄다. 최근 진행분으로 collected observed summary와 comparison summary 양쪽에 `reroute_rate`, `retry_to_success_rate`, `cost_per_successful_task` 3개 KPI가 같은 형식으로 실리고, 이를 run-based fixture 회귀 테스트로 고정했다. 추가로 baseline flag drift, pending/ready 경로의 rejection count-only suffix, ready 경로 count-only suffix까지 핵심 반례 보강이 닫혔다. 최근에는 decision surface에 `next_priority_recommendation`, `next_priority_reason`를 추가해 observed sample 부족, same-surface 부족, policy pair 부족, baseline 입력 drift, ready 후 operator 병목 검증까지 다음 우선순위 1개를 직접 추천하도록 보강했고, 관련 경계 케이스 회귀 테스트도 함께 고정했다. 이어서 collected observed summary에도 `observed_reason_signals_present`를 노출해 ready 상태에서 operator 병목 검증 추천이 왜 나왔는지 summary surface에서 바로 읽을 수 있게 맞췄다. 이번 배치에서는 invalid observed_output이 여러 필드를 동시에 놓쳐도 rejection case 수와 reason map이 summary/report에 그대로 남도록 explicit rejection metadata 집계를 보강했고, 이와 함께 `로드맵 최신화 + 현재 진행 상태 체크`처럼 혼합 의도의 실제 observed_request를 fixture에 추가해 `$omc-plan` 추천 정밀도를 별도 회귀 테스트로 고정했다. 이어서 autopilot overview에도 collected summary surface(`baseline_line`, `policy_summary`, `reason_signal`)를 직접 노출해 report/decision을 열지 않아도 운영 화면 한 장에서 readiness 의미를 읽게 했고, sample 부족 deferred 경계와 ready reason-signal 경계를 overview fixture로 별도 고정했다. 최근에는 overview에도 `next_priority_recommendation`, `next_priority_reason`가 직접 surfaced되도록 연결해, 운영자가 콘솔 overview만 보고도 다음 1개 액션을 바로 판단할 수 있게 맞췄다. 추가로 `cmd_run` 계열 observed task가 `.omc/runs` 기록을 남기고 `completion_requires_real_runs=true`를 실제 observed 증가 조건으로 묶이도록 보강해, 태스크 완료와 dataset 증가가 어긋나지 않게 맞췄다. 이어서 `observed-collect-reverse`, `observed-ready-surface`, `omc_observed_collect_batch.py`를 추가해 reverse/same-surface observed 축적을 반복 실행할 수 있게 했고, unique runtime task copy 전략으로 중복 fingerprint 없이 샘플을 누적하도록 조정했다. 현재 overview 기준 `observed_samples=21`, `readiness_same_surface=8`, `distinct_policy_pairs=2`, `baseline_comparison_status=ready`가 충족됐고, `v4b-operational-validation` 실행도 completed로 남아 overview / collected summary / decision surface 정렬이 확인됐다. 이번에는 benchmark decision surface가 `recommended_executor`, `executor_reason_summary`, `executor_fallback`까지 직접 노출하고, 해당 summary 계약도 `resolve_policy_summary()` 기준 회귀 테스트로 잠겨 decision/report/contract alignment가 한 번 더 닫혔다. 구현 반례 추가보다 운영 observed 기준 충족과 완료 판정이 더 중요한 단계였고, 그 1차 기준까지 닫혔다. | 운영 신뢰도 유지 및 후속 기준 미세조정 |
| V5. Learned Orchestrator | 미착수 | 데이터 축적 게이트 전 단계다. | 연구용 feature 정의 |
| Operator Experience | 진행중 | `plan/task/review` 진입점과 추천은 있으나, 실제 사용감은 아직 “더 똑똑한 흐름 제어”까지는 아니다. 다만 `omc-plan`, `omc-review`, `omc-task` 출력 contract에 `decision / risk / next_action` 의미를 각 스킬 문맥으로 고정하는 1차 보강, `2-1 next_action 공통화`, `2-2 reroute / delay UX`와 `role_suggest` 시작 스킬 오판 패턴 보강까지 반영됐다. 최근에는 `top-expensive-flows` benchmark CLI와 `verification_checklist`의 고비용 흐름 커버 맵도 추가돼, 병목 흐름을 더 직접적으로 보게 됐다. response-mode 벤치마크에는 실제 review/plan/status/reentry 요청 케이스를 추가했고, next-action 파서는 한글/영문 라벨과 구분자 변형까지 흡수하도록 정리했다. 최근에는 `로드맵 최신화 + 현재 진행 상태 체크`, `로드맵 기준 어떤 작업들이 남은거야` 같은 roadmap-sync observed_request도 fixture에 편입해, status성 표현이 섞여도 `$omc-plan`으로 정렬돼야 하는 경계를 별도 케이스로 잠갔다. 이어서 expensive flow report에 `reroute_reason / reroute_signal / output_bloat_reason / compression_signal`을 직접 노출해, reroute와 과출력 병목을 한 화면에서 읽을 수 있게 맞췄다. 이번에는 `추천이 task야 critique야`, `plan full 이면 critique 해야하나`, `방금 거는 task 해도 되나`, `이거 review 해야해?` 같은 설명/허용 여부 질문도 observed_request fixture로 고정해, 스킬 추천 질문을 곧바로 task 진행으로 오해하지 않도록 wrong_next_step 경계를 한 단계 더 잠갔다. 추가로 `우리 방금 추천은 plan 이었는데 critique 하니까 치명이 나왔어`, `plan 을 했을 때 추천은 task 가 나온 상태고 내가 혹시 몰라서 critique 했더니 구멍을 발견한거잖아`, `critique 활용해서 진행하자` 같은 실제 요청문도 fixture에 넣어, 설명 질문이 아니라 실제 critique reroute가 맞는 경계를 observed_request 기준으로 더 직접 잠갔다. 여기에 더해 현재 구현 라운드를 언제 닫을지 판단할 수 있도록 `Operator Experience 마감 기준`을 문서에 명시했고, `.omc/tasks/operator-experience-validation.json` autopilot completed 증빙까지 확보해 수동 설명력 검증과 자동 실행 완료가 같은 결론으로 수렴하도록 맞췄다. 최근에는 failed status follow-up도 shared decision input으로 이관해 review/debug/build 후속 안내가 status surface와 같은 규칙으로 수렴하도록 맞췄고, autopilot overview follow-up도 같은 shared decision input으로 옮겨 overview surface까지 후속 액션 규칙이 일관되게 수렴하도록 맞췄다. observed_request neutral seed가 candidate 규칙으로 `expected_next_action`을 스스로 만들지 않게 다시 분리해 benchmark 자기검증도 제거했다. 이번에는 `ready` 입력 기준 `overview / collected_summary / report_decision`가 같은 `next_priority_recommendation`과 reason을 유지하는 정합성 회귀 테스트도 추가해, surface별 설명 문구 drift를 다시 바로 잡을 수 있게 했다. | 운영 observed 검증 + 마감 기준 유지 점검 |

## V1 ~ V5 로드맵

### V1. Skill-based Routing

현재 단계다.

- 입력: 스킬명, 자연어 키워드, `task_kind`
- 출력: `mini_default / mini_high / full_default`
- 장점: 단순하고 예측 가능함
- 한계: 요청 전체를 하나의 강도로 보는 경향이 강함

현재 포함 항목:

- `role_suggest`의 `task_kind_hint`
- `autopilot`의 `task_kind` 기반 실행
- `resolve_task_routing()` 공통 helper
- `OMC_ROUTING_POLICY` 프리셋

### V2. Step-level Routing

한 요청을 step으로 나누고, step마다 별도 프로필을 고른다.

예시:

- `plan`: `mini_high`
- `implementation draft`: `mini_default`
- `retry`: `mini_high`
- `final review`: `full_default`

현재 반영된 변화:

- autopilot step schema에 라우팅 메타데이터 추가
- `step.task_kind` 외에 `complexity`, `risk`, `sensitive_paths`, `escalation_policy` 지원
- `preferred_profile`, `complexity`, `risk`, `sensitive_paths`가 실제 profile 선택에 반영된다
- `preferred_profile`도 `ship`/high-risk safety guard는 우회하지 못한다

남은 변화:

- V4 telemetry와 연결된 profile 선택 근거 고도화

메타데이터 책임은 아래처럼 나눈다.

1. `role_suggest`
   요청 단위 초안 메타데이터를 만든다.
   예: `task_kind_hint`, 초기 `risk`, 초기 `complexity`

2. `plan` 또는 task file 생성 단계
   사람 또는 스킬이 step별 메타데이터를 보정한다.
   예: `sensitive_paths`, `preferred_profile`, `escalation_policy`

3. `autopilot` runtime
   최종 normalization과 fallback 책임을 진다.
   누락값 보정, 허용 task kind 정규화, 안전 기본값 강제를 맡는다.

즉, metadata producer는 단일 주체가 아니라
`초안 생성 -> 설계 보정 -> 런타임 정규화` 3단계 파이프라인으로 본다.

### V3. Failure-driven Escalation

자동 전환의 핵심 단계다.

처음에는 싸게 시작하고, 실패 신호가 누적되면 더 강한 모델로 올린다.

현재 상태는 `V3 완료 + V4 multi-run KPI summary 1차 완료`에 가깝다.

- V3-1에서 들어간 것:
  - `failure_class` / `reason_codes` persistence
  - `quality_success`, `failure_class_breakdown` report 반영
  - `completed + BLOCK/HOLD/REVISE`까지 포함한 failure 집계
- V3-2에서 최근 반영된 것:
  - `retry_exhausted`, `failed_critique_loop` 경로의 `escalation_policy` decision persistence 연결 완료
  - critique 경로에서 `decision / reroute_target` 실제 소비까지 연결 완료
  - `task_retry`, `plan_retry` 성공 경로의 `decision / decision_reason / reroute_target` shape 일반화 1차 완료
  - `timeout` 경로의 `decision / decision_reason / reroute_target` shape 일반화 완료
  - `failed`, `failed_branch`, `failed_ambiguous_response` 경로의 decision contract 정규화 완료
  - `bad_entry_skill`, `metadata_missing`, `reroute_loop`에 대한 `orchestration_failure` 1차 decision policy 연결 완료
  - `VERDICT: BLOCK`만 있고 reason code가 없는 task 경로를 `block_without_reason_code`로 승격해 silent completion을 차단했다.
  - `decision_policy_entry` helper 추출로 failure-class별 decision 규칙을 공통 엔진으로 옮길 준비를 마쳤다.
  - critique/review failure step runtime 소비가 `_failure_step_decision` helper를 통해 공통 decision 엔진을 직접 사용하도록 정리됐다.
  - `task_retry`, `plan_retry` 실패 payload도 `_retry_step_payload` helper로 정리돼 retry runtime decision 하드코딩이 제거됐다.
  - `ambiguous_response`, `branch_setup_failed`도 `orchestration_failure`로 승격돼 persisted decision이 explicit hold로 수렴하도록 정리됐다.
- V3-2에서 남은 것:
  - 없음 — `orchestration_failure` runtime 소비 경로 전반이 `decision_policy_entry`를 직접 사용하도록 정리됐다.

실패 신호 예:

- 같은 step 재시도 2회 이상
- 테스트 실패 반복
- review에서 `major` 또는 `critical`
- output format mismatch
- 같은 파일 반복 수정

실패는 최소 3개 클래스로 나눈다.

1. execution failure
   테스트 실패, 명령 실패, timeout, 파일 미생성

2. quality failure
   review `major/critical`, 회귀 발견, DoD 미달

3. orchestration failure
   잘못된 시작 스킬, reroute 반복, output format mismatch,
   step metadata 부족으로 인한 잘못된 선택

같은 retry라도 failure class가 다르면 승격 규칙도 다르게 가져간다.

필수 변화:

- 승격 규칙 엔진 도입
- retry 사유 분류
- `same / escalate / reroute` 선택 로직 추가

### V4. Telemetry-driven Tuning

정책을 감이 아니라 로그로 튜닝한다.

수집 대상:

- 요청 유형
- 선택 모델
- 토큰/비용
- retry 횟수
- 최종 결과
- review severity
- reroute 횟수
- start delay

필수 변화:

- `.omc/` 내 실행 메타데이터 구조화 저장
- benchmark 리포트에 single-run telemetry 추가
- `.omc/runs/` 기준 aggregate KPI summary 추가
- 정책별 비교 리포트 자동화

최소 KPI는 세 가지로 고정한다.

1. reroute rate
   처음 선택한 시작 스킬 또는 경로가 중간에 얼마나 자주 바뀌는지

2. retry-to-success rate
   retry 이후 실제 성공으로 회복되는 비율이 얼마나 되는지

3. cost per successful task
   성공한 작업 1건당 평균 비용이 얼마인지

이 세 가지가 있어야 `품질 개선`과 `비용 증가`를 같은 화면에서 같이 본다.

### V5. Learned Orchestrator

가장 마지막 단계다.

규칙은 여전히 안전망으로 남기되, 실제 선택은 축적된 데이터 기반 점수화 또는 학습형 분류기로 보조한다.

가능한 형태:

- 정책 추천기
- step 난이도 추정기
- “처음부터 Full로 갈 요청” 예측기

단, 이 단계는 V2~V4 로그 품질이 확보된 뒤에만 의미가 있다.

초기 진입 게이트는 아래처럼 둔다.

- step telemetry 300건 이상
- 정책별 비교 가능 케이스 100건 이상
- retry reason 분류 정확도 85% 이상

이 수치를 만족하기 전에는 V5를 연구 단계로만 두고,
운영 기본값은 계속 rule-based 계층을 사용한다.

## Operator Experience

자동 모델 전환과 별개로, 지금 사용 중인 `plan / task / review` 경험 자체를 더 똑똑하게 만드는 축이 필요하다.

이 트랙은 단순한 UX 문구 개선이 아니라,
사용자 요청을 더 정확히 해석하고 다음 액션을 더 잘 추천하는
`사람이 체감하는 오케스트레이션 품질`을 다룬다.

핵심 목표는 다섯 가지다.

1. 시작 스킬 정확도
   `plan`으로 가야 할 요청과 `critique`나 `review`가 먼저 필요한 요청을 더 잘 구분한다.

2. next-action 추천 품질
   파이프라인 기본 순서가 아니라
   현재 병목과 사용자 의도를 우선해 다음 스킬 1개를 추천한다.

3. stop / proceed 경계 명확화
   스킬 완료 후 자동 진입 없이,
   왜 여기서 멈췄는지와 다음 선택지를 짧게 설명한다.

4. reroute / delay UX
   잘못 시작한 경우 “왜 경로를 바꿔야 하는지”를 설명하고
   바로 `critique`, `investigate`, `review`로 reroute할 수 있어야 한다.

5. plan/task/review 결과 구조화
   각 스킬의 출력이 길어져도
   결정, 리스크, 다음 액션, 게이트 상태가 끝까지 보존되어야 한다.

### Operator Experience 마감 기준

아래 조건을 만족하면 Operator Experience의 현재 구현 라운드는 일단 마감 가능으로 본다.

- expensive-flow summary에서 `dominant_flow_kind=wrong_next_step`가 유지되더라도, 최근 observed 질문 보강이 실제 explanation-first 경계를 대표적으로 덮고 있다
- `operator_next_priority=tighten_next_action_routing`와 `operator_validation_status=ready_to_close`가 함께 유지된다
- explanation/permission 질문 축에서 최소한 아래 observed_request 케이스가 fixture로 고정돼 있다
  - `추천이 task야 critique야`
  - `plan full 이면 critique 해야하나`
  - `방금 거는 task 해도 되나`
  - `이거 review 해야해?`
- 새 observed case를 1건 더 넣더라도 현재 우선순위 판단이 바뀌지 않는다

즉, 지금 단계에서는 무한히 케이스를 더 넣는 것보다 현재 마감선을 명시하고, 다음 라운드에서 다른 병목이 실제로 떠오르는지 보는 편이 토큰 대비 효율이 높다.

현재 기준 즉시 넣을 만한 작업은 아래다.

- `role_suggest`에 시작 스킬 오판 패턴 보강
- next-skill 추천 규칙에 “현재 병목 > 기본 파이프라인” 원칙 고정
- `plan/task/review` 출력 contract에 `decision`, `risk`, `next_action` 필수화
- `reroute`와 `delay`를 오케스트레이션 이벤트로 기록

최근 반영된 1차 변화:

- `omc-plan` 출력 contract에 `decision / risk / next_action` 의미를 plan 문맥으로 고정
- `omc-review` 출력 contract에 `decision / risk / next_action` 의미를 review 문맥으로 고정
- `omc-task` 출력 contract에 `decision / risk / next_action` 의미를 task 문맥으로 고정
- 관련 contract regression test 추가로 누락 시 즉시 실패하도록 보강

최근 반영된 2차 변화:

- `2-1 next_action 공통화`: `plan / task / review`가 공통 결정표(`stage / outcome / user_selection_needed`, review는 `ship_intent_explicit` 추가) 기준으로 주추천 1개만 남기도록 정리
- `plan`의 unresolved 경로를 `critique`와 `office-hours`로 의미 분리
- `review`의 approve 경로를 `ship_intent_explicit=yes`일 때만 `$omc-ship`, 그 외에는 사용자 선택 대기로 단일화
- 관련 contract test와 benchmark check로 “한 상태당 next_action 1개” 회귀를 고정
- `2-2 reroute / delay UX`: `omc-critique`, `omc-status` 출력 contract에 조건부 `reroute 이유 / delay 이유 / 재개 조건` 설명 규칙 반영
- `role_suggest`가 `변경 상태 검토`와 `plan 검증` 요청을 각각 `review`, `critique`로 더 정확히 라우팅
- `omc-status`가 `현재 병목 > 기본 파이프라인` 우선순위를 직접 따르도록 추천 규칙을 보강
- `omc-plan`, `omc-review`도 같은 우선순위를 다음 추천 규칙에 직접 반영해 plan/review/status 3축을 맞췄다.
- `top-expensive-flows` benchmark CLI와 `verification_checklist` 커버 맵으로 고비용 흐름 상위 5개를 바로 읽을 수 있게 정리했다.

남은 2차 변화:

- `omc-plan`, `omc-review`, `omc-task` 1차 계약 보강은 끝났다.
- `2-1 next_action 공통화`도 끝났다.
- `2-2 reroute / delay UX`도 1차 완료됐다.
- next-action 품질 보강 3차의 plan/review/status 병목 우선 추천은 반영 완료다.
- response-mode 벤치마크에 실제 review/plan/status/reentry 요청 케이스를 넣어 next-action 정밀도를 더 직접 검증한다.
- next-action 파서는 한글/영문 라벨과 여러 구분자 변형을 흡수하도록 정리됐다.
- expensive flow report도 이제 top 5를 단일 `wrong_next_step`만으로 채우지 않고 `wrong_next_step / reroute_loop / output_bloat / over_stage_entry`를 함께 surface하도록 보강됐다.
- summary에도 `dominant_flow_kind`, `operator_next_priority`, `operator_next_priority_reason`를 추가해 어떤 병목을 먼저 줄여야 하는지 한 화면에서 바로 읽게 맞췄다.
- `role_suggest`도 Operator Experience 정리 요청을 일반 review/task가 아니라 `$omc-plan`으로 먼저 정렬하도록 보강됐다.
- 다음 남은 조각은 fixture/benchmark에도 이 우선순위를 더 직접 반영해 실제 기대 추천과의 오차를 줄이는 것이 아니라, 운영 observed 데이터에서 이 summary 신호의 설명력이 충분한지 검증하는 일이다.

이 트랙은 V2~V4와 연결된다.

- V2가 step metadata를 더 잘 가지면 시작 스킬 판단이 좋아진다.
- V3가 failure class를 가지면 reroute 품질이 좋아진다.
- V4가 telemetry를 쌓으면 어떤 추천이 실제로 유효했는지 측정할 수 있다.

## 제품 아키텍처

완전 자동 모델 전환 제품은 아래 계층으로 본다.

1. Classifier
   요청과 컨텍스트를 읽고 복잡도/리스크/범위를 추정한다.

2. Planner
   요청을 step으로 나누고 각 step의 목적을 정한다.

3. Router
   각 step에 맞는 모델 강도를 고른다.

4. Evaluator
   테스트, review, 실행 결과를 읽고 실패 신호를 수집한다.

5. Escalator
   실패 시 같은 모델 유지 / 상향 / reroute를 결정한다.

6. Telemetry
   비용, 성공률, retry율, 품질 지표를 남긴다.

## 운영 원칙

- 기본은 자동 선택
- 필요하면 사용자 override 가능
- 선택 이유는 설명 가능해야 함
- 고가 모델은 처음부터 남발하지 않고 실패 신호 기반으로 승격
- `ship` 같은 고위험 단계는 보수적 유지

## 현재 구현 연결점

현재 V1의 실제 SSOT는 아래 파일들이다.

- [scripts/omc_role_suggest.py](/Users/noseunglae/Downloads/dev/omc_kit/scripts/omc_role_suggest.py)
- [scripts/omc_autopilot.py](/Users/noseunglae/Downloads/dev/omc_kit/scripts/omc_autopilot.py)
- [scripts/omc_exec.py](/Users/noseunglae/Downloads/dev/omc_kit/scripts/omc_exec.py)

관련 전략/사용 문서는 아래를 함께 본다.

- [docs/fugu_benchmark.md](/Users/noseunglae/Downloads/dev/omc_kit/docs/fugu_benchmark.md)
- [docs/orchestration_usage.md](/Users/noseunglae/Downloads/dev/omc_kit/docs/orchestration_usage.md)

## 즉시 착수 1순위 4개

### 1. Step metadata 확장

가장 먼저 해야 한다.

추가 후보:

- `complexity`
- `risk`
- `sensitive_paths`
- `preferred_profile`
- `escalation_policy`

이유:

- 지금은 `task_kind` 하나로 너무 많은 판단을 대신한다.
- V2, V3, V4 전부의 기반이 된다.

### 2. Retry escalation engine

두 번째 우선순위다.

최소 규칙 예:

- 첫 실패: same profile
- 두 번째 실패: `mini_high`
- 세 번째 실패 또는 review major: `full_default`

이유:

- 자동 전환 제품의 체감 가치는 여기서 가장 크게 생긴다.
- 사용자는 “처음엔 싸게, 막히면 알아서 세게”를 원한다.

### 3. Execution telemetry 저장

세 번째 우선순위다.

최소 저장 항목:

- chosen profile
- task kind
- retry count
- final result
- token usage
- cost estimate

이유:

- 이후 benchmark와 learned orchestrator의 재료가 된다.
- 지금 단계부터 로그가 없으면 다음 단계는 계속 감으로 가게 된다.

### 4. Plan / Task / Review experience 고도화

네 번째 우선순위다.

최소 작업:

- 시작 스킬 판정 정확도 개선
- next-action 추천 단일화
- `decision / risk / next_action` 구조 강제
- reroute / delay 기록 추가

이유:

- 사용자는 모델 라우팅보다 먼저 “지금 이 흐름이 똑똑한가”를 체감한다.
- plan, task, review 경험이 좋아져야 자동 모델 전환도 신뢰받는다.

## 이번 분기 현실적 목표

이번 분기 목표는 V5가 아니라 V3 완료 + V4 multi-run KPI summary 1차 완료까지였다.
현재 구현은 이미 그 선을 넘었고, 이제는 V4 데이터 축적과 Operator Experience 정교화가 다음 우선순위다.

이번 분기 핵심 목표는 아래 3개다.

- multi-run telemetry 축적
- 정책 비교 리포트 정교화
- Plan / Task / Review experience 고도화

`Plan / Task / Review experience 고도화`는 이번 분기에도 병행할 수 있지만,
핵심 엔진 로드맵을 흐리지 않도록 `병행 UX 트랙`으로 취급한다.

즉, 분기 운영 기준은 아래처럼 본다.

- 핵심 엔진 트랙: telemetry / policy comparison / data accumulation
- 병행 UX 트랙: 시작 스킬 정확도 / next-action 품질 / reroute UX

## 작업 운영 원칙

- 코드 작업과 설치/정리를 분리한다.
- plan -> task -> review 왕복은 위험할 때만 반복한다.
- 같은 저장소 안에서 한 번에 끝내는 것을 우선한다.
- 설치/동기화 작업은 setup / gitignore / hook 같은 운영성 변경에 한해 예외적으로 묶는다.
- 저장소를 넘는 작업은 코드와 설치를 분리하고, setup은 대상별로 끝낸다.

## 바로 다음 작업 계획

현재 기준 상태는 아래와 같다.

decision engine 잔여 예외 감사는 완료됐고, 추가 코드 gap은 발견되지 않았다.
`next-action 품질 보강 3차`는 완료됐고, 이제 다음 우선순위는 아래와 같다.

1. next-action 품질 보강 3차 - 완료
   `2-2 reroute / delay UX`, 시작 스킬 정확도 보강, `omc-status`, `omc-plan`, `omc-review`의 병목 우선 추천은 반영됐다.
   다음은 fixture/benchmark에도 같은 우선순위를 더 직접 반영해, 같은 상태에서도 사용자 의도 차이를 더 잘 구분하는 것이다. 현재는 top-expensive-flows CLI가 생겨서, 이 작업의 병목 후보를 훨씬 빨리 찾을 수 있고, review intent 실제 요청 케이스와 next-action gap/next_action_incomplete도 함께 드러난다. response-mode fixture는 29 cases까지 늘었고 observed_request / expected_next_action 케이스도 더 촘촘히 고정됐다.

2. telemetry report 정리 2차 - 완료
   정책 비교 리포트는 1차 자동화가 들어갔고, benchmark/report 출력도 비교 가능한 형태로 정리됐다.
   readiness와 baseline comparison 상태는 `policy_comparison_summary`까지 포함해 deferred/ready 판정을 한 줄로 바로 읽을 수 있게 됐다.
   collected observed summary에도 `observed_data_bottleneck_summary`를 넣어 샘플 부족과 rejected observed_output reason을 함께 읽게 만들었다.
   이제 V4의 다음 목표는 더 많은 실제 실행 데이터를 쌓고 정책 기준을 더 정교하게 만드는 것이다.

최근 보강:
- `Executor Recommendation Surface`의 추천-only acceptance line과 handoff acceptance binding을 문서/테스트로 고정해, executor surface가 어디까지 설명하고 어디서 reroute layer로 넘기는지 경계를 명시했다.
- 추가로 fallback은 executor 대체안 제시, reroute는 다음 경로 결정 소유라는 책임 분리를 문서/테스트로 더 직접 고정했다.

## 다음 순환 목표

현재 기준 요약은 아래와 같다.

- V1 완료
- V2 완료
- V3 완료
- V4 완료
- Operator Experience는 후속 운영 UX 정리 단계

즉, 구현 로드맵 기준으로는 `V4-A 구현 마감`뿐 아니라 `V4-B 운영 완료 판정 1차`까지 닫혔고, 다음 순환부터는 구현 잔여가 아니라 운영 유지·검증과 Operator Experience 후속 정리로 본다.

다음 순환에서 볼 후보는 아래와 같다.

1. V4 multi-run KPI summary 2차
   이 트랙은 `V4-A 구현 마감`과 `V4-B 운영 완료 판정`으로 나눠서 봤고, 현재는 1차 완료 판정까지 도달했다.
   `V4-B 운영 검증 실행 준비`를 넘어서 실제 `v4b-operational-validation` 실행이 completed로 남아 있고, overview 기준 `operational_validation_readiness=start-ready`, `observed_samples=21`, `same-surface=8`, `policy pair=2`가 함께 확인됐다.
   실제 observed run 축적 20회 이상 조건은 충족됐고, overview / collected summary / decision 정렬도 운영 검증 태스크로 다시 확인했다.
   최근에는 autopilot 운영 유지 측면에서도 stale `running` state를 재실행 전에 자동 복구하도록 보강했다. live PID가 남아 있으면 재실행을 막고, dead PID와 missing PID는 서로 다른 `stale_reason`으로 step/task state에 남겨 운영자가 재시도 전후 맥락을 같은 surface에서 읽을 수 있게 맞췄다.
   핵심 반례 보강은 사실상 완료됐고, 이제는 실제 실행 케이스를 더 쌓아 정책 비교의 신뢰도를 높이는 운영 검증 단계다.
   `reroute rate`, `retry-to-success rate`, `cost per successful task` 기준 샘플을 더 모은다.
   neutral observed seed와 observed_output 실데이터를 구분한 상태에서 same-surface observed evidence를 더 누적한다.
   readiness/baseline 상태 문구는 이미 report와 overview에 실리므로, threshold taxonomy와 candidate count-aware check이 들어간 현재 기준에서는 다음 단계가 deferred/ready 판정이 실제 observed dataset 누적에서 얼마나 안정적인지 검증하는 일이다.
   최근에는 collected observed summary도 `policy pair 2/2` 부족을 별도 병목으로 표시하도록 맞췄고, same-surface observed evidence가 `0 → 1 → 2`로 변할 때 collected summary / report / taxonomy가 함께 기대값을 유지하는 회귀 테스트도 추가했다.
   추가로 multi-run KPI 3종은 이제 collected observed summary와 comparison summary 양쪽에 함께 노출되고, run fixture 기반 회귀 테스트로 고정됐다. 최근에는 neutral observed_request seed가 readiness용 policy pair를 부풀려 `가짜 ready`를 만들지 않도록, 전체 관측 분포와 readiness 입력 분포도 분리했다.
   여기서의 threshold taxonomy/candidate 비교는 구현 정합성 확인용이며, threshold 숫자 자체의 정책 타당성은 실제 observed multi-run 비교로만 판단한다.
   invalid observed_output이 조용히 사라지지 않도록 rejection summary는 들어갔고, 1차로 collected observed summary에서도 rejection reason 병목을 함께 읽게 만들었다. 최근에는 이 rejection context가 comparison decision의 pending 경로뿐 아니라 ready 경로의 `policy_comparison_summary`에도 남도록 맞춰, readiness 달성 후에도 버려진 실데이터 수를 한 줄에서 같이 읽게 했다. 추가로 ready mixed fixture에 neutral observed_request를 함께 섞어도 readiness sample/policy-pair count가 부풀지 않고 ready 판단이 유지되는 회귀 테스트를 고정했고, 이어서 accumulated observed dataset fixture에서도 collected summary와 report summary가 같은 ready 결론으로 수렴하는지 별도 케이스로 잠갔다. 이번에는 count 기준이 모두 충족돼도 `baseline_comparison_ready=false`이면 decision이 ready처럼 보이지 않도록 drift guard를 추가해, collected/report와 decision 문구의 비정상 어긋남도 별도 회귀 테스트로 잠갔다. 이어서 rejection reason map이 비어도 `rejected observed_output` count 자체는 decision bottleneck에서 사라지지 않도록 count-only suffix 보강까지 넣었고, 마지막으로 collected observed summary도 `readiness_blocker_line`, sample/same-surface gap, `baseline_comparison_ready`를 함께 노출해 report/decision과 같은 readiness 경계 신호를 직접 읽게 맞췄다. 이어서 ready mixed / accumulated observed fixture에서도 이 gap과 `baseline_comparison_ready`가 그대로 유지되는지 직접 검증하도록 회귀 테스트를 보강했다. ready 경로의 `policy_comparison_summary`에서도 같은 count-only suffix가 유지됨을 별도 테스트로 확인했다. 최근에는 observed request에서 포착된 reroute/과출력 신호도 최종 decision surface인 `policy_comparison_summary`에 `reason signals observed`로 최소 연결해, 병목 문구와 설명 신호 존재를 한 줄에서 같이 읽게 했다. 이어서 `next_priority_recommendation`, `next_priority_reason`를 decision payload에 추가해 sample/same-surface/policy-pair/baseline drift/ready 후 operator 병목 중 무엇을 다음 우선순위로 볼지 직접 surfaced하도록 맞췄고, 해당 경계 분기 회귀 테스트도 함께 고정했다. 최근에는 invalid same-surface noise가 있어도 valid same-surface evidence가 `0 → 1`로 바뀔 때 deferred/ready 전이가 정확히 유지되는 fixture를 추가했고, 이어서 same-surface는 이미 충족된 상태에서 readiness용 policy pair가 `1 → 2`로 바뀌는 혼합 fixture도 별도 회귀 테스트로 잠가 threshold가 노이즈에 흔들리지 않게 했다. 추가로 accumulated observed dataset에서 reason signal이 summary와 final decision 양쪽에 같은 우선순위 추천으로 유지되는 정렬 회귀 테스트도 고정했다. 이번에는 collected observed summary도 `baseline_comparison_status`, `next_kpi_blocker`를 직접 surface하도록 맞춰 decision을 내려가지 않아도 운영 화면에서 ready/deferred와 blocker를 바로 읽게 했고, sample 부족 deferred 경계도 별도 운영형 fixture로 잠가 collected summary / final decision이 같은 `insufficient_observed_samples` 판단을 유지하는지 고정했다. 이어서 `completion_requires_real_runs=true` 태스크가 dry-run만으로 실제 observed 완료처럼 보이지 않도록 `simulated` 메타데이터, legacy dry-run heuristic, `cmd_status`의 `completed (dry-run)` 표기, overview readiness 집계 제외까지 묶어 read/write/status 호환을 닫았다. 이제 V4 2차는 새 반례를 더 추가하는 구현 단계가 아니라, 현재 완료 기준을 유지하면서 운영 데이터 drift를 감시하는 유지·검증 단계로 넘어간다.

2. Operator Experience 4차
   구현 1차는 반영됐다. `plan / task / review`의 next-action 품질과 response-mode fixture 보강, Operator Experience 정리 요청의 `$omc-plan` 정렬, expensive flow report의 다양성 surface와 우선순위 summary가 들어갔다.
   최근에는 expensive-flow summary에도 `operator_validation_status`, `output_bloat_followup_needed`, `output_bloat_status_line`를 추가해, `output_bloat`가 관측되더라도 주 병목이 아닐 때는 follow-up 구현보다 `wrong_next_step` 축을 계속 우선해야 한다는 운영 판정을 한 화면에서 읽게 맞췄다.
   추가로 `"plan으로 계획 세우고 task 했는데 왜 작업을 선언하라는거지"` observed 요청을 wrong-next-step 회귀 테스트로 별도 고정해, 재선언 혼란 케이스에서도 baseline이 `$omc-task`로 과수렴하고 candidate는 `사용자 선택 대기`로 멈춰야 한다는 경계를 explicit하게 잠갔다.
   현재 기준으로는 `dominant_flow_kind=wrong_next_step`, `operator_next_priority=tighten_next_action_routing`, `operator_validation_status=ready_to_close`가 수동 검증과 autopilot 검증에서 함께 일관되게 확인됐다.
   `.omc/tasks/operator-experience-validation.json`은 이제 `completed`로 남고, `benchmark-report`에도 `operational_validation_stage=operator_experience_validation` 메타데이터가 보존돼 자동 실행 완료 증빙까지 확보됐다.
   즉, 이 트랙은 구현 중심 다음 우선순위가 아니라 운영 검증 중심 후속 정리 단계로 이동했고, 현재 판정은 `조건부 통과`가 아니라 `1차 완료 후 유지·검증 단계`에 가깝다.

3. Learned orchestrator 진입 조건 정리
   데이터가 충분한지 판단하는 gate를 더 명시적으로 만든다.
   telemetry 축적 기준과 정책 비교 가능 케이스 기준을 함께 고정한다.

현재 실행 큐는 아래 순서로 유지한다.

1. `public policy summary helper` 묶음 변경 커밋 마감
2. `.omc/tasks/v4b-operational-maintenance.json` 기준 운영 observed 유지 검증
3. `.omc/tasks/operator-experience-validation.json` 기준 Operator Experience 설명력 검증

### 최소 KPI 기준

`V4 multi-run KPI summary 2차`는 아래 조건을 만족해야 완료로 본다.

- observed_request / observed_output 기준 multi-run 실행 샘플 20회 이상
- neutral observed seed는 수집량으로만 보이고 readiness 입력에서는 제외된다
- observed_output은 `comparison_scope`, response sample을 보존하되 `mode_accuracy` / `task_start_delay` decision metric을 공짜로 밀어 올리지 않는다
- observed_output producer는 partial metadata를 허용하지 않고, task metadata backfill 후에도 필수 schema가 비면 benchmark payload를 남기지 않는다
- threshold taxonomy(`ready / pending / ambiguous`)와 candidate 비교가 같은 observed fixture 기준으로 재현 가능하다
- distinct policy pair 2개 이상
- `reroute rate`, `retry-to-success rate`, `cost per successful task` 3개 KPI가 모두 표에 노출
- 한 번의 실행 요약이 아니라, 반복 실행에서 같은 형식으로 재현 가능
- baseline은 직전 정책 또는 고정 기준값 대비로 정의된다
- baseline 대비 개선/악화 판단 문구가 함께 표시된다
- baseline/timebox 예외 허용은 운영 검증 목적에서 timebox로만 허용한다
- `Operator Experience 4차`는 구현 우선순위의 다음 단계로 올리되, V4 2차의 남은 운영 검증과는 분리해서 다룬다

### 운영 유지 체크포인트

- dry-run completion은 운영 완료 샘플에 포함하지 않는다
- `operational_validation_readiness=start-ready`가 overview / collected summary / decision surface에서 같이 보여야 한다
- `next_priority_recommendation`과 `next_priority_reason`은 ready 이후에도 operator follow-up 문맥을 잃지 않아야 한다
- `wrong_next_step`이 주 병목이 아닌 경우에만 output_bloat follow-up을 다음 구현 후보로 올린다
- `wrong_next_step`이 주 병목이 아니면 `next_priority_recommendation`은 `compress_operator_outputs`로 바로 바뀌지 않는다
- autopilot 재실행 시 기존 task state가 `running`이면 live PID는 차단하고, dead/missing PID는 `stale_recovery` 이력과 분리된 `stale_reason`으로 복구돼야 한다
- `.omc/tasks/v4b-operational-maintenance.json`은 `resume_failed=true`, observed metadata contract, `expect_only` step 구성을 갖춰 headless 자유응답 때문에 유지 검증이 멈추지 않도록 고정돼야 한다

`Operator Experience 4차`는 아래 조건을 만족해야 한다.

- `plan / task / review` next-action이 각기 1개로 수렴
- 시작 스킬 오판 패턴을 benchmark로 재현 가능
- reroute / delay 이유와 재개 조건이 한 화면에서 읽힘
- 첫 구현 배치는 `response-mode fixture 기반 next-action 의도 분기 정밀화`로 시작한다

## V5 후보 트랙 구체화

Fugu식 오케스트레이션에서 실제로 가져올 가치가 높은 다음 3개 트랙은 아래와 같다.

### 1. Next-step Decision Engine 일반화

- 목표: `plan / task / review / ship / status` 전반의 다음 액션 추천을 공통 decision engine으로 수렴
- 문제: 일부 surface는 정렬됐지만 스킬별 추천 규칙이 아직 부분적으로 흩어져 있다
- 완료 기준:
  - 같은 입력 상태면 어떤 surface에서도 같은 `next_action / next_priority`가 나온다
  - `wrong_next_step / reroute / output_bloat / over_stage_entry` 우선순위가 같은 규칙표로 설명된다
  - 대표 반례가 fixture와 benchmark 회귀 테스트로 고정된다
- 산출물:
  - 공통 decision input schema
  - 스킬별 adapter
  - regression fixture set
- 최근 반영:
  - readiness 쪽에 먼저 들어가 있던 input-builder 패턴을 operator priority 경로에도 적용해, `wrong_next_step / reroute / output_bloat / over_stage_entry` 판단 입력이 `core + extension` shape로 한 번 감싸지도록 1차 정렬했다.
  - 이어서 `next_priority`도 `report_decision / collected_summary` 양쪽에서 같은 surface adapter builder를 타도록 이관해, `source_surface + extension` 조립 규칙을 한 곳으로 모았다.
  - 추가로 `resolve_next_priority / resolve_next_priority_from_input`를 공통 모듈로 올려 `benchmark / autopilot`이 같은 priority rule을 공유하도록 2차 공통화를 마쳤다.
  - overview도 이제 `shared input -> shared resolver` 경로를 직접 타도록 보강했고, 해당 경로는 전용 회귀 테스트로 잠가 local unpacking drift를 막았다.
  - 최근에는 `operator priority`에 이어 `output_bloat validation`과 `operator explanation`도 shared decision input contract로 이관해, benchmark 쪽이 thin wrapper만 남기고 같은 resolver를 직접 타도록 정리했다.
  - 이어서 `overview_summary`의 `next_priority` adapter도 `source_surface=overview_summary`까지 포함한 공통 input shape로 잠가, surface별 adapter drift를 fixture 수준에서 바로 감지할 수 있게 맞췄다.
  - 추가로 `operator explanation`도 ready flow 기준 shared resolver와 benchmark adapter가 같은 설명 라인을 유지하는 parity fixture를 넣어, explanation surface drift까지 같은 방식으로 잠갔다.
- 구현 순서:
  1. decision input schema 고정 - 완료
  2. priority rule 공통화 - 완료
  3. skill adapter 이관 - 사실상 완료 (operator priority / output_bloat validation / operator explanation / overview next_priority parity 고정)
  4. fixture 확대 - 대표 반례 마감 단계 (새 observed failure가 다시 잡힐 때만 추가 확장)

### 2. Cost-Quality Policy Layer

- 목표: 작업 난이도와 실패 비용에 따라 `cost_saver / balanced / quality_first`를 추천하거나 반자동 결정
- 문제: 지금은 사용자가 모델 강도와 thinking 강도를 직접 판단해야 하는 비중이 크다
- 완료 기준:
  - 요청 난이도, 실패 비용, 범위, ambiguity를 보고 policy profile을 추천한다
  - 선택 근거가 `reason summary`로 남고 benchmark에서 비용/품질 차이를 재현 가능하다
  - 토큰 낭비 케이스와 품질 실패 케이스를 같은 비교 리포트에서 읽을 수 있다
- 산출물:
  - policy profile 3종 정의
  - 선택 규칙표
  - observed 비교 리포트
- 구현 순서:
  1. profile 정의
  2. trigger 조건 정의
  3. benchmark case 연결
  4. summary surface 노출

policy decision input SSOT:
- `failure_cost`
- `ambiguity`
- `operator_goal`

입력 축은 3개로 시작한다.
`failure_cost / ambiguity / operator_goal`만 Cost-Quality Policy Layer의 SSOT로 쓰고,
`task_kind / risk / review_severity / retry_count / sensitive_path` 같은 신호는
Decision Engine 또는 runtime routing에서 파생 입력으로만 사용한다.

- `cost_saver`: low failure cost + low ambiguity + speed goal
- `balanced`: 기본값 및 low-confidence fallback
- `quality_first`: high failure cost 또는 quality goal 우선
- `confidence=low`이면 `balanced + user_selection_needed=yes`로 고정한다.

Layer boundary:
- Cost-Quality Policy Layer: 정책 프로필 추천과 설명만 담당
- Executor Recommendation Surface: 실행기/모델 매핑만 담당
- Reroute Layer: 실패 후 fallback / retry / delay만 담당

최근 반영:
- policy helper 1차는 위 3축 기준으로 축소 정렬됐다.
- 기본 반환은 `balanced`로 보수화했고, `cost_saver`는 `low failure cost + low ambiguity + speed goal`의 명시적 lightweight 조건에서만 선택되게 제한했다.
- low-confidence 경계는 `balanced + user_selection_needed=yes` output contract로 고정했다.
- `omc-plan` surface 1차도 연결되어 `policy_profile / policy_reason_summary / policy_confidence`와 low-confidence fallback 규칙을 plan 계약에서 직접 읽을 수 있게 맞췄다.
- benchmark/report surface에도 `recommended_policy_profile / policy_reason_summary / policy_confidence / user_selection_needed`가 직접 노출되고, 관련 회귀 테스트로 summary 계약이 고정됐다.

설계상 남은 갭:
- ambiguity/failure_cost/operator_goal 조합별 confidence threshold 표를 아직 문서에 고정하지 않았다.
- observed 비교 리포트에서 policy 추천 적중/과보수/과공격을 어떻게 판정할지 acceptance line이 아직 부족하다.
- Executor Recommendation Surface로 넘기는 handoff contract를 summary field 기준으로 더 명시해야 한다.

confidence threshold 표:

| failure_cost | ambiguity | operator_goal | recommended_policy_profile | confidence |
|---|---|---|---|---|
| low | low | speed | cost_saver | high |
| high | high | quality | quality_first | high |
| medium | high | balanced | balanced | low |

policy comparison acceptance line:
- 적중(hit): observed outcome과 policy recommendation이 같은 방향으로 수렴
- 과보수(over-conservative): balanced/quality_first가 반복되지만 실패 비용 대비 과도한 비용 증가가 확인됨
- 과공격(over-aggressive): cost_saver가 선택됐지만 retry/review failure로 곧바로 상향 필요가 확인됨

executor handoff summary fields:
- `recommended_policy_profile`
- `policy_reason_summary`
- `policy_confidence`
- `user_selection_needed`

후속 구현 순서:
1. confidence threshold 표 문서화
2. policy comparison acceptance line 추가
3. summary surface handoff contract 고정

### 3. Executor Recommendation Surface

- 목표: Codex / Claude / Gemini 또는 모델 강도를 작업 성격에 따라 추천
- 문제: 현재는 실행기와 강도를 사람이 자주 직접 고른다
- 완료 기준:
  - `추천 실행기 + 이유 + fallback`이 자동 산출된다
  - 실패 클래스별 reroute rule이 있다
  - 사람 승인 하에서만 executor 전환이 가능하다
- 산출물:
  - executor capability matrix
  - routing rule table
  - fallback / reroute rule
- 구현 순서:
  1. 추천-only read mode
  2. 승인 기반 reroute
  3. 제한적 auto-switch

executor recommendation input contract:
- `task_kind`
- `recommended_policy_profile`
- `risk`
- `sensitive_paths`
- `operator_goal`

executor recommendation output contract:
- `recommended_executor`
- `executor_reason_summary`
- `executor_fallback`
- `user_selection_needed`

executor 설계상 남은 갭:
- 추천-only read mode의 acceptance line이 아직 부족하다.
- executor fallback과 reroute layer의 책임 분리 문구가 더 직접적이어야 한다.
- Cost-Quality Layer에서 넘어오는 handoff summary field를 executor surface 기준으로 고정해야 한다.

executor acceptance line:
- pass: `recommended_executor / executor_reason_summary / executor_fallback / user_selection_needed` 4개 필드가 한 surface에서 함께 설명된다.
- hold: 추천은 나왔지만 `executor_reason_summary` 또는 `user_selection_needed`가 비어 사람이 바로 선택 근거를 읽을 수 없다.
- fallback: 추천 실행기가 막혀도 `executor_fallback`이 같은 task_kind / policy_profile 문맥에서 바로 제시된다.
- reroute: fallback으로도 해결되지 않는 실패만 reroute layer로 넘기며, executor surface는 실패 이후 경로 결정을 직접 소유하지 않는다.

fallback vs reroute 책임 분리:
- fallback은 추천 실행기 선택 이후의 대체안 제시에 한정되며, task 재분해나 policy 재선택을 트리거하지 않는다.
- reroute는 fallback 실패 또는 executor surface 바깥 신호(`retry_exhausted`, `quality_failure`, `orchestration_failure`)가 확인됐을 때만 열린다.
- executor surface는 `어떤 실행기를 먼저 쓸지`와 `막혔을 때 어떤 실행기로 한 번 더 시도할지`까지만 답한다.
- reroute layer는 `같은 실행기군 재시도`가 아니라 `plan_retry / critique / delay / hold` 같은 다음 경로 결정을 소유한다.

executor handoff acceptance binding:
- `recommended_policy_profile`와 `policy_confidence`는 executor 선택 강도의 근거로 읽혀야 한다.
- `policy_reason_summary`는 `executor_reason_summary`와 서로 모순 없이 이어져야 한다.
- `user_selection_needed=yes`면 executor surface도 추천-only로 멈추고 자동 전환을 시도하지 않는다.

executor 후속 구현 순서:
1. executor input/output contract 문서화
2. fallback vs reroute 책임 분리
3. acceptance line 및 handoff summary field 고정

## Learned Orchestrator 진입 게이트

V5는 "바로 구현해볼 만한 다음 기능"이 아니라, 아래 gate를 통과했을 때만 여는 연구/제품화 단계로 본다.

### 진입 조건

- V4 운영 observed 유지 검증이 계속 `ready`를 유지한다
- Operator Experience가 `ready_to_close` 상태를 유지하고, 새 observed 케이스 1건 추가에도 주 우선순위가 흔들리지 않는다
- `Decision Engine 일반화`, `Cost-Quality Policy Layer`, `Executor Recommendation Surface`의 추천-only surface가 먼저 정리된다
- 사람 승인 없는 자동 executor 전환 없이도 policy/executor 추천 품질을 설명 가능하게 유지한다

### 보류 조건

- observed run은 충분하지만 policy drift 설명이 아직 약하다
- wrong_next_step가 여전히 주 병목인데 learned layer로 덮으려 한다
- executor recommendation이 추천-only 단계도 닫히지 않았다
- 운영 검증보다 구현 욕심이 앞서서 fallback/guard 설명력이 약해진다

### 시작 전 금지선

- learned orchestrator를 runtime closed-loop auto-switch로 바로 연결하지 않는다
- benchmark/fixture 없이 learned policy를 넣지 않는다
- 기존 decision engine 설명 가능성을 희생하면서 black-box 점수를 올리지 않는다

### 진입 체크리스트

- telemetry 300건 이상
- 정책 비교 가능 케이스 100건 이상
- retry reason 분류 정확도 85% 이상
- V4 운영 유지 검증이 최근 기준에서도 `ready`를 유지한다
- Operator Experience가 새 observed 케이스 1건 추가에도 `wrong_next_step` 우선순위를 흔들지 않는다

### 진입 산출물

- learned candidate scorecard 1차
- rule-based baseline comparison report
- shadow recommendation audit log
- learned 후보가 기존 추천을 뒤집는 대표 케이스 10개 이상

### 첫 구현 범위

- 추천-only shadow mode로 시작한다
- 기존 rule-based decision은 primary, learned score는 secondary로 병렬 기록한다
- executor 자동 전환이 아니라 `추천 차이 감지`와 `설명 품질 비교`만 먼저 다룬다
- 기존 `next_priority_recommendation` surface와 충돌하면 learned 결과는 참고 정보로만 남긴다

한 줄 기준:

- `추천 엔진 3축이 먼저, learned layer는 맨 마지막`

## Fugu식 기능 MVP 설계

## 로드맵 검증 매트릭스

지금 단계에서는 "많이 수정했다"와 "실제로 반영됐다"를 같은 말로 쓰지 않는다.
로드맵 완료 항목은 아래 기준으로 다시 확인한다.

| 로드맵 완료 항목 | 실제 반영 증거 | Fugu 비교에 쓰는 축 | 판정 규칙 |
|---|---|---|---|
| V4. Telemetry-driven Tuning | roadmap 문구 + 관련 테스트 + overview/summary/autopilot completed 상태 | feedback loop / policy tuning | 문서만 반영이면 `부분 반영`, 테스트/실행 증거까지 있으면 `반영 확인` |
| Operator Experience | roadmap 문구 + response-mode fixture + expensive-flow summary + validation task 상태 | next-action quality / operator control | 문서+fixture만 있으면 `반영 확인`, 운영 판정까지 닫히면 `체감 개선 확인` |
| Fugu식 MVP 설계 | benchmark 문서 + roadmap MVP 섹션 + 후속 설계 태스크 | single-entry runtime orchestration | 설계만 있으면 `문서만 반영`, 구현/검증이 붙어야 `반영 확인` |

Fugu 비교 문구는 `현재 상태 참조`와 `반영 검증 완료`를 구분한다.

완전 자동 전환보다 먼저 넣을 최소 제품 단위는 아래 3개다.

### MVP 1. Decision Engine Core

- 입력:
  - `task_kind`
  - `ambiguity_level`
  - `failure_cost`
  - `scope_size`
  - `observed_bottleneck`
  - `ship_intent`
- 출력:
  - `recommended_next_skill`
  - `recommended_policy_profile`
  - `recommended_executor`
  - `reason_summary`
  - `confidence`
- 원칙:
  - 초기에 자동 실행은 하지 않고 추천 엔진으로만 시작한다
  - 스킬은 편의성 레이어이고 강제 진입점은 기존 CLI 가드를 유지한다

### MVP 2. Policy Profile 3종

- `cost_saver`: 짧은 조회, 작은 수정, low-risk 작업
- `balanced`: 일반 개발 기본값
- `quality_first`: 설계, 리팩터링, 교차 영향 큰 작업
- 각 profile은 권장 model / thinking / executor 전략을 가진다

### MVP 3. Executor Recommendation Surface

- 출력 예시:
  - `추천 실행기: Codex`
  - `추천 프로필: balanced`
  - `이유: 범위 고정, 코드 수정 중심, 교차 시스템 리스크 중간`
  - `fallback: Claude Code quality_first`

### MVP 제외 범위

- 자동 executor 전환
- 정책 자동 학습
- 무인 closed-loop reroute

위 3개는 구현비와 운영 리스크가 커서 V5 초기 MVP에서는 제외한다.

## 토큰 대비 효과 점수표

점수 기준은 아래처럼 본다.

- 효과: 5 높음
- 구현비: 5 큼
- 토큰절감: 5 큼
- 리스크: 5 큼

| 항목 | 효과 | 구현비 | 토큰절감 | 리스크 | 총평 |
|---|---:|---:|---:|---:|---|
| Decision Engine 일반화 | 5 | 3 | 4 | 2 | 가장 먼저 |
| Cost-Quality Policy Layer | 5 | 3 | 5 | 2 | 두 번째 |
| Executor Recommendation Surface | 4 | 2 | 3 | 1 | 빠른 승리 |
| 승인 기반 reroute | 4 | 4 | 3 | 3 | 중기 |
| 자동 executor switch | 4 | 5 | 4 | 4 | 나중 |
| learned orchestrator | 5 | 5 | 4 | 5 | 맨 나중 |

현재 기준 우선순위는 아래 3개로 고정한다.

1. `Decision Engine 일반화`
2. `Cost-Quality Policy Layer`
3. `Executor Recommendation Surface`

## Decision Engine Spec

runtime decision은 `failure_class / escalation_policy / retry_count / reason_codes` 조합으로 결정한다.
핵심은 failure path마다 분기문을 따로 늘리는 것이 아니라, 같은 입력 shape에서 같은 decision을 내리게 하는 것이다.

기본 상태 전이표는 아래처럼 고정한다.

- `execution_failure` + default policy + threshold 미만
  - decision: `same`
  - reroute_target: 없음
  - 의미: 현재 경로를 한 번 더 유지한다.

- `execution_failure` + default policy + threshold 이상 또는 `retry_exhausted`
  - decision: `reroute`
  - reroute_target: `task_retry`
  - 의미: 구현 경로 재시도로 올린다.

- `execution_failure` + aggressive policy
  - decision: `reroute`
  - reroute_target: `task_retry`
  - 의미: threshold를 기다리지 않고 빠르게 재시도 경로로 보낸다.

- `quality_failure` + default policy
  - decision: `reroute`
  - reroute_target: `plan_retry`
  - 의미: 구현 보정보다 계획 재정렬이 먼저다.

- `quality_failure` + conservative policy
  - decision: `hold`
  - reroute_target: 없음
  - 의미: 자동 우회보다 명시적 재설계를 우선한다.

- `contract_failure`
  - decision: `hold`
  - reroute_target: 없음
  - 의미: 사용자의 명시적 확인 없이는 진행하지 않는다.

- `orchestration_failure`
  - decision: 기본 `hold`, 필요한 경우만 `plan_retry`
  - reroute_target: 상황별
  - 의미: 잘못된 시작 스킬, 잘못된 reroute, metadata 부족은 엔진 자동 보정보다 경로 재설계를 우선한다.

이 표를 먼저 고정한 뒤 runtime이 이를 소비해야 한다.
반대로 runtime 분기부터 늘리면 failure path마다 예외 규칙이 다시 생긴다.

failure path 일반화에서 최소 orchestration failure shape와 single-run telemetry가 안정된 뒤 multi-run KPI summary를 붙였다.
이제 KPI는 정책 비교와 next-action 품질 개선 근거로 연결돼야 한다.

여기까지 가면 OMC는 `규칙 기반 스킬 오케스트레이터`에서 `초기 자동 모델 전환 엔진`으로 넘어가기 시작한다.

## 하지 말아야 할 것

- learned orchestrator를 너무 일찍 도입하기
- 로그 없이 정책만 계속 늘리기
- 모델 선택을 완전히 블랙박스로 만들기
- step 메타데이터 없이 예외 규칙만 쌓기

## 한 줄 결론

OMC가 완전 자동 모델 전환 제품으로 가려면,
`task_kind 기반 규칙 라우팅`을
`step 메타데이터 -> 실패 기반 승격 -> 실행 telemetry -> 데이터 기반 튜닝`
구조로 확장해야 한다.
