# Automatic Model Routing Roadmap

## Current Roadmap

OMC의 제품 목표는 사용자가 모델·executor·작업 단계를 직접 조합하지 않아도 요청의 난이도·위험·실패 신호에 따라 안전한 실행 경로를 선택하는 도구 중립 오케스트레이터다. 현재 판단과 다음 작업은 이 문서를 기준으로 하며, 완료 이력과 과거 실험 원문은 [Roadmap History](automatic_model_routing_roadmap_history.md)에 보존한다.

## Current Evidence

- V1–V4 routing, TDD guard, bounded scheduler와 fail-closed receipt 검증은 구현·회귀 근거가 있다. 이는 기능 존재의 근거이며, 모든 제품 효과 또는 자동 실행의 근거는 아니다.
- task-review pilot v2는 roster와 T0는 보존됐지만 readiness·paired arm 실행·terminal·decision receipt가 없어 `ARCHIVED_INCOMPLETE`로 동결했다. reconciliation과 감사 외에 재사용하지 않는다.
- `task-review-persona-effectiveness-20260904-v1`은 연구 계약 `APPROVED`, 실행 `PAUSED_NOT_CANCELLED`, evidence `NOT_STARTED` 상태다. 10건 paired case와 external Codex executor receipt 계약은 보존하지만 별도 사용자 결정 전에는 시작하지 않는다.
- WeeklyKPI bounded local dashboard는 `LOCAL_DISCOVERY_PROTOTYPE`이며 `USER_ACCEPTANCE_PENDING`이다. 데이터 일치·계약 테스트·빌드·desktop/mobile QA는 로컬에서 확인했지만 durable product evidence, 반복 재사용, 수정 지시 감소와 OMC의 우위는 `NOT_YET_PROVEN`이다.
- Codex-only V0 `$omc-dashboard`는 JSON/CSV와 기술 검증에 더해 question evidence contract를 fail-closed gate로 고정했다. 봉인본 `repo-ops-20260908-v4`의 기술 gate는 통과했지만 첫 forward case 판정은 `REVISION_REQUIRED`이며 reason은 `UNSUPPORTED_PRIORITY_INFERENCE`다. repository health와 dirty repository inventory는 유용했지만 source에 없는 business priority를 `오늘 먼저 볼 곳`으로 제시한 근거 없는 priority ranking은 거부했다. 스킬은 `SKILL_IMPLEMENTED_NOT_FORWARD_VALIDATED`를 유지하며 두 번째 독립 forward case는 `NOT_STARTED`다. Claude Artifact와 동등하다고 주장하지 않고 독립 case 수용 전에는 `WORKFLOW_REPEATABILITY_OBSERVED`로 승격하지 않는다.
- `claude-code-omc-incremental-value-20260908-v3`는 `raw_claude_code`와 `claude_code_with_omc`의 active Stage F 실행 정적 계약만 구현한 `STAGE_F_EXECUTION_CONTRACT_IMPLEMENTED_NOT_STARTED` 상태다. prospective T0→T1 population, 2개 repository별 feature 2·bugfix 2·refactor 1 quota, fatal 독립 envelope, authorization subject와 terminal 우선순위를 고정했지만 terminal 코드와 실제 실행은 없다. claim은 `NO_SUPERIORITY_CLAIM`이며 실행되지 않은 `claude-code-omc-incremental-value-20260908-v1`과 `claude-code-omc-incremental-value-20260908-v2`를 SHA-256 predecessor chain으로 보존한다. 30쌍 Stage D와 powered Stage C는 evidence를 공유하지 않는 별도 승인·registration 경계를 유지한다.
- Codex plan/task/review 출력 가독성 계약은 단계별 순서·줄 수·중복·단일 다음 행동을 실제 autopilot consumer 경로에서 fail-close하도록 구현했다. 내부 routing 상태는 machine footer에 유지하되 화면에는 사용자가 결정할 실제 대상을 요청한다. ready/blocked 6개 native Codex 표본은 독립 ephemeral session의 prompt·raw JSONL·output과 SHA-256을 함께 보존해 재검증 가능하다. 이는 출력 계약의 구현·forward evidence이며 실제 작업의 수정 지시·지연·개입 감소를 증명하지 않는다.
- Completion Observation V0 projection은 기존 sealed terminal·completion lineage를 유일한 작업 원장으로 사용한다. frozen registration digest와 저장소 키와 분리된 승인 executor trust anchor를 검증하고, population closure와 report 양쪽에서 등록된 `.omc/state/sessions`를 전수 재스캔한다. `(captured_at, repo_id, work_id)` 결정 순서의 첫 10건·최소 2개 저장소를 만족할 때만 판정한다. 별도 Codex-first live 계층은 opt-in 저장소에서 최초 미커밋 완료와 이후 사용자 반응을 `work_id`·baseline·executor surface에 결속해 무복사 수집하며, Claude 입력 혼입과 UTF-8 원문 손실을 차단한다. 두 경로 모두 실제 적격 표본은 아직 `0건`이고 승인 executor receipt를 포함한 projection의 전체 production evidence 경로는 시작하지 않았으므로 상태는 `CAPTURE_CODE_COMPLETE_PROSPECTIVE_NOT_STARTED`다. 따라서 코드·회귀 통과는 수정 지시 30% 감소나 제품 효과를 뜻하지 않는다.
- 절대 수용성 Pilot v3는 Codex CLI sidecar·population/case collector·4-way evaluator와 fail-closed 회귀 검증을 구현했다. v2는 관찰 시작 전 종료하며 표본을 승계하지 않는다. v3는 `draft_unregistered`, 실제 표본 `0/10`으로, 다음 단계는 아래 Real-use Product Observation의 등록·custody·실제 smoke 준비다. 구현 완료는 실행 승인이나 제품 효과 증명이 아니다.
- 완료 이력·중단된 실험·세부 설계는 [Roadmap History](automatic_model_routing_roadmap_history.md)에 보존한다. 과거 receipt나 테스트 통과를 현재 운영 효과로 승격하지 않는다.

## Active Decision Gate

현재 단일 활성 제품 lane은 `COMPLETION_OBSERVATION_CAPTURE_FEASIBILITY`다. 이는 실제 관찰을 이미 시작했다는 뜻이 아니라, 다른 연구·Persona Pilot·대시보드 확장을 보류하고 single-host 다중 저장소 capture 준비만 우선한다는 뜻이다. 실제 관찰은 공통 roster와 T0를 먼저 고정한 뒤 각 저장소를 등록하고 최신 설치를 검증해야 시작된다.

아래 decision table은 실행 지시가 아니라 보류된 Persona contract revision 7의 판정 계약이다. 공동 서명 registration은 machine-readable preregistration의 hash와 contract revision, 30% 최소 감소율, baseline correction event 최소 3건을 직접 포함한다. 재개가 별도로 승인되면 case 1 전에 calibration qualification과 four-authority rehearsal, fresh T0와 21일 창, chronological first eligible implementation 10건·무대체, 최소 2개 저장소·저장소당 최대 7건, arm mapping과 고유한 네 authority key를 동결한다. 공식 claim은 blind correction-required case 30% 이상 감소이며 completion·verification·총 사람 개입·blind requirement·persona·DoD 품질·incorrect completion·median wall-clock 비열화를 함께 판정한다. 이 gate와 case별 enrollment 검증 전에 수행된 실행은 유효한 Pilot evidence로 인정하지 않는다. 서명·hash·binding·raw output 증거가 누락·위조·불일치하면 운영 명령은 `blocked`로 차단한다.

| Decision condition | Outcome |
|---|---|
| `fatal_violation` | `STOP` |
| `provider_execution_absent` | `INCONCLUSIVE` |
| `blinding_failed` | `INCONCLUSIVE` |
| `completion_noninferiority_failed` | `STOP` |
| `verification_noninferiority_failed` | `STOP` |
| `total_intervention_noninferiority_failed` | `STOP` |
| `wall_clock_noninferiority_failed` | `STOP` |
| `blind_quality_noninferiority_failed` | `STOP` |
| `insufficient_baseline_correction_events` | `INCONCLUSIVE` |
| `correction_reduction_target_missed` | `REDUCE` |
| `all_gates_passed` | `CONTINUE` |

## Target Architecture — Approved Study, Execution Paused

**Persona Pilot contract revision 7**의 연구 범위와 판정 계약은 `APPROVED`, 실행은 `PAUSED_NOT_CANCELLED`, evidence는 `NOT_STARTED`다. 목표는 여러 레포를 운영하는 SaaS 창업자가 새 제품 기능을 요구사항·검증·리뷰 기준까지 완료하도록, OMC가 persona·DoD·검증 계약을 정하고 Codex가 실행한 뒤 OMC가 선언된 검증과 종료 판정을 제공하는 것이다. Codex adapter는 아직 구현되지 않았고 이번 범위에서 개발하지 않는다. external Codex executor 수동 receipt 프로토콜을 사용하며, machine-readable SSOT는 [persona-effectiveness preregistration](task_review_persona_effectiveness_preregistration_v1.json)이다.

### 현재 위치

| 트랙 | 상태 | 현재 근거 | 남은 핵심 |
|---|---|---|---|
| V1 Skill-based Routing | 완료 | task kind와 skill 기반 profile 선택 | 운영값 유지 |
| V2 Step-level Routing | 완료 | step metadata가 실제 profile 선택에 반영 | 운영 surface 미세조정 |
| V3 Failure-driven Escalation | 완료 | failure class·retry·reroute·hold decision 통합 | multi-run tuning |
| V4 Telemetry-driven Tuning | 완료 | token·cost·retry·reroute·readiness KPI 수집 | 운영 drift 감시 |
| V5 Learned Orchestrator | 부분 반영 | single child, exact 2-child, v2 grant 전용 bounded N-child scheduler·provider adapter, authoritative acceptance harness | 실제 3–5 child 운영 표본 acceptance |
| Operator Experience | 진행중 | output contract, Lite/Full routing, Stage graph SSOT, resume identity fail-close, CLI fast-path 구축 | 지연·개입 횟수 운영 검증 |

현재 OMC는 운영 가능한 규칙 기반 코어이며, 고급 오케스트레이션의 제품 가치는 미검증 상태다. task-review pilot v2에서 구현한 roster·repository identity·T0 decision·inventory dry-run·signed execution·terminal receipt 검증 코드는 유지하지만, v2 study 자체는 증거 미완료로 동결했다. 신규 study는 검증 코드만 재사용하고 v2 identity와 evidence는 재사용하지 않는다.

### 현재 제품 결정 gate

`$omc-dashboard`의 첫 forward case `repo-ops-20260908-v4`는 기술 gate와 별개로 `REVISION_REQUIRED` 판정을 받았으며 제품 효과는 `NOT_YET_PROVEN`이다. 근거 없는 priority ranking을 제거하는 question evidence contract 구현 뒤에도 `NO_ACTIVE_EXECUTION_LANE`을 유지한다. Persona study는 재개가 별도로 승인될 때에만 자연 발생 implementation 10건을 `direct_codex`와 `omc_persona` arm으로 paired 실행한다. 같은 request·base commit·provider·model·reasoning·`timeout_sec`·verification command와 balanced order, anonymous evaluation packet, blind adjudication v4 계약은 변경하지 않는다.

실행 절차 SSOT는 [Task Review Product Focus Pilot](task_review_product_focus_pilot.md), machine-readable 판정 SSOT는 [persona-effectiveness preregistration](task_review_persona_effectiveness_preregistration_v1.json)이다. 실제 실행 전에 fresh authority와 T0를 동결하고 operator custody의 trusted execution public key를 설정해야 한다. 결과가 `CONTINUE`여도 원본 저장소에 자동 반영하지 않고 사용자가 선택한 arm만 별도 작업에서 적용한다.

- `CONTINUE`: baseline correction event가 최소 3건 있고, OMC persona arm의 blind correction-required case rate가 30% 이상 감소하며 completion·verification pass rate·총 사람 개입 수·blind requirement/persona/DoD 품질이 비열화가 아니고 incorrect completion이 증가하지 않으며 median wall-clock이 baseline의 115% 이내이고 fatal violation이 없다.
- `INCONCLUSIVE`: fatal violation이 없는 유효한 terminal에서 provider 실행 부재가 확인되거나, 강제 arm 추측에서 OMC arm을 9건 이상 맞혀 blinding이 실패하거나, 앞선 비열화 gate를 모두 통과한 유효한 10쌍에서 baseline correction event가 3건 미만이거나, 21일 deadline에 10건 미달임을 유효한 signed collection-close receipt로 봉인한 경우다. receipt·서명·hash·binding·raw output·blind adjudication 증거의 누락·위조·불일치는 결과로 승격하지 않고 `blocked`로 차단한다.
- `REDUCE/STOP`: 30% 개선에 못 미치면 범위를 축소하고, completion·verification 악화 또는 fatal violation이 있으면 정지한다.
- 완성 표본의 판정 우선순위는 `fatal violation → provider 실행 부재 → blinding 실패 → completion → verification → 총 사람 개입 → median wall-clock → blind quality → baseline correction event 수 → 30% 감소율`이다. 따라서 provider 실행 부재와 blinding 실패는 `INCONCLUSIVE`지만 fatal violation은 항상 `STOP`이며, baseline event가 부족해도 앞선 비열화가 확인되면 `STOP`이다.
- 모든 연구 lane은 `PAUSED_NOT_CANCELLED`이며 현재 prototype 사용자 판정 후에도 별도 사용자 결정 없이는 재개하지 않는다.

### Evidence-state Scorecard

완성도 백분율 대신 코드, 회귀 테스트, 운영 표본, 독립 재현의 증거 단계를 분리한다. Product Value의 claim scope는 `bounded_n_child_execution`이며 다른 스킬이나 전체 OMC의 대체 판정으로 확대하지 않는다.

| 대상 | evidence-state | 현재 근거 | 승격 조건 |
|---|---|---|---|
| Routing V1–V4 | `OPERATIONALLY_VALIDATED` | 라우팅·실패 복구·telemetry 코드와 운영 receipt | 운영 drift 감시 유지 |
| Bounded scheduler | `IMPLEMENTED` | v2 grant 전용 N-child scheduler·provider adapter·회귀 테스트 | 실제 3–5 child acceptance |
| Product Value | `BLOCKED` | 기존 evidence-loss batch는 종료했으며 prospective study는 `PAUSED_NOT_CANCELLED` | 별도 사용자 결정으로 재개 |
| Product focus v2 | `ARCHIVED_INCOMPLETE` | roster·T0는 보존, readiness·provider·terminal·decision evidence는 없음 | reconciliation과 감사만 허용; binding·case 재사용 금지 |
| Persona effectiveness | `APPROVED` / `PAUSED_NOT_CANCELLED` / `NOT_STARTED` | 10건 paired case·blind correction-required 30% 감소·비열화·calibration·external receipt 계약 사전 등록 | 별도 사용자 재개 결정 뒤 calibration qualification과 fresh authority·T0 동결 |
| Validated Deliverable discovery | `LOCAL_DISCOVERY_PROTOTYPE` / `USER_ACCEPTANCE_PENDING` | WeeklyKPI 데이터 일치·5개 contract test·build·desktop/mobile local QA | 사용자 수용 후 실제 작업 2건 추가 재사용 여부를 별도 계획; 그 전 제품 효과는 `NOT_YET_PROVEN` |
| Dashboard V0 | `SKILL_IMPLEMENTED_NOT_FORWARD_VALIDATED` / `REVISION_REQUIRED` | `repo-ops-20260908-v4`의 health·dirty inventory는 유용했지만 `오늘 먼저 볼 곳`은 `UNSUPPORTED_PRIORITY_INFERENCE`; question evidence contract 구현 | 별도 승인된 OMC 핵심 review 데이터로 두 번째 독립 case를 시작하기 전 질문 결속 동결; 그 전 `WORKFLOW_REPEATABILITY_OBSERVED` 금지 |
| Claude incremental value | `STAGE_F_EXECUTION_CONTRACT_IMPLEMENTED_NOT_STARTED` / `NO_SUPERIORITY_CLAIM` | v1·v2 보존, v3 prospective selection·authorization·fatal·terminal 정적 계약 | 다음 원자 작업에서 v3 Stage F terminal adapter 구현; 실제 실행은 계속 금지 |
| Product Value independence | `NOT_REPRODUCED` | 유효한 development evidence 없음 | 신규 development evidence 검증 후 별도 선정한 holdout에서 primary metric 충족 |
| Plan | `NOT_PROVEN` | 단일 저장소 pilot만 존재하며 현재 `PAUSED_NOT_CANCELLED` | 별도 사용자 결정으로 재개 여부 결정 |
| Review | `NOT_PROVEN` | durable native provider 원문 부재이며 현재 `PAUSED_NOT_CANCELLED` | 별도 사용자 결정으로 재개 여부 결정 |
| Autopilot | `LIMITED` | mission packet 동결, 명시적 `mission_accept` receipt, work contract v2 결속, provider 호출 전 mission briefing 재검증, 격리 candidate 실행과 trusted-base review까지 fail-close | 고정 커밋 기준 외부 provider TASK→REVIEW smoke와 운영 latency·개입 acceptance |
| Decision Policy | `IMPLEMENTED` | feasibility 계약과 회귀 테스트는 구현됐으며 현재 `PAUSED_NOT_CANCELLED` | 별도 사용자 결정으로 재개 여부 결정 |
| Setup | `RUNTIME_CHECK_REQUIRED` | 최신 배포 상태는 strict install audit의 machine-readable 결과를 SSOT로 사용한다. 권위 확인 surface는 `python3 scripts/omc_install_audit.py --strict --json <consumer-path> [...]`이며, 과거 rollout의 버전·대상 수·검증 결과는 Setup Distribution Integrity와 roadmap history에 관찰 이력으로만 보존 | audit 대상 inventory를 확정한 뒤 runtime 결과 확인; source freshness와 rollback 회귀 유지 |

과거 6건 corpus에서 `DEVELOPMENT_PASS`가 기록됐지만 manifest·workload inventory·execution packet 원문을 현재 검증할 수 없어 유효한 development evidence로 승계하지 않는다. 구현과 acceptance 계약은 유지하되, 사용자가 별도로 재개를 결정한 경우에만 새 prospective development study에서 chronological first-N 6건을 다시 확보한다. 그 증거가 검증된 뒤에만 비중복 disjoint holdout을 별도로 계획한다. post-call token은 비교 지표로만 사용하며 strict hard-budget 증거로 취급하지 않는다. Product Value 결과는 Plan, Review 또는 전체 OMC 판정을 변경하지 않는다.

Autopilot Phase A mission gate는 구현·회귀 검증을 완료했다. 사용자 요청과 base commit을 mission packet으로 동결하고 exact `mission_accept` receipt를 work contract v2에 결속하며, safe runner는 provider 호출 전에 packet·approval·session·request·base를 재검증해 mission briefing을 주입한다. receipt는 완전 쓰기와 `fsync` 후 no-replace 방식으로 게시하고, 부분 쓰기 또는 상태 저장 사이 실패는 동일 receipt 재시도로만 복구한다. 관련 회귀 `194 passed`, staged TDD gate, diff check와 OMC review `APPROVE`를 확인했다. 이는 실행 전 목적 결속의 구현 근거이며 외부 provider 실사용 smoke나 운영 acceptance를 대신하지 않는다.

Work Packet prospective feasibility는 **capture-only schema v2 검증 코드 완료 / 실제 수집 0/5** 상태다. 5건 chronological first-N capture는 observation 시작 15분 전에 완료된 RFC 3161 registration, Git registry anchor, 서로 다른 registration·source snapshot·completion collector·executor 키와 custody identity, preregistration에 고정된 source inventory path, registry commit의 후손인 canonical inventory commit, 연속 sequence·entry hash·source snapshot checkpoint chain, source snapshot과 completion ledger의 exact equality, raw request·provider output의 execution receipt 결속을 모두 통과해야 한다. 실패·불확정 study는 서명된 failure receipt로 봉인하며 자동 재시작하지 않고, 새 study는 승인된 restart parent를 명시해야 한다. 완료 artifact는 임시 경로가 아닌 durable evidence root에 원자적으로 게시하고 digest 검증 후 reload한다. 이 계약은 독립적인 작은 운영 표본의 **수집 가능성만** 검증하며 실제 5건이 수집되기 전에는 제품 가치, 품질 projection 또는 Plan 대체 증거로 사용하지 않는다.

Decision Policy prospective feasibility는 **증거·실행 계약 구현 완료 / 실제 적격 표본 0/5** 상태다. failure corpus는 실제 result JSON과 별도 Ed25519 causal-review receipt를 동일 run·request·commit·source tree에 결속하고, 외부 trust root와 chronology가 일치하는 경우만 적격으로 인정한다. 정책 packet은 decision priority·tradeoff·evidence boundary·stop condition과 독립 승인 receipt를 동결하며, baseline/policy 두 arm은 같은 request·base·runner·adapter·tool contract subject를 사용해야 한다. 실행 receipt v2는 provider·model·reasoning·paired timeout·critical omission·서명 시각을 결속하고, 동결된 balanced order의 엄격한 시간 순서와 각 case 실행 후 blind adjudication을 검증한다. 누락·위조·순서 불일치는 `INCONCLUSIVE`, 유효한 품질 기준 실패는 `FEASIBILITY_FAIL`로 분리한다. artifact는 root-relative descriptor와 descriptor 기반 `openat`·`O_NOFOLLOW` 단일 읽기로 경로 이탈, 중간 symlink, digest 교체를 fail-close한다. 이는 persona가 검증 루프에 머물지 않고 명시적 종료 정책으로 작업을 완결하는지 측정하기 위한 준비 근거일 뿐이며, 실제 5건과 paired 결과 전에는 제품 효과 또는 Codex Plan 대체 근거로 사용하지 않는다.

### 제품 약점 기반 개선 축

기능 수가 아니라 사용자가 실제 작업을 더 잘 끝내는지를 기준으로 남은 약점을 세 핵심 축과 하나의 지원 축으로 관리한다. Product Value·Operator Experience·Evidence를 핵심 축으로, Maintainability를 이를 지속시키는 지원 축으로 둔다. 구현 완료와 제품 효과 검증을 분리하며, 새 스킬·정책·benchmark fixture 수 증가는 완료 지표로 사용하지 않는다.

| 개선 축 | 우선순위 | 현재 약점 | 다음 범위 | 종료 기준 |
|---|---|---|---|---|
| Product Value | P0 | bounded scheduler는 완성됐지만 실제 다중 child 가치가 미검증 | 실제 3–5 child 운영 acceptance와 single-agent baseline 비교 | 중복 실행·scope·budget 위반 없이 완료하며, baseline 대비 성공률은 같거나 높고 시간·token·개입 횟수는 사전 등록된 개선 기준을 충족 |
| Operator Experience | P1 | 반복 승인·상태 확인·스킬 왕복이 작은 작업의 준비 시간을 키움 | Lite/Full observed 표본에서 단계별 latency·retry·개입 측정 후 안전한 자동 분기 조정 | 품질 gate를 유지하면서 p50/p95·token·사용자 개입 횟수 감소 |
| Evidence | P1 | Plan·Review 품질 우위와 비용 절감이 독립 운영 증거로 확정되지 않음 | single-agent baseline 대비 성공률·시간·token·개입 횟수, durable raw output, blind adjudication 수집 | 사전 등록된 독립 배치의 acceptance를 통과한 지표만 대체·우월 판정에 사용 |
| Maintainability | P2 | public/research CLI 경계와 setup 배포 SSOT는 정리됐지만 상태 수명주기와 검증 도구 규모가 여전히 사용자 신뢰를 저해. 운영 파일 기반 거짓 source drift는 격리 consumer에서 해시·version·strict audit로 교정 확인 | stale session 교정, 멀티 호스트 동일 fixture 검증, 최신 source의 consumer 재배포 | README·CLI·로드맵 상태가 일치하고 일반 사용 경로가 setup·task·autopilot·status·ship 중심으로 동작하며 완료 상태와 freshness가 실제 Git·run 상태와 일치 |

Operator Experience의 반복 커밋 확인 병목은 `2026-08-31`에 코드 계약을 닫았다. 리뷰에서 이미 제시한 동일 범위 local commit 선택은 현재 confirmed session·TTL·선택 path·blob과 정확한 staged tree에 결속된 authorization으로 pre-commit에서 한 번만 검증하고, 실제 commit tree가 일치할 때 post-commit에서 receipt를 한 번 소비한다. 범위·내용·세션·만료가 달라지면 fail-close하며, 실패한 재검증은 이전 authorization을 즉시 폐기해 `--no-verify` 뒤 만료 receipt가 소비되는 경로도 막는다. push·PR·deploy 권한은 상속하지 않는다. 실제 post-commit hook 통합 테스트를 포함한 관련 회귀 `178 passed`, 문법 검사, staged TDD gate와 OMC review `APPROVE`를 확인했다. 이는 반복 확인 한 종류를 제거한 구현 완료 근거이며, Lite/Full 운영 표본의 p50/p95·token·전체 사용자 개입 감소를 증명한 것은 아니다.

운영 증거 없는 자동화 확대 금지를 공통 원칙으로 둔다. 실제 병목을 줄이지 않는 새 추상화, 정책, 스킬 추가는 위 종료 기준보다 우선하지 않는다.

### Real-use Product Observation

제품 가설은 `bounded N-child` 자체가 아니라 OMC가 실제 개발 작업을 요구사항·검증·종료 기준에 맞게 완성하는지다. 현재 활성화 후보는 Codex CLI 자연 implementation 작업의 절대 수용성만 묻는 v3이며, 기존 workflow 대비 개선·우월성 delta는 주장하지 않는다.

- Lightweight V0: `scripts/omc_completion_observation.py`는 기존 sealed terminal·completion·lineage 원문과 raw request/output을 candidate에 보존한다. raw digest는 저장소·collector 키와 분리된 승인 executor trust anchor의 execution receipt에 결속한다. 6종 correction reconciliation과 population closure는 collector가 서명하며, closure 생성과 report 판정은 등록 root hash를 확인한 뒤 실제 `.omc/state/sessions` 전체를 각각 재스캔한다. implementation-only, `(captured_at, repo_id, work_id)` 순서의 first eligible 10건, 최소 2개 저장소 조건에서 누락·cherry-pick·중복·순서·repo coverage·위조가 있으면 `CAPTURE_INCOMPLETE`다. claim은 `CAPTURE_FEASIBILITY_ONLY`로 고정하고 이 V0를 제품 효과나 v3 acceptability 판정의 대체 근거로 사용하지 않는다.
- Codex-first 무복사 수집 계층: `live-register`가 저장소 밖 write-once receipt에 공통 study ID·미래 T0·14일/T1+24h 경계·저장소 roster·root identity를 먼저 봉인하고, 각 저장소의 `live-enable`은 그 exact registration digest를 결속한다. `$omc-task`는 implementation start를 저장소 로컬 write-once 원장에 모두 기록하며 로컬 5건 allocator는 제거했다. T0+14일 이후 24시간 안에 `live-close`가 registration 원문과 등록 저장소 전부를 재검증하고 `(started_at, repo_id, work_id)` 전역 순서의 첫 5건을 무대체로 고른 뒤, 저장소 밖 경로에 closure bundle을 원자적으로 한 번만 게시한다. roster·policy·record·raw digest 손상이나 계측 실패는 `CAPTURE_FAILED`, 완전한 모집단의 5건/2저장소 미달은 `LOW_NATURAL_DEMAND`, 선택 건의 완료·outcome 누락은 `INCONCLUSIVE`, 5/5가 온전하면 `CAPTURE_FEASIBLE`이다. 설치 hook은 외부 registry·quarantine 경로와 exact work identity가 모두 있을 때만 cross-session prompt를 자동 결속하고, 식별자가 있지만 대상이 없거나 모호할 때만 원문을 quarantine해 target resolution을 요구한다. 일반 입력과 정상적인 비대상 상태는 실패나 quarantine으로 기록하지 않는다. 활성화 시 strict install identity를 결속하고 Claude 입력을 Codex 표본에 섞지 않으며 UTF-8 원문과 끝 개행까지 그대로 보존한다. 최초 완료는 미커밋 `commit_bound=false` snapshot이며 signed completion receipt를 대체하지 않는다. 진짜 관찰 명령 실패만 저장소 로컬 failure receipt로 남기며 제품 작업은 계속하고 표본은 `OBSERVATION_INVALID`로 처리한다. 이 5건은 수집 가능성만 판정하고 수정 지시 30% 감소는 별도 최소 20건 paired observation 전에는 주장하지 않는다. 로컬 구현·회귀 검증·코드 리뷰는 완료했으며 대상 저장소 배포·공통 등록·실제 T0는 아직 시작하지 않았다.

- v1 종료: canonical v1 SHA-256 `16aa508b5ec85f302e6d157895bf5e31bfb5d7007f9aaba3c449a4cbff45bc2c`는 변경하지 않는다. 별도 write-once `docs/real_use_product_observation_v1_supersession.json` SHA-256 `ec9bc00685affc857533c2b50bd22e8d11a6e1993f2fe07083510b2fc959bc5d`가 관찰 시작 전 `superseded_before_observation`, 실제 candidate `0건`, observation 불가를 기록하며 기존 표본·outcome은 v2로 승계하지 않는다. write-once authority는 roadmap hash binding과 같은 commit에 포함된 immutable Git blob이다.
- v2 종료: canonical v2 SHA-256 `9d0609130881029edb129d12c9046a4e86d47cb510741cf35bf633c115ffed46`는 변경하지 않는다. 별도 `docs/real_use_product_observation_v2_supersession.json` SHA-256 `b9dbc15b7d667cd4594ad295dcc2d8ae4298aa191bdbde52cc1198eaaa188609`이 관찰 시작 전 `superseded_before_observation`, 실제 candidate `0건`, v3 표본 승계 금지를 기록한다.
- v3 상태: `docs/real_use_product_observation_preregistration_v3.json` SHA-256 `af2eea2884dfad16d9ffba2e7837127dd6e17a9fa13d7375f99f6a047ac16e4c`은 `draft_unregistered`, `claim_eligible=false`, `observation_allowed=false`, 실제 표본 `0/10`이다. `scripts/omc_absolute_acceptability_pilot.py`는 저장소 밖 0600 Ed25519 custody key로 실제 `codex exec --json` 성공·실패 원문을 봉인한다. 판정 시 승인 registration digest, 고유 executor·source·evidence·저장소별 start key·경로·repo root를 검증하고 등록 저장소의 start capture·work-class lock·repository-signed executor-start를 직접 재스캔한다. executor 분류는 start 후 60초 안에 기록하고 그 이후에만 execution을 허용하며 execution receipt가 선택된 executor-start digest를 양방향 결속한다. repository-persisted source case의 completion·verification·비어 있지 않은 raw follow-up stream, 저장소 키로 서명된 case closure·verification receipt와 원문 artifact digest, evidence 요약의 exact equality를 요구한다. 완료 case는 `start ≤ executor-start ≤ execution ≤ completion ≤ exact 24h observation ≤ 실제 collector closure ≤ evaluation`을 강제하고 창 이전 closure를 거부한다. 미완료 case는 서명된 실패 execution·verification을 허용하되 completion/window/correction을 null·empty로 강제하고 OMC/non-OMC 귀책을 봉인한다. `executor-start`, `population-close`, `case-close`는 overwrite를 금지하고 case의 동일 내용 재시도만 허용한다. 미래 시각·구간 밖 correction·synthetic provenance 역시 fail-close한다. draft preregistration은 `activate_preregistration`으로 runtime schema에 결정적으로 변환하며 원문 hash를 결속한다. 합성 검증은 실제 표본에 포함하지 않으며 실제 no-op/provider 실행도 아직 수행하지 않았다.
- v3 실행 전 검증: `run-codex-sidecar --registration`은 등록된 실행 키·저장소·work/request·서명된 executor-start 원문·executor 분류·시각을 확인한 뒤에만 provider를 호출한다. 모집단은 세션 디렉터리 전체와 start capture를 대조하며, capture 누락은 수요 부족이 아니라 `OBSERVATION_INCONCLUSIVE`다. 과거 세션도 관찰 구간 밖임을 확인할 유효 start capture가 없으면 제외하지 않고 차단한다.
- v3 표본: exact 14일 동안 자연 발생 `codex_cli_json` implementation start 전체를 inventory에 포함하고 `(started_at, work_id)` 순서 첫 10건을 최소 2개 저장소에서 선택한다. 미완료 start도 제거·교체하지 않는다. 완료 뒤 acceptance 여부와 무관하게 정확히 24시간 raw follow-up을 관찰하고 모호한 correction은 primary로 센다.
- v3 판정: 유효 evidence에서 verified completion `10/10`, primary correction `3/10 이하`, OMC-attributable incomplete `0건`이면 `PRELIMINARY_ACCEPTABLE`; 완전한 evidence에서 기준 미달이면 `NOT_ACCEPTABLE`; receipt·inventory·raw output·분류·24시간 창이 누락되면 `OBSERVATION_INCONCLUSIVE`; 완전한 inventory에서 10건 또는 2개 저장소 미달이면 `LOW_NATURAL_DEMAND`다. 이는 절대 수용성 예비 판정일 뿐 30% 개선이나 Codex·Claude Code 대비 우월성 근거가 아니다.
- v3 최근 보강: collector·evaluator는 artifact를 한 번 읽어 크기·SHA-256을 검증한 동일 바이트를 원문으로 사용한다. 검증 영수증의 session/work/start 결속, 손상된 서명·artifact·비 UTF-8 JSON·잘못된 taxonomy/attribution 타입을 구조화된 실패로 반환한다. stdout·stderr 교체 회귀 테스트와 CLI의 blocked JSON·종료 코드 2·파일 미생성 검증을 포함한다.
- v3 활성화 전 남은 일: source commit·evaluator hash·exact 미래 window·승인 authority key와 대상 저장소 경로를 확정한다. sidecar는 등록정보와 대상 저장소의 유효 start/lock/executor-start가 필요하므로 이 사전 조건을 먼저 준비하고, 별도 승인된 실제 Codex CLI no-op smoke에서 receipt를 발급·재검증한다. 합성 검증·smoke를 자연 implementation 표본으로 합산하지 않으며 실제 관찰 활성화와 표본 수집은 별도로 진행한다. 이 외부 실행은 별도 사용자 승인 전 수행하지 않는다.

### Product Value P0 evidence-loss 종료 완료와 신규 prospective study

- 상태 보고: 전체 완성도 백분율을 사용하지 않는다. 구현·검증 준비·운영 검증·독립 재현 evidence-state를 대상별로 보고한다.
- 기존 종료 상태: `product-value-batch-20260826-v5-r1`과 preregistration `69115b41210a14b42ea9096bf3cea98c8897a2047b5bc0a322e5f7a64c2af8df`는 manifest·workload inventory·execution packet 원문을 복구하지 못해 `BLOCKED` / `evidence_loss`로 종료했다. `2026-09-02`에 승인·서명·durable failure marker 기록을 완료했으며 closure subject SHA-256은 `5b83c246de93c626c0f91b09318f20425e63292b3c39081189150f41a9229ea8`, marker file SHA-256은 `f88f1abbd31f74240f726b4c45b9150842cfcbaa80f67e351b9424f917a92967`이다. schema v1 Git registry blob, closure subject, Ed25519 authority receipt를 결속한 no-replace marker가 acceptance 재개를 차단한다. 기존 `2026-09-05` 최종 판정 기한은 연장하지 않는다.
- 복구 금지: 기존 batch의 manifest·workload inventory·execution packet을 추정하거나 재구성하지 않는다. hash-only registry record와 임시 진단 receipt는 실행·판정 입력으로 승격하지 않는다.
- study 분리: 신규 study는 기존 batch의 retry 또는 continuation이 아니다. 새 evaluation ID·selection policy·source universe·authority commitment·registration lineage를 사용한다.
- 기존 `2026-08-31`∼`2026-09-09` 일정은 observation 전 preregistration·receipt를 확보하지 못했으므로 실행 대상에서 폐기한다.
- 새로운 미래 observation window를 사전 등록하고 T0에 Git registry와 RFC 3161 receipt를 검증한다.
- T0+24시간부터 정확히 7일간 chronological first-N development case 6건을 수집하고 각 case의 source snapshot·inventory·completion evidence를 즉시 봉인한다.
- window 종료 후 schema v5 registration과 durable evidence bundle을 생성·검증하며, development evidence 검증 후에만 별도 holdout 계획을 열 수 있다.
- authority 분리: source snapshot signer·preregistration signer·registration authority·inventory collector는 서로 다른 key·operator·custody identity를 사용하며 key 재사용 또는 provenance 불일치는 fail-close한다.
- 허용 범위: failure receipt, 신규 development preregistration, chronological capture, registration, durable evidence 검증과 이를 막는 최소 결함 수정만 허용한다.
- 금지 범위: development evidence 검증 전 provider 호출과 holdout 실행을 금지하며 신규 schema·transport·benchmark fixture를 추가하지 않는다.
- claim 제한: 신규 study는 development evidence만 생성한다. 결과를 Plan·Review 또는 전체 OMC의 대체·우월 증거로 사용하지 않는다.

### Operational P0

**bounded N-child 실제 acceptance**는 `PAUSED_NOT_CANCELLED`다. 아래 내용은 재개 시 사용할 보존 계약이며 현재 실행 우선순위가 아니다.

**현재 병목**

등록된 Product Value 후보가 요구하는 `provider_enforced` hard-token 계약에는 raw Codex 실행 파일을 직접 사용할 수 없어 계속 `HOLD_TRANSPORT_UNSUPPORTED`다. 다만 API 키 없이 ChatGPT 로그인 상태를 사용하는 별도 `subscription_bounded` adapter를 구현해 elapsed time·output chars·process group을 강제하고 실제 input/output/total token을 호출 후 receipt로 기록할 수 있게 됐다. 이 경로는 hard total-token cap을 주장하지 않으며 strict certification에는 부적격이지만, operational pilot과 운영 acceptance의 비용·지연 표본에는 사용할 수 있다. exact input count와 native output cap이 필요한 strict 증명 경로는 기존 Responses transport/backend 후보와 분리해 유지한다.

운영 대체 판정은 strict hard-token 인증과 독립적으로 종료한다. no-key paired 결과가 사전 등록된 품질·시간·token·개입·안전 기준을 충족하면 운영 판정을 발행하고, strict capability가 없다는 이유만으로 이를 무효화하지 않는다. holdout provenance를 고정하는 schema v6 계약까지 완료했으며, 이 acceptance가 끝날 때까지 새 schema·transport·benchmark fixture 추가를 중단하고 기존 실행·등록·evidence 경로만 사용한다.

고정 커밋 `906cfcc`에서 subscription 진단 pilot을 열기 위한 preflight를 실행했지만 `preregistration_schema_invalid`로 fail-close했다. 현재 Git registry에는 preregistration hash만 가진 schema v1 record만 남아 있고, 해당 hash의 signed manifest·6개 execution packet·RFC 3161 receipt 원문은 저장소·개발 디렉터리·임시 저장소에서 복구되지 않았다. runner 차단 출력은 `/private/tmp/omc-product-value-906cfcc-pilot-preflight.json`에 보존했으며 SHA-256은 `402a8f3d...c630568`이다. 이 파일은 임시 진단 증거일 뿐 durable acceptance artifact가 아니므로 기존 batch의 pilot·confirmatory 실행은 종료하고 새 corpus와 schema v2 durable registration을 생성해야 한다.

이 재발을 막기 위한 durable evidence bundle primitive와 CLI 연결은 완료했다. 절대 경로의 비임시 evidence root만 허용하고 preregistration·registration receipt·execution packet·immutable runner bundle을 SHA-256 index로 결속하며, 완성된 staging 디렉터리를 macOS `RENAME_EXCL` 또는 Linux `RENAME_NOREPLACE`로 한 번에 게시한다. `publish`·`verify` CLI는 단일 검증 시점의 bundle hash와 artifact 개수만 반환하며 mutable artifact 경로를 안전한 handle처럼 노출하지 않는다. holdout acceptance는 manifest·packet·runner 직접 경로와 bundle 입력의 혼합을 거부하고, 검증 직후 process-private read-only snapshot으로 materialize한 파일만 소비한다. bundled registration receipt와 실행 context의 receipt가 다르거나 현재 parent runner 및 manifest가 선언한 5개 execution bundle hash가 frozen runner들과 다르면 provider capability probe 전에 차단한다. 동일 bundle hash는 registration gate·phase receipt·authority execution/adjudication subject·최종 report까지 전파하고 v6 authority packet은 정식 provenance 검증을 통과한 registration gate 없이는 생성하지 않는다. Git clean clone에서 독립 loader가 같은 bundle hash를 복구하는 회귀도 통과했다. 기존·경쟁 batch를 교체하지 않고 게시 후 durability 실패는 완전한 bundle을 보존한 채 `indeterminate`로 닫는다. 새 corpus 6건은 availability preflight에서 source repository identity·HEAD·clean 상태, execution packet binding, dependency lock·verification surface의 committed blob hash, read-only cache와 runtime identity를 provider 호출 전에 fail-close 검증한다. 실제 외부 evidence root와 등록된 holdout bundle은 아직 생성하지 않았으므로 운영 증거 확보로 계산하지 않는다.

**준비 완료 체크포인트**

| 영역 | 완료 근거 |
|---|---|
| Scheduler | scope normalization과 child `approval_id` 고유성 정책은 완료. 승인 전 canonical proposal과 v2 grant 재검증, ready-child claim, 제한 병렬 실행, dependency 해제, scope 격리 patch, DAG·child ledger를 구현했다. |
| 안전·실패 | immutable provider snapshot, hard output/call·elapsed·token budget, idempotency·expiry를 강제한다. timeout·scope violation·부분 실패는 bounded `parent_review`로 수렴하고 patch 적용을 막는다. acceptance 복합 제한 분류 완료. |
| Preregistration | Product Value preregistration 계약 완료. schema v6는 1건 비판정 pilot과 동일 조건 paired 5건, canonical workload·pair 순서·execution packet·environment receipt 해시, immutable runner·arm adapter·scheduler·provider adapter bundle을 frozen manifest에 결속한다. `evidence_tier=holdout`, initial/replication 역할, development 기준·양쪽 workload inventory·selection policy·선행 holdout report 해시와 selection·gold·execution·adjudication authority identity도 manifest digest에 결속한다. validator는 manifest의 inventory·selection hash를 재계산하고 development와 holdout, initial과 replication 사이의 비중복 및 authority 분리를 fail-close한다. 실제 등록 증거 없이는 claim eligible이 아니다. |
| 등록 | Product Value 중립 등록 검증 코드는 `prepare-v2` → `registry-record` → `prepare-receipt` → `validate-registration`과 durable schema v2 record를 지원한다. 다만 기존 batch의 실제 registry commit `8b23f83`은 manifest 원문이 없는 schema v1이며, 문서에 기록된 manifest `69115b41...af8df`·receipt `70d18121...d510` 원문을 복구하지 못했다. 기존 batch는 실행 불가로 판정하고 새 batch를 schema v2로 다시 등록한다. |
| Evidence durability | immutable bundle writer·loader, `publish`·`verify` CLI, holdout acceptance bundle-only gate와 crash-safe no-replace atomic publish를 구현했다. 임시 root·필수 artifact 누락·path escape·digest 변조·직접 입력 혼합·receipt/runner 불일치·중복/경쟁 게시를 fail-close한다. 실행은 검증된 private read-only snapshot만 사용하고 bundle hash를 registration·phase·authority·final report에 결속하며 Git clean clone에서 같은 bundle hash 복구를 검증한다. 실제 외부 durable root에 등록된 holdout bundle을 게시하는 작업은 남아 있다. |
| Corpus·freeze | `product-value-freeze prepare-inputs → prepare → validate`와 availability preflight 구현은 완료됐다. 과거 실제 구현 workload 6건의 corpus v2-r1은 원문 evidence가 소실되어 신규 study 입력으로 재사용할 수 없다. 신규 chronological first-N 6건은 새 source universe·registration lineage 아래 source root·commit·packet·환경 artifact·committed blob 결속을 다시 검증한다. |
| Acceptance | Product Value paired acceptance harness 완료. OMC arm은 승인된 v2 grant·child prompt·dependency·scope·aggregate budget을 사용하고 baseline arm은 동일 provider adapter를 사용한다. v6 runner는 holdout과 development 양쪽의 등록 receipt를 검증하고 provenance 결과를 registration gate에 결속한다. selection·gold·execution·adjudication authority 선언은 Ed25519 공개키 identity와 역할별 signed subject로 검증하며, initial report를 replication에서 소비할 때도 네 서명과 subject를 다시 검증한다. runner 실측 elapsed·token·개입·scope·budget과 raw output을 저장하며 authoritative reload 후 모든 arm 성공일 때만 `run-pilot` → `run-confirmatory` → `prepare-authority-subjects` → 외부 서명 → `record-authority-receipts` → `finalize`한다. receipt 기록은 검증 후 멱등 저장하고 다른 값의 교체를 거부하며, authority 검증에만 lazy crypto dependency를 요구한다. initial holdout 통과는 `HOLDOUT_PROVISIONAL_PASS`만 발행한다. replication은 선행 report의 전체 workload inventory·threshold·authority evidence를 다시 검증하고, initial과 replication 각각에서 token 중앙값 최소 10% 개선과 나머지 primary metric을 모두 충족할 때만 `OPERATIONALLY_REPLACEABLE`을 허용한다. 운영 교체 판정과 strict hard-token 인증은 분리한다. |
| Provider 계약 | Product Value provider enforcement 계약 v2 완료. provider 출력의 profile 자기 주장은 폐기하고 runner가 adapter·backend·capability hash를 직접 결속한다. backend는 승인 hash 확인 후 immutable runtime으로 복사하고 모든 provider subprocess의 `OMC_PROVIDER_BACKEND`를 snapshot 경로로 고정해 검사-실행 간 교체를 차단한다. OpenAI Responses backend 후보는 count endpoint의 exact input count에서 남은 output budget을 계산하고 native `max_output_tokens`로 전달하며 completed usage가 reservation을 넘으면 fail-close한다. boolean-only backend를 외부 실행 전에 거부하고 legacy v2 prepared input 재사용을 fail-close한다. 별도 `subscription_bounded` profile은 ChatGPT 구독 인증과 post-call usage만 허용하며 `provider_enforced`와 혼용하지 않는다. |
| Conformance | Product Value provider conformance 증거 계약 완료. trusted metering receipt만 정산하고 위반·실패는 worst-case `indeterminate`로 봉인한다. over-limit 요청, forged capability·usage, timeout, output overflow를 포함한 adversarial conformance와 disposable shadow execution receipt를 검증하되 실제 실행 전 `claim_eligible=false`다. |
| Transport feasibility | 승인 hash의 arm adapter·scheduler·provider adapter·provider backend immutable snapshot만 고정 명령으로 실행하고 canonical argv, 명시적 backend 환경, timeout·출력 상한, 자식 프로세스 정리, runtime hash와 Ed25519 evidence를 결속한다. signer private key는 저장소·artifact 밖에서만 읽는다. Responses 후보의 실제 count→generation canary와 격리 self-test는 `SUPPORTED`지만 raw Codex의 hard-token provider 사용은 `HOLD_TRANSPORT_UNSUPPORTED`다. ChatGPT 구독 adapter는 prompt를 stdin으로 전달하고 API key를 제거하며 실제 no-key smoke와 timeout 후 잔존 PID 방지를 통과했다. 등록 gate를 통과한 `subscription_bounded` receipt는 운영 대체 판정에 사용할 수 있지만 strict hard-token 인증에는 부적격이다. |

최신 검증은 runner-owned transport attestation과 provider backend immutable snapshot 회귀를 포함한 Product Value 관련 테스트 `164 passed`, staged TDD gate와 OMC review `APPROVE`다. 원본 backend를 executor 생성 뒤 교체해도 snapshot만 실행되는 동적 회귀를 포함한다. durable evidence bundle 단위 회귀 `10 passed`, preregistration·registry·corpus·freeze·acceptance 연계 회귀 `155 passed`, staged TDD gate와 OMC review `APPROVE`도 확인했다. ChatGPT 구독 adapter의 실제 stdin smoke `OMC_SUBSCRIPTION_STDIN_OK`, 기존 Responses transport 연관 회귀 `190 passed`, transport evidence validator `VALID`, hard-token raw Codex probe `HOLD_TRANSPORT_UNSUPPORTED`, Responses transport 격리 probe `SUPPORTED`, conformance `22 passed`, 전체 회귀 `2807 passed, 3 skipped`도 보존한다. `906cfcc` pilot preflight 후 acceptance·arm adapter·scheduler 회귀 `113 passed`와 staged TDD gate를 재확인했다. corpus availability preflight 전용 회귀 `12 passed`, Product Value 연관 회귀 `187 passed`를 통과했고 실제 6건은 `ready_count=6`, `provider_call_count=0`, input binding `50bda4bd...644b50`, report `0facbba5...788c0`으로 확인했다. schema v6 holdout preregistration 추가 후 Product Value 연관 회귀 `196 passed`, preregistration 회귀 `43 passed`, staged TDD gate와 OMC review `APPROVE`를 확인했다. v6 inventory 재계산·development/holdout 비중복·양쪽 등록 검증·initial/replication 판정과 prior-report provenance 연속성 보강 후 Product Value 확장 회귀 `214 passed`, preregistration·acceptance·roadmap 집중 회귀 `129 passed`, staged TDD gate와 OMC review `APPROVE WITH NOTES`를 확인했다. 이후 initial→replication finalize 통합 경로, 양 배치 workload·authority 비중복, 배치별 token 최소 10% 개선 계약과 역할별 Ed25519 signer·subject 재검증을 보강했다. authority subject 준비·외부 receipt 기록 CLI, 멱등·교체 차단, lazy crypto 계약까지 포함한 Product Value 전체 회귀 `224 passed`, authority·replication 집중 회귀 `15 passed`, staged TDD gate와 OMC review `APPROVE WITH NOTES`를 확인했다. durable bundle의 private snapshot·receipt/runner fail-close·manifest execution bundle 검증·registration/phase/authority/final hash 결속과 authority 전 사전 provenance gate 검증 보강 후 집중 회귀 `114 passed`, 전체 회귀 `2966 passed, 3 skipped`, staged TDD gate와 최종 OMC review `APPROVE`를 통과했다. 기존 evidence-loss batch closure는 Git commit blob 결속, signer·subject 검증, fd 기반 symlink 차단, crash-safe no-replace marker, 미설치 registry 호환을 보강했으며 Product Value 전체 회귀 `243 passed`, `py_compile`, staged diff 검사와 OMC review `APPROVE WITH NOTES`를 통과했다. 합성 E2E·smoke·blocked preflight receipt와 availability report는 운영 acceptance 표본으로 계산하지 않는다.

source workspace 신뢰 루트 결속, clean clone readiness, 설치 consumer self-claim 차단을 포함한 최신 안정화 검증은 집중 회귀 `78 passed`, 전체 회귀 `3161 passed, 3 skipped`, staged TDD gate, diff check와 OMC review `APPROVE`를 통과했다.

**P0 종료 기준**

- 실제 3–5 child 작업을 중복 실행·범위 침범·예산 초과 없이 완료한다.
- 실패·timeout은 동일한 `parent_review` 계약으로 수렴한다.
- baseline 대비 성공률은 같거나 높고 시간·token·개입 횟수는 사전 등록된 개선 기준을 충족한다.
- operational pilot은 등록된 `subscription_bounded` conformance를 통과하면 열 수 있다.
- strict certification은 `provider_enforced` backend가 adversarial conformance와 disposable shadow execution receipt를 통과한 경우에만 평가한다.

### Operational Obligation

**Plan Batch B receipt 수집**은 `PAUSED_NOT_CANCELLED`다.

- 등록된 관측 창: `2026-08-20`부터 `2026-09-18`
- 수집 계약: lock-backed implementation receipt, 최소 3개 저장소, 저장소별 최대 5건, 전체 최대 15건
- 현재 감사 상태: raw provisional receipt `7건/3개 저장소`, cap 초과 `3건` 제외, source snapshot 검증 전 `validated_eligible=0`
- 금지: 관측 창 사후 연장, quota 변경, synthetic·document·benchmark maintenance 혼입
- 다음 단계: 관측 종료 후 source snapshot 동결, universe·shortlist 10건, 독립 gold sign-off, paired 실행, blind adjudication

**Work Packet 5건 feasibility**는 `PAUSED_NOT_CANCELLED`다.

- 구현 상태: capture-only preregistration schema v2, 15분 registration buffer, 4개 authority key·operator·custody 분리, implementation-only selection, append-only source sequence·snapshot checkpoint, signed failure seal·승인된 restart parent, execution receipt, atomic case capture, durable evidence publish·authoritative reload 완료
- 검증 상태: Work Packet 집중 회귀 `45 passed`, 관련 registry·RFC 3161·로드맵 회귀 `70 passed`, 전체 회귀 `2955 passed, 3 skipped`, staged TDD gate 통과, OMC review `APPROVE`
- 현재 표본: 실제 적격 case `0/5`; 합성 unit fixture는 운영 표본으로 계산하지 않는다.
- 다음 단계: 실제 분리 authority와 canonical source inventory를 observation 15분 전에 등록한 뒤 chronological first-N implementation 5건을 capture·publish·reload하고 수집 가능성만 판정한다.
- 경계: Work Packet 결과를 품질 projection, Plan Batch B evidence 또는 대체 판정으로 자동 승격하지 않는다.

### Active Quality Validation
품질 대체 판정은 구현 완료와 분리한다. 같은 작업의 durable raw output과 독립 adjudication 없이는 우월성이나 완전 대체를 선언하지 않는다.
### Plan Quality Validation
- 현재 판정: 다중 저장소 기준 `NOT_PROVEN`
- 참고 근거: 단일 corpus Fresh Batch A의 `PROVISIONALLY_REPLACEABLE`은 repository-scoped pilot로만 보존
- 다음 마일스톤: Operational Obligation의 Batch B 수집과 독립 confirmatory batch 완료
- 종료 기준: 신규 disjoint Batch B 통과 후 별도 독립 confirmatory batch에서 재현해야 `REPLACEABLE`; 두 독립 배치가 primary gain·confidence gate까지 통과해야 `BENCHMARK_SUPERIOR`
### Review Quality Validation

- 현재 판정: durable native provider 원문 부재로 `NOT_PROVEN`
- 현재 근거: 실사용 anonymized diff 10건의 historical same-diff batch와 gold-label sign-off는 보존했지만 durable raw provider output이 없어 참고 evidence로만 사용한다.
- 참고 수치: Codex `3/8 hit, 3 FP`, OMC `6/8 hit, 6 FP`는 참고 수치일 뿐 대체 판정 근거가 아니다.
- 다음 마일스톤: durable raw output을 남긴 native review-agent 동일 10건 재실행, blind gold-label과 false-positive 재측정
- 종료 기준: OMC가 핵심 탐지율·evidence 정확도에서 Codex보다 높고 false-positive가 같거나 낮아야 대체 가능
- 대체 판정은 위 증거 마일스톤 완료 때만 갱신하며 historical pilot만으로 승격하지 않는다.
- 상세 계약: [OMC Review Synthetic Comparison](omc_review_synthetic_comparison.md)

Work-unit closure primitive는 `2026-09-01`에 구현·검증했다. session·task·request digest에 결속된 immutable envelope, 사용자 acceptance의 단일 소비 receipt, residual issue 내용 hash, validation round와 issue event의 분리, issue revision lineage·budget, scope·verification binding을 fail-close로 판정한다. envelope 동결 전에 별도 enrollment marker를 no-replace로 게시해 동결 파일이 사라진 work unit이 legacy mode로 강등되는 경로를 차단한다. closure/state 관련 회귀 `123 passed`, context/version 회귀 `36 passed`, 문법·staged diff·TDD gate와 OMC review `APPROVE WITH NOTES`를 확인했다. 이 근거는 primitive 구현 완료만 의미하며 실제 task/review/ship 종료 consumer에는 아직 연결하지 않았다. production consumer 연결, marker-only crash recovery failpoint, parent-directory durability 검증을 완료하기 전에는 일반 OMC 완료 판정에 사용하지 않는다.

Task completion lineage schema v3와 terminal capture 결속을 구현했다. `start`, `continue`, `preserve` 동작과 generated `work_id`, root/current session 순서, 중복 없는 `session_ids`, `rework_count`를 하나의 pending completion에 결속하고, continuation의 request·work class·baseline 불일치와 `document_only` 세션의 source 변경을 fail-close한다. 각 completion session은 `work_id`·root·predecessor·lineage index를 work-class lock schema v2 단일 receipt에 prospective하게 서명하며, terminal은 final에서 root까지 이 체인을 역추적해 누락·재정렬·서명 훼손·unsigned session 동시 변조를 거부한다. 별도 lineage sidecar는 terminal 서명 전 입력으로만 취급하고 sealed terminal이 그 hash와 검증된 ordered lineage를 결속한다. 관련 state·capture·candidate-universe 회귀 `132 passed`, `py_compile`, staged diff·TDD gate와 OMC review `APPROVE`를 확인했다. 이는 3건 pilot에서 재작업을 중복 case가 아닌 동일 work unit으로 계수할 수 있게 한 구현 근거이며, 실제 제품 가치나 provider 실행 성공을 의미하지 않는다.

### Operator Experience 1차 통합안

- CLI fast-path 1차 완료: 루트 `-h`·`--help`를 prompt 옵션으로 잘못 라우팅하던 회귀를 수정하고, source freshness hash는 저장소 전체가 아니라 실제 설치 대상만 순회한다. template 탐색 오류는 불완전한 hash를 반환하지 않고 fail-close한다.
- readiness fast-path 완료: 반복 호출되는 `state status`는 전체 설치 감사를 수행하지 않고 `unverified`와 동일 target의 권위 확인 명령만 출력한다. `version`과 `doctor`는 install audit의 `version_readiness`를 SSOT로 사용하며 drift 상태에서 정상 설치 문구를 출력하지 않는다. target과 CWD가 달라도 공백을 포함한 절대 script·target 경로의 안내 명령을 실행할 수 있게 고정했다. 관련 회귀 `88 passed`, 외부 CWD 실행, `py_compile`, diff check, staged TDD gate와 OMC review `APPROVE`를 확인했으며 단독 `state` 20회 측정은 median `194ms`, p95 `223ms`였다.
- 실제 5회 측정: help p95 `71.8ms`, version p95 `255.5ms`, status p95 `202.0ms`. 설치·버전·hash 집중 회귀 `155 passed`, staged TDD gate와 OMC review `APPROVE`를 통과했다.
- 작은 작업: 안전 조건을 만족하면 Lite `task → review`
- 복잡한 작업: Full `plan → task → review`
- 고위험 또는 명시적 Full override: `plan → task → critique → review`
- Stage graph SSOT 완료: 계획 생성, 실제 autopilot 품질 루프, execution metrics가 같은 `skill_path` 결정을 소비한다.
- 실행 receipt 보강 완료: instruction hash·mode·mode source·skill path·requested branch를 `pipeline_identity/v1`으로 결속한다.
- resume fail-close 완료: identity가 없거나 instruction·mode·mode source·skill path·requested branch가 다르면 기존 단계를 재사용하지 않는다.
- 안전 실행 경계 완료: frozen work contract를 기준으로 task를 격리 clone에서 실행하고 scope·verification을 검증한 뒤 immutable review packet을 생성한다. critique와 final review는 각각 별도 clean clone의 trusted `base_commit`에서 read-only로 실행하므로 candidate가 수정한 `AGENTS.md`·hook·skill을 reviewer control plane으로 자동 주입할 수 없다. 승인된 candidate만 전용 branch로 promotion하며 clone·commit·packet 결속 불일치는 fail-close한다.
- 안전 실행 검증: 관련 전체 회귀 `351 passed, 1 skipped`, reviewer 격리 집중 회귀 `3 passed`, staged TDD gate, staged diff 검사와 OMC review `APPROVE`를 통과했다. 외부 provider live smoke는 실행하지 않았으므로 이 근거만으로 운영 Autopilot 완성이나 latency 개선을 주장하지 않는다.
- Codex 출력 가독성 gate 완료: plan/task/review의 정상·차단 출력에 단계별 순서, 첫 3줄 결론, 줄 수 상한, 중복 0회, 다음 행동 1개를 적용했다. machine footer는 측정에서 제외하고, 화면에는 내부 routing 상태 대신 구체적인 선택·입력 대상을 표시한다. validator는 Codex autopilot raw·legacy-normalized 출력 경로에 연결했으며, 독립 ephemeral session 6개의 prompt·raw event·output 원문과 해시 결속을 보존했다. 관련 집중 회귀 `311 passed`와 OMC review `APPROVE`를 확인했지만 실제 사용자 개입 감소는 아직 측정하지 않았다.
- 회귀 검증: 관련 테스트 `411 passed, 1 skipped`, 전체 테스트 `2582 passed, 3 skipped`, TDD gate 통과.
- 목표: 품질 gate를 유지하면서 반복 확인, p50/p95 지연, input/output/total token을 줄인다.
- 현재 유효 latency 표본은 history의 운영 기록을 따르며 표본 기준 충족 전 라우팅 경계를 확정하지 않는다.

### Setup Distribution Integrity

- 설치 receipt schema v3가 배포 파일을 `exclusive_managed`·`merged_host`·`preserved`·`manual_review`로 분류한다.
- `setup-ignore`가 OMC 전용 파일만 literal pathspec으로 Git 추적에서 제외하고 로컬 파일을 보존하며, migration receipt 기반 rollback을 제공한다.
- `setup-ignore refresh`는 install receipt·관찰 정책·프로젝트 `.gitignore`를 변경하지 않고 repository-local exclude만 갱신한다. OMC 전용 Python bytecode는 숨기되 프로젝트 소유 quality gate와 외부 custody 전 관찰 증거는 계속 Git 표면에 노출하며, receipt·marker 손상은 쓰기 전에 fail-close한다.
- setup이 생성하는 OMC ignore block은 공유 `.gitignore`가 아니라 `git rev-parse --git-path info/exclude`로 찾은 repository-local exclude에 기록한다. 예약된 OMC namespace만 wildcard로 압축하고 일반 경로는 literal rule로 유지하며, linked worktree와 비 Git 대상의 동작을 분리한다.
- 설치 SSOT는 고정된 `omc_kit/` 경로가 아니라 `.omc/install-source.json`의 `source_path`로 통일했다. 저장소에 추적되던 legacy `omc_kit/scripts` 복사본과 nested installer fallback은 제거했다.
- active migration은 legacy schema v1/v2의 `.gitignore` 결속을 유지하고 schema v3부터 local exclude hash에 결속한다. receipt가 손상되거나 결속된 ignore surface가 달라지면 manifest 생성과 파일 변경 전에 exit code `2`로 fail-close한다.
- receipt schema v3를 install audit·version 판정이 인식하며, legacy migration receipt v1의 안전한 v2 승격까지 지원한다.
- local-exclude 전환은 관련 회귀 `185 passed`, 핵심 setup/gitignore 테스트 `126 passed`, staged TDD gate와 fresh setup smoke를 통과했다. smoke에서 공유 `.gitignore`는 그대로 유지됐고 exclusive receipt 214개 경로는 86개 rule로 압축됐다(59.8% 감소).
- 실제 사용처 8곳은 `55252ec` 기준 `setup --force` 재배포와 strict install audit `8/8`을 통과했다. 모든 대상에서 `installed_integrity_status=ok`, `core_usage_readiness=ready`, `source_freshness_status=up_to_date`, `verification_status=ok`를 확인했고 설치 전후 Git 상태가 동일해 기존 로컬 작업도 보존됐다. 소스 `omc_kit` 자체는 consumer inventory에서 제외했으며 비 Git 대상 1곳은 completion hook을 `not_applicable`로 유지했다.
- setup-created ownership과 Git visibility 검증을 보강했다. receipt가 최초 생성 파일을 `setup_created`로 기록하고, setup-created merged host와 OMC runtime 경로만 local exclude에 포함한다. strict audit은 이 경로가 다시 untracked로 노출되거나 Git probe가 실패하면 `setup-visibility` 오류로 fail-close한다. 관련 집중 회귀 `161 passed`, staged TDD gate, diff check와 OMC review `APPROVE`를 통과했다.
- 최신 source hash `7471392acd9bc4a02875223e864486a0694ae37c8b9e7e48f0eeb11aadce38ba` 기준 실제 사용처 9곳을 다시 `setup --force` 배포하고 strict audit `9/9`을 통과했다. 모든 대상이 `installed_integrity_status=ok`, `core_usage_readiness=ready`, `source_freshness_status=up_to_date`, `verification_status=ok`였고 설치 전후 Git 상태를 보존했다. per-target 백업과 실행 로그는 `/private/tmp/omc-setup-force-20260901-2145`에 남겼다.

## 로드맵 검증 매트릭스

| 로드맵 완료 항목 | 실제 반영 증거 | Fugu 비교에 쓰는 축 | 판정 규칙 |
|---|---|---|---|
| V1~V4 routing | 코드·회귀 테스트·운영 telemetry | routing·cost·recovery | 문구만 있으면 `문서만 반영`, 코드와 테스트가 있으면 `반영 확인` |
| Operator Experience | observed request·latency·intervention evidence | 사용자 개입·지연 | 운영 표본까지 acceptance를 통과해야 `체감 개선 확인` |
| V5 orchestration | grant·scheduler·실제 child execution receipt | 자동 분해·위임 | grant만으로 실행 완료를 주장하지 않으며 실제 acceptance가 필요 |

Fugu 비교 문구는 `현재 상태 참조`와 `반영 검증 완료`를 구분한다. 경쟁 제품의 문서 주장과 OMC의 구현 근거도 같은 증거 수준처럼 혼합하지 않는다.

## 실행 우선순위

contract revision 7에서는 T0 전에 calibration qualification과 표본 제외 synthetic protocol rehearsal을 완료하고 네 authority의 고유 키와 서명을 start gate에 명시해야 한다. calibration은 구조화 판정만 비교하고 자유서술 문구는 비교하지 않으며, adjudicator의 gold·arm mapping 사전 접근 금지를 information-access custody로 봉인한다. 모든 `INCONCLUSIVE`는 현재 study를 종료하며 기존 표본을 연장·합산하지 않고 사전 등록된 사유별 새 study만 허용한다.

현재는 `NO_ACTIVE_EXECUTION_LANE`이다. `$omc-dashboard` 첫 forward case `repo-ops-20260908-v4`는 기술적으로 통과했지만 제품 판정은 `REVISION_REQUIRED`이며 제품 가치 증거가 아니다. question evidence contract를 반영했지만 두 번째 독립 case 전에는 반복 가능성을 주장하지 않는다. Persona contract를 재개하기로 별도 결정한 경우에만 calibration qualification과 four-authority rehearsal, anonymous arm mapping, 외부 custody registration, fresh T0·21일 창을 차례로 동결한다. Product Value·Plan·Review·Work Packet·Decision Policy 검증도 모두 `PAUSED_NOT_CANCELLED`이며 추가 스킬·transport·benchmark fixture를 늘리지 않는다.

## 제품 원칙과 금지선

- 제품 포지션: 승인된 범위와 예산 안에서 결과를 재현 가능한 receipt로 증명하는 도구 중립 오케스트레이터
- 사람의 명시 승인 없이 push·PR·deploy·delete·reset 권한을 확장하지 않는다.
- 운영 evidence 없이 자동 model switch와 자동 재분배를 열지 않는다.
- synthetic·historical pilot만으로 대체 가능성을 주장하지 않는다.
- 특정 프레임워크·테스트 도구를 OMC 코어 정책에 하드코딩하지 않는다.
- 실행 로그 없이 정책 규칙만 늘리거나 모델 선택을 블랙박스로 만들지 않는다.

## 검증과 문서 SSOT

- 현재 상태·우선순위·active validation: 이 문서
- 완료 구현·중단 실험·상세 설계 기록: [Roadmap History](automatic_model_routing_roadmap_history.md)
- Review 판정 원문: [OMC Review Synthetic Comparison](omc_review_synthetic_comparison.md)
- Plan runtime·gold·preregistration 원문: `scripts/fixtures/omc_plan_*`
- 현재 문서가 history와 충돌하면 전용 benchmark artifact를 확인하고 현재 요약을 교정한다.

## 다음 실행 순서

현재 0번은 제품 decision gate이며 실행 단계가 아니다. Persona registration 계약과 외부 custody 절차, 최종 sealed decision 재검증은 [Persona Pilot Operator Runbook](task_review_persona_operator_runbook.md)에 보존한다.

0. **SEMANTIC REVISION — `REVISION_REQUIRED` / `NO_ACTIVE_EXECUTION_LANE`** — `$omc-dashboard` 첫 forward case `repo-ops-20260908-v4`는 기술 검증과 별개로 source에 없는 priority를 추천해 `UNSUPPORTED_PRIORITY_INFERENCE`로 닫았다. question evidence contract가 질문별 source field·계산·결측 정책·surface를 결속하며, 별도 승인된 두 번째 독립 case 전에는 `WORKFLOW_REPEATABILITY_OBSERVED`로 승격하지 않는다. Claude Artifact 동등성, 제품 우위·수정 지시 감소는 계속 `NOT_YET_PROVEN`이다.

아래 1–16번과 Persona Pilot은 모두 `PAUSED_NOT_CANCELLED` backlog다. 0번 사용자 판정 뒤에도 사용자가 명시적으로 하나를 선택하기 전에는 실행하지 않는다.

1. **BLOCKED_EVIDENCE_LOSS** (`PAUSED_NOT_CANCELLED`) — 기존 schema v1 Product Value batch는 실행 대상에서 제외했지만 corpus v2-r1의 source commit·request·DoD·verification·environment artifact 원문과 durable registration을 현재 검증할 수 없다. hash-only record와 과거 availability 요약은 development evidence로 승계하지 않으며, 재개가 승인되면 신규 prospective study를 사전 등록해 다시 수집한다.
2. **완료** — `bounded_n_child_execution` claim scope와 development evidence 판정 gate 구현 완료. 기존 v3–v5 manifest는 `development`로 정규화하며 통과해도 최고 `DEVELOPMENT_PASS`만 발행한다.
3. **완료** — schema v6 holdout manifest 계약과 `prepare-v6` CLI를 구현했다. initial/replication 역할과 development 기준·양쪽 workload inventory·selection policy·선행 holdout report 해시를 preregistration digest에 결속하며 기존 v3–v5 직렬화와 판정은 유지한다.
4. **검증 코드 완료 / 실제 corpus 대기** — 현재 구현·판정 기준을 동결하고, development corpus와 repository·source snapshot·request·workload·execution packet이 겹치지 않는 disjoint holdout 5건을 initial과 replication에 각각 선정한다. v6 validator는 canonical inventory 불일치, observation chronology 위반, initial과 replication 사이의 비중복 또는 authority 역할 분리 위반을 거부한다. 실제 holdout 선정은 아직 남아 있다.
5. **CLI·clean-clone 회귀 완료 / 실제 evidence 대기** — holdout corpus와 exact 3–5 child decomposition을 사전 승인한 뒤 schema v6 preregistration을 동결하고 schema v2 registry record·RFC 3161 receipt·execution packet을 외부 durable evidence root에 게시한다. loader와 acceptance는 검증된 bundle만 소비하며 clean clone 동일 hash 복구 계약은 완료했다.
6. **코드 완료 / 실행 증거 대기** — acceptance runner는 양쪽 등록·disjointness·chronology 검증을 통과한 v6 manifest만 `holdout`으로 소비한다. initial 통과는 provisional이며, 선행 report의 development registration receipt와 전체 inventory·threshold·authority provenance까지 검증한 독립 replication 통과 후에만 최종 운영 판정을 허용한다. token 중앙값은 각 배치에서 baseline보다 최소 10% 개선되어야 한다.
7. 고정 커밋과 동일 no-key `subscription_bounded` 조건에서 비판정 pilot 1건과 paired confirmatory 5건을 실행해 성공률·elapsed·post-call token·개입·scope·duplicate·major regression receipt를 수집한다.
8. holdout evidence만 운영 대체 판정에 사용한다. primary metric을 모두 충족하면 `BOUNDED_EXECUTION_OPERATIONALLY_REPLACEABLE`, 실패하면 `NOT_REPLACEABLE`로 종료하며 Plan·Review·전체 OMC 판정은 변경하지 않는다.
9. strict hard-token 인증은 기본 운영 판정과 분리한다. no-key transport가 exact count·native cap을 제공하거나 사용자가 credentialed transport를 별도로 선택한 경우에만 conformance와 `STRICTLY_CERTIFIED` 평가를 재개한다.
10. Product Value 결과를 근거로 Lite/Full 경계를 조정하고 불확실한 요청은 Full로 fail-safe 승격한다.
11. **capture-only schema v2 코드 완료 / 실제 표본 대기** — Work Packet manifest의 15분 registration buffer, implementation-only selection, 4-authority key·custody 분리, append-only source checkpoint, failure seal·명시적 restart parent, durable publish/reload 계약을 고정했다. 다음은 실제 chronological first-N 5건을 capture·publish·reload해 수집 가능성만 판정하는 것이며 Plan Batch B, Product Value acceptance 또는 품질 projection으로 합산하지 않는다.
12. Plan Batch B와 native Review 독립 검증을 계속해 각 대체 판정을 별도로 갱신한다.
13. **public CLI·운영 source drift·consumer 재배포 완료 / 상태 신뢰성·멀티 호스트 검증 대기** — 루트 help는 setup·task·status·review·ship 핵심 흐름과 orchestrate·autopilot·team advanced 흐름을 우선 표시한다. 모든 직접 CLI command는 core·advanced·research 중 하나로 단일 분류되며, dispatch는 이 registry를 사용해 prompt 오라우팅을 막는다. Product Value·N-child research 명령은 직접 호출 호환성을 유지한 채 루트 help에서 숨겼고 README 계약과 CLI 회귀 테스트를 고정했다. research·benchmark 실행은 교체 없이 real-use cohort에서 제외한다. `.omc` 운영 상태 변경이 source hash·version·strict audit를 오염하지 않고 실제 배포 대상 변경은 `update_available`로 탐지하는 것을 격리 consumer에서 확인했다. 현재 배포 버전·consumer 수·검증 결과는 실행 시점의 strict install audit 결과에서 확인하며, 로드맵의 과거 rollout 수치는 현재 상태 주장으로 재사용하지 않는다. 다음은 stale active session 교정과 멀티 호스트 동일 fixture 검증이다.
14. **Autopilot 안전 실행 코드 완료 / 외부 smoke 대기** — frozen work contract, 격리 task workspace, trusted-base critique/review, immutable review packet과 candidate branch 전용 promotion을 고정했다. 다음은 고정 커밋의 격리 clone에서 실제 provider TASK→CRITIQUE→REVIEW smoke를 실행해 candidate 변경 보존, reviewer control-plane 비오염, 실패 시 promotion 차단, latency·token·사용자 개입 receipt를 함께 확인하는 것이다.
15. **Decision Policy feasibility 계약 완료 / 실제 evidence 대기** — 실제 결과와 독립 causal-review receipt로 확인된 chronological first-N 실패 5건을 수집하고, 사전 승인된 정책과 동일 subject의 baseline/policy arm을 paired 실행한다. 완료율·불필요한 검증 왕복·사용자 개입·token을 사전 고정 기준으로 비교하며 독립 판정 전에는 효과를 주장하지 않는다.
16. **Codex-first capture-all + global closure 코드·review 완료 / 버전·배포·실제 표본 대기** — single-host roster/T0 binding, 저장소별 capture-all, 전역 first-5 closure, cross-session exact binding과 quarantine를 로컬 계약으로 구현했고 fail-closed 코드 리뷰를 통과했다. 다음에는 버전을 올려 등록 저장소에 `setup --force`하고, 동일 roster·T0로 `live-enable`한다. 실제 14일 모집단과 T1+24h closure 전에는 수집 가능성을 주장하지 않으며, 5건 표본을 제품 효과 근거로 재사용하지 않는다.

## 한 줄 결론

현재 OMC의 다음 제품 전환점은 기능 추가가 아니라, 자연 발생 작업에서 OMC가 반복 사용될 가치가 있는지 먼저 확인하고 그 gate를 통과한 뒤 bounded N-child가 성공률·시간·token·개입을 개선하는지 독립 receipt로 증명하는 것이다.
