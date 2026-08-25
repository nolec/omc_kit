# Automatic Model Routing Roadmap

## Current Roadmap

OMC의 제품 목표는 사용자가 모델·executor·작업 단계를 직접 조합하지 않아도 요청의 난이도·위험·실패 신호에 따라 안전한 실행 경로를 선택하는 도구 중립 오케스트레이터다. 현재 판단과 다음 작업은 이 문서를 기준으로 하며, 완료 이력과 과거 실험 원문은 [Roadmap History](automatic_model_routing_roadmap_history.md)에 보존한다.

### 현재 위치

| 트랙 | 상태 | 현재 근거 | 남은 핵심 |
|---|---|---|---|
| V1 Skill-based Routing | 완료 | task kind와 skill 기반 profile 선택 | 운영값 유지 |
| V2 Step-level Routing | 완료 | step metadata가 실제 profile 선택에 반영 | 운영 surface 미세조정 |
| V3 Failure-driven Escalation | 완료 | failure class·retry·reroute·hold decision 통합 | multi-run tuning |
| V4 Telemetry-driven Tuning | 완료 | token·cost·retry·reroute·readiness KPI 수집 | 운영 drift 감시 |
| V5 Learned Orchestrator | 부분 반영 | single child, exact 2-child, v2 grant 전용 bounded N-child scheduler·provider adapter, authoritative acceptance harness | 실제 3–5 child 운영 표본 acceptance |
| Operator Experience | 진행중 | output contract, Lite/Full routing, Stage graph SSOT, resume identity fail-close 구축 | 지연·개입 횟수 운영 검증 |

현재 OMC는 `rule-based orchestration v1`을 넘어 승인된 v2 grant를 제한 병렬 실행하는 `bounded orchestration` 단계다. 일반 N-child 실행과 authoritative acceptance 판정 코드는 갖췄지만 실제 운영 표본 acceptance, 실패 재분배, 자동 모델 전환, 자동 ship은 아직 완료되지 않았다.

### 제품 약점 기반 개선 축

기능 수가 아니라 사용자가 실제 작업을 더 잘 끝내는지를 기준으로 남은 약점을 세 핵심 축과 하나의 지원 축으로 관리한다. Product Value·Operator Experience·Evidence를 핵심 축으로, Maintainability를 이를 지속시키는 지원 축으로 둔다. 구현 완료와 제품 효과 검증을 분리하며, 새 스킬·정책·benchmark fixture 수 증가는 완료 지표로 사용하지 않는다.

| 개선 축 | 우선순위 | 현재 약점 | 다음 범위 | 종료 기준 |
|---|---|---|---|---|
| Product Value | P0 | bounded scheduler는 완성됐지만 실제 다중 child 가치가 미검증 | 실제 3–5 child 운영 acceptance와 single-agent baseline 비교 | 중복 실행·scope·budget 위반 없이 완료하며, baseline 대비 성공률은 같거나 높고 시간·token·개입 횟수는 사전 등록된 개선 기준을 충족 |
| Operator Experience | P1 | 반복 승인·상태 확인·스킬 왕복이 작은 작업의 준비 시간을 키움 | Lite/Full observed 표본에서 단계별 latency·retry·개입 측정 후 안전한 자동 분기 조정 | 품질 gate를 유지하면서 p50/p95·token·사용자 개입 횟수 감소 |
| Evidence | P1 | Plan·Review 품질 우위와 비용 절감이 독립 운영 증거로 확정되지 않음 | single-agent baseline 대비 성공률·시간·token·개입 횟수, durable raw output, blind adjudication 수집 | 사전 등록된 독립 배치의 acceptance를 통과한 지표만 대체·우월 판정에 사용 |
| Maintainability | P2 | 문서·fixture·검증 도구가 커져 사용자 기능과 내부 연구 경계가 흐림 | README와 실제 executor 구현 상태 정합화, 사용자 명령과 benchmark 내부 도구 구분 | README·CLI·로드맵 상태가 일치하고 일반 사용 경로가 setup·task·autopilot·status·ship 중심으로 설명됨 |

운영 증거 없는 자동화 확대 금지를 공통 원칙으로 둔다. 실제 병목을 줄이지 않는 새 추상화, 정책, 스킬 추가는 위 종료 기준보다 우선하지 않는다.

### Operational P0

**bounded N-child 실제 acceptance**를 단일 운영 최우선 작업으로 둔다.

- 입력: 승인된 `3–5` child DAG grant, dependency, prompt, scope, aggregate budget
- 선행 정책: scope normalization과 child `approval_id` 고유성 정책은 완료
- 실행 전 계약: 승인 전 canonical proposal이 graph·prompt·grant·budget·target-bound scope를 결속하고, 승인 시 같은 의미와 expiry를 재검증한 v2 grant만 `scheduler_eligible=true`
- 구현 완료: ready child claim, 제한 병렬 실행, dependency 해제, scope 격리 patch, 별도 DAG·child ledger
- 안전 경계 완료: immutable provider snapshot, hard output bound, call·elapsed·token budget, idempotency·expiry 재검증
- 실패 정책 완료: retry·자동 재분배·fallback·resume 없이 bounded `parent_review`로 전환하고 실패 결과의 patch 적용 차단
- acceptance 계약 완료: 고정 5-case 카탈로그(성공 2·실패·timeout·scope violation), source commit·request hash·receipt·DAG/child ledger 결속, authoritative reload 전용 최종 판정
- 합성 E2E 통과: capability handshake·hard token/output limit·process-group timeout·scope/budget 위반 차단을 고정 fixture로 검증; 이 결과는 운영 표본으로 간주하지 않음
- Product Value preregistration 계약 완료: v1의 prospective 고정 5건 호환성을 유지하고, v2는 1건 비판정 pilot과 paired confirmatory 5건, 최소 2개 저장소·2개 구현 유형, canonical workload 순서·해시 불변성, repository alias↔identity 일대일 매핑, 관측 창·등록 authority, 사후 제외 금지, 동일 비교 조건·개입 측정·판정 threshold를 `frozen` manifest로 고정한다. 이 상태는 `claim_eligible=false`이며 등록 완료나 운영 acceptance를 의미하지 않는다.
- Product Value 중립 등록 검증 경로 완료: Git registry의 exact preregistration record·ancestor와 RFC 3161 claim·authority·관측 시작 전 timestamp·receipt digest를 공통 primitive로 검증하고, Product Value schema v2의 `prepare-v2` → `registry-record` → `prepare-receipt` → `validate-registration` 경로를 연결했다. 정확한 registry anchor와 유효한 receipt를 모두 통과한 경우에만 `claim_eligible=true`다. 실제 외부 registration receipt와 운영 acceptance는 아직 확보하지 않았다.
- 현재 병목: 실제 구현 workload 6건(1건 비판정 pilot과 동일 조건 paired 5건)의 source·request·DoD·verification hash를 채우고, exact Git registry record와 실제 외부 RFC 3161 registration receipt를 관측 전에 확보·검증한 뒤 pilot과 confirmatory 5건을 순서대로 실행한다.
- 완료 기준: 실제 3–5 child 작업에서 중복 실행·범위 침범·예산 초과 없이 완료하고 실패·timeout이 같은 parent review 계약으로 수렴

### Operational Obligation

**Plan Batch B receipt 수집**은 Implementation P0와 별개로 중단 없이 병행한다.

- 등록된 관측 창: `2026-08-20`부터 `2026-09-18`
- 수집 계약: lock-backed implementation receipt, 최소 3개 저장소, 저장소별 최대 5건, 전체 최대 15건
- 현재 감사 상태: raw provisional receipt `7건/3개 저장소`, cap 초과 `3건` 제외, source snapshot 검증 전 `validated_eligible=0`
- 금지: 관측 창 사후 연장, quota 변경, synthetic·document·benchmark maintenance 혼입
- 다음 단계: 관측 종료 후 source snapshot 동결, universe·shortlist 10건, 독립 gold sign-off, paired 실행, blind adjudication

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

### Operator Experience 1차 통합안

- 작은 작업: 안전 조건을 만족하면 Lite `task → review`
- 복잡한 작업: Full `plan → task → review`
- 고위험 또는 명시적 Full override: `plan → task → critique → review`
- Stage graph SSOT 완료: 계획 생성, 실제 autopilot 품질 루프, execution metrics가 같은 `skill_path` 결정을 소비한다.
- 실행 receipt 보강 완료: instruction hash·mode·mode source·skill path·requested branch를 `pipeline_identity/v1`으로 결속한다.
- resume fail-close 완료: identity가 없거나 instruction·mode·mode source·skill path·requested branch가 다르면 기존 단계를 재사용하지 않는다.
- 회귀 검증: 관련 테스트 `411 passed, 1 skipped`, 전체 테스트 `2582 passed, 3 skipped`, TDD gate 통과.
- 목표: 품질 gate를 유지하면서 반복 확인, p50/p95 지연, input/output/total token을 줄인다.
- 현재 유효 latency 표본은 history의 운영 기록을 따르며 표본 기준 충족 전 라우팅 경계를 확정하지 않는다.

## 로드맵 검증 매트릭스

| 로드맵 완료 항목 | 실제 반영 증거 | Fugu 비교에 쓰는 축 | 판정 규칙 |
|---|---|---|---|
| V1~V4 routing | 코드·회귀 테스트·운영 telemetry | routing·cost·recovery | 문구만 있으면 `문서만 반영`, 코드와 테스트가 있으면 `반영 확인` |
| Operator Experience | observed request·latency·intervention evidence | 사용자 개입·지연 | 운영 표본까지 acceptance를 통과해야 `체감 개선 확인` |
| V5 orchestration | grant·scheduler·실제 child execution receipt | 자동 분해·위임 | grant만으로 실행 완료를 주장하지 않으며 실제 acceptance가 필요 |

Fugu 비교 문구는 `현재 상태 참조`와 `반영 검증 완료`를 구분한다. 경쟁 제품의 문서 주장과 OMC의 구현 근거도 같은 증거 수준처럼 혼합하지 않는다.

## 제품 개선 실행 백로그

Oh My Claude Code `v4.15.10` 비교에서 확인한 운영 증거·자동 복구·자연어 UX 약점을 OMC 제품 축에 편입한다. 경쟁 제품의 mode를 복제하는 대신 Product Value, Operator Experience, Evidence 종료 기준을 직접 충족한다. 경쟁 제품의 토큰 절감 주장과 OMC의 안전성 우위 가설은 동일 작업 직접 측정 전까지 확정 사실로 사용하지 않는다.

| 우선순위 | 개선 축 | 다음 범위 | 종료 기준 |
|---|---|---|---|
| P0 | bounded N-child 운영 검증 | preregistration된 실제 구현 5건의 등록 receipt, 1건 pilot, paired 실행·budget enforcement 증거 | 실제 3–5 child acceptance 통과 |
| P1 | 자동 모드 선택과 사용자 개입 감축 | Lite/Full 자동 분기와 fail-safe 승격 | 품질 유지 + 개입·p50/p95 감소 |
| P1 | 비용·품질 운영 증거 | single-agent 대비 token·elapsed·retry·intervention 비교 | 사전 등록 10건 acceptance 통과 |
| P2 | 도구 중립 UX 차별화 | Codex·Claude Code·Gemini·Cursor 공통 receipt·readiness | 3개 이상 호스트 동일 fixture 통과 |

작업 순서는 `P0 Product Value acceptance → P1 Operator Experience 측정·조정 → P1 Evidence 독립 검증 → P2 유지보수·멀티 호스트 UX`로 고정한다. 새로운 스킬 수를 늘리거나 경쟁 제품의 mode를 복제하는 작업은 이 acceptance를 앞당기지 않으면 우선하지 않는다.

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

1. authoritative acceptance 진입점으로 고정된 실제 3–5 child 작업의 성공·실패·timeout receipt를 수집한다.
2. 같은 작업의 single-agent baseline과 성공률·시간·token·개입 telemetry를 비교한다.
3. 결과를 근거로 Lite/Full 경계를 조정하고 불확실한 요청은 Full로 fail-safe 승격한다.
4. Plan Batch B와 native Review 독립 검증을 계속해 대체 판정을 갱신한다.
5. README·CLI·로드맵 정합성을 맞춘 뒤 멀티 호스트 동일 fixture로 도구 중립 UX를 검증한다.

## 한 줄 결론

현재 OMC의 다음 제품 전환점은 기능 추가가 아니라, 완성된 bounded N-child 실행이 실제 작업의 성공률을 높이고 시간·token·개입을 줄이는지 독립 receipt로 증명하는 것이다.
