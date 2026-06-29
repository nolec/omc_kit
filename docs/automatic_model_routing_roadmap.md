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
| V2. Step-level Routing | 완료 | `autopilot` step metadata schema와 normalization이 있고, `complexity/risk/preferred_profile/sensitive_paths`가 실제 profile 선택에 반영된다. | 선택 근거 기록 + V3 승격 규칙 연결 |
| V3. Failure-driven Escalation | 진행중 | V3-1 수준의 failure class 분리와 persistence/report 반영은 들어갔고, `retry_exhausted`와 `failed_critique_loop` 경로의 `escalation_policy` decision persistence 연결 및 critique 경로 runtime consumption이 완료됐다. 추가로 `task_retry` / `plan_retry` 성공 경로, `timeout` 경로, `failed` 계열 주요 경로의 decision payload shape 일반화와 `orchestration_failure` 1차 decision policy 연결이 반영됐다. 다만 일반화된 `same/escalate/reroute` 엔진과 telemetry 기반 정책 비교는 아직 없다. | telemetry report MVP + decision engine 일반화 |
| V4. Telemetry-driven Tuning | 진행중 | step state에 `token_usage`, `cost_estimate` 저장은 들어갔고, `benchmark-report`에 `had_reroute`, `recovered_after_retry`, `total_cost_usd`, `total_tokens` 같은 single-run telemetry가 반영됐다. 추가로 `.omc/runs/` 기준 `reroute_rate`, `retry_to_success_rate`, `cost_per_successful_task` multi-run KPI summary와 current-path 중복 제거까지 반영됐다. 다만 정책 비교 리포트 자동화는 아직 없다. | policy 비교 자동화 + telemetry report 정리 |
| V5. Learned Orchestrator | 미착수 | 데이터 축적 게이트 전 단계다. | 연구용 feature 정의 |
| Operator Experience | 진행중 | `plan/task/review` 진입점과 추천은 있으나, 실제 사용감은 아직 “더 똑똑한 흐름 제어”까지는 아니다. 다만 `omc-plan`, `omc-review`, `omc-task` 출력 contract에 `decision / risk / next_action` 의미를 각 스킬 문맥으로 고정하는 1차 보강, `2-1 next_action 공통화`, `2-2 reroute / delay UX`와 `role_suggest` 시작 스킬 오판 패턴 보강까지 반영됐다. | next-action 품질 + 병목 우선 추천 |

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

- step별 선택 근거 기록
- `escalation_policy`를 V3 승격 엔진과 연결

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

현재 상태는 `V3-2 주요 failure path 일반화 완료 + V4 multi-run KPI summary 1차 완료`에 가깝다.

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
  - `decision_policy_entry` helper 추출로 failure-class별 decision 규칙을 공통 엔진으로 옮길 준비를 마쳤다.
  - critique/review failure step runtime 소비가 `_failure_step_decision` helper를 통해 공통 decision 엔진을 직접 사용하도록 정리됐다.
  - `task_retry`, `plan_retry` 실패 payload도 `_retry_step_payload` helper로 정리돼 retry runtime decision 하드코딩이 제거됐다.
  - `ambiguous_response`, `branch_setup_failed`도 `orchestration_failure`로 승격돼 persisted decision이 explicit hold로 수렴하도록 정리됐다.
- V3-2에서 남은 것:
  - `orchestration_failure` runtime 소비 경로 전반이 `decision_policy_entry`를 직접 사용하도록 확장
  - 일반화된 `same / escalate / reroute` 엔진 정리

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

남은 2차 변화:

- `omc-plan`, `omc-review`, `omc-task` 1차 계약 보강은 끝났다.
- `2-1 next_action 공통화`도 끝났다.
- `2-2 reroute / delay UX`도 1차 완료됐다.
- next-action 품질 보강 3차의 plan/review/status 병목 우선 추천은 반영 완료다.
- 다음 남은 조각은 fixture/benchmark에도 이 우선순위를 더 직접 반영해 실제 기대 추천과의 오차를 줄이는 것이다.

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

이번 분기 목표는 V5가 아니라 V2 완료 + V3-1 완료까지다.
원래 분기 목표선은 V3-1 완료까지였지만, 현재 구현은 V3-2 일부까지 선행 진입한 상태다.

이번 분기 핵심 목표는 아래 3개다.

- step metadata 확장
- retry escalation MVP
- telemetry 최소 저장

`Plan / Task / Review experience 고도화`는 이번 분기에도 병행할 수 있지만,
핵심 엔진 로드맵을 흐리지 않도록 `병행 UX 트랙`으로 취급한다.

즉, 분기 운영 기준은 아래처럼 본다.

- 핵심 엔진 트랙: step metadata / escalation / telemetry
- 병행 UX 트랙: 시작 스킬 정확도 / next-action 품질 / reroute UX

## 바로 다음 작업 계획

현재 기준 다음 구현 순서는 아래가 가장 효율적이다.

1. decision engine 일반화 2차
   이번에 `decision_policy_entry` helper까지 추출해 공통 엔진의 시작점을 만들었다.
   다음은 `orchestration_failure` runtime 소비 경로와 나머지 failure class를 이 helper 중심으로 정리해 예외 분기를 줄이는 것이다.

2. next-action 품질 보강 3차
   `2-2 reroute / delay UX`, 시작 스킬 정확도 보강, `omc-status`, `omc-plan`, `omc-review`의 병목 우선 추천은 반영됐다.
   다음은 fixture/benchmark에도 같은 우선순위를 더 직접 반영해, 같은 상태에서도 사용자 의도 차이를 더 잘 구분하는 것이다.

3. autopilot 작업 단위 정리
   로드맵 전체 자동 실행이 아니라, spec이 고정된 한 태스크씩만 autopilot에 넘기도록 task file 기준을 문서화한다.
   목표는 “큰 로드맵은 사람 계획, 작은 실행 단위는 autopilot” 경계를 명확히 하는 것이다.

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
