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
| Maintainability | P2 | setup 배포 SSOT·소유권·rollback은 정리됐지만 문서·fixture·검증 도구가 커져 사용자 기능과 내부 연구 경계가 흐림 | README와 실제 executor 구현 상태 정합화, 사용자 명령과 benchmark 내부 도구 구분 | README·CLI·로드맵 상태가 일치하고 일반 사용 경로가 setup·task·autopilot·status·ship 중심으로 설명됨 |

운영 증거 없는 자동화 확대 금지를 공통 원칙으로 둔다. 실제 병목을 줄이지 않는 새 추상화, 정책, 스킬 추가는 위 종료 기준보다 우선하지 않는다.

### Operational P0

**bounded N-child 실제 acceptance**를 단일 운영 최우선 작업으로 둔다.

**현재 병목**

등록된 Product Value 후보가 요구하는 `provider_enforced` hard-token 계약에는 raw Codex 실행 파일을 직접 사용할 수 없어 계속 `HOLD_TRANSPORT_UNSUPPORTED`다. 다만 API 키 없이 ChatGPT 로그인 상태를 사용하는 별도 `subscription_bounded` adapter를 구현해 elapsed time·output chars·process group을 강제하고 실제 input/output/total token을 호출 후 receipt로 기록할 수 있게 됐다. 이 경로는 hard total-token cap을 주장하지 않으며 strict Product Value acceptance에는 부적격이지만, no-key 운영 진단과 비용·지연 표본 수집에는 사용할 수 있다. exact input count와 native output cap이 필요한 strict 증명 경로는 기존 Responses transport/backend 후보와 분리해 유지한다.

**준비 완료 체크포인트**

| 영역 | 완료 근거 |
|---|---|
| Scheduler | scope normalization과 child `approval_id` 고유성 정책은 완료. 승인 전 canonical proposal과 v2 grant 재검증, ready-child claim, 제한 병렬 실행, dependency 해제, scope 격리 patch, DAG·child ledger를 구현했다. |
| 안전·실패 | immutable provider snapshot, hard output/call·elapsed·token budget, idempotency·expiry를 강제한다. timeout·scope violation·부분 실패는 bounded `parent_review`로 수렴하고 patch 적용을 막는다. acceptance 복합 제한 분류 완료. |
| Preregistration | Product Value preregistration 계약 완료. 1건 비판정 pilot과 동일 조건 paired 5건, canonical workload·pair 순서·execution packet·environment receipt 해시, immutable runner·arm adapter·scheduler·provider adapter bundle을 frozen manifest에 결속한다. 이 상태는 `claim_eligible=false`이며 등록이나 acceptance를 선점하지 않는다. |
| 등록 | Product Value 중립 등록 검증 경로 완료. `prepare-v2` → `registry-record` → `prepare-receipt` → `validate-registration`과 durable schema v2 record가 exact Git anchor·RFC 3161 receipt를 검증한다. schema v5 manifest `69115b41...af8df`, registry commit `8b23f83`, receipt `70d18121...d510`을 확보했다. |
| Corpus·freeze | 실제 구현 workload 6건의 corpus v2-r1, exact 3–5 child decomposition, repository identity/source commit, dependency lock, read-only cache와 immutable execution bundle을 동결했다. `product-value-freeze prepare-inputs → prepare → validate`로 재계산한다. |
| Acceptance | Product Value paired acceptance harness 완료. OMC arm은 승인된 v2 grant·child prompt·dependency·scope·aggregate budget을 사용하고 baseline arm은 동일 provider adapter를 사용한다. runner 실측 elapsed·token·개입·scope·budget과 raw output을 저장하며 authoritative reload 후 모든 arm 성공일 때만 `run-pilot` → `run-confirmatory` → `finalize`한다. 운영 교체 판정과 strict hard-token 인증을 분리해 transport 증거가 부족하면 운영 지표가 통과해도 `HOLD_TRANSPORT`로 fail-close한다. |
| Provider 계약 | Product Value provider enforcement 계약 v2 완료. provider 출력의 profile 자기 주장은 폐기하고 runner가 adapter·backend·capability hash를 직접 결속한다. backend는 승인 hash 확인 후 immutable runtime으로 복사하고 모든 provider subprocess의 `OMC_PROVIDER_BACKEND`를 snapshot 경로로 고정해 검사-실행 간 교체를 차단한다. OpenAI Responses backend 후보는 count endpoint의 exact input count에서 남은 output budget을 계산하고 native `max_output_tokens`로 전달하며 completed usage가 reservation을 넘으면 fail-close한다. boolean-only backend를 외부 실행 전에 거부하고 legacy v2 prepared input 재사용을 fail-close한다. 별도 `subscription_bounded` profile은 ChatGPT 구독 인증과 post-call usage만 허용하며 `provider_enforced`와 혼용하지 않는다. |
| Conformance | Product Value provider conformance 증거 계약 완료. trusted metering receipt만 정산하고 위반·실패는 worst-case `indeterminate`로 봉인한다. over-limit 요청, forged capability·usage, timeout, output overflow를 포함한 adversarial conformance와 disposable shadow execution receipt를 검증하되 실제 실행 전 `claim_eligible=false`다. |
| Transport feasibility | 승인 hash의 arm adapter·scheduler·provider adapter·provider backend immutable snapshot만 고정 명령으로 실행하고 canonical argv, 명시적 backend 환경, timeout·출력 상한, 자식 프로세스 정리, runtime hash와 Ed25519 evidence를 결속한다. signer private key는 저장소·artifact 밖에서만 읽는다. Responses 후보의 실제 count→generation canary와 격리 self-test는 `SUPPORTED`지만 raw Codex의 hard-token provider 사용은 `HOLD_TRANSPORT_UNSUPPORTED`다. ChatGPT 구독 adapter는 prompt를 stdin으로 전달하고 API key를 제거하며 실제 no-key smoke와 timeout 후 잔존 PID 방지를 통과했지만 `claim_eligible=false` 진단 lane으로만 분류한다. |

최신 검증은 runner-owned transport attestation과 provider backend immutable snapshot 회귀를 포함한 Product Value 관련 테스트 `164 passed`, staged TDD gate와 OMC review `APPROVE`다. 원본 backend를 executor 생성 뒤 교체해도 snapshot만 실행되는 동적 회귀를 포함한다. ChatGPT 구독 adapter의 실제 stdin smoke `OMC_SUBSCRIPTION_STDIN_OK`, 기존 Responses transport 연관 회귀 `190 passed`, transport evidence validator `VALID`, hard-token raw Codex probe `HOLD_TRANSPORT_UNSUPPORTED`, Responses transport 격리 probe `SUPPORTED`, conformance `22 passed`, 전체 회귀 `2807 passed, 3 skipped`도 보존한다. 합성 E2E와 준비된 receipt, subscription 진단 실행은 운영 acceptance 표본으로 계산하지 않는다.

**P0 종료 기준**

- 실제 3–5 child 작업을 중복 실행·범위 침범·예산 초과 없이 완료한다.
- 실패·timeout은 동일한 `parent_review` 계약으로 수렴한다.
- baseline 대비 성공률은 같거나 높고 시간·token·개입 횟수는 사전 등록된 개선 기준을 충족한다.
- production backend가 adversarial conformance와 disposable shadow execution receipt를 통과한 뒤에만 pilot을 연다.

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

### Setup Distribution Integrity

- 설치 receipt schema v3가 배포 파일을 `exclusive_managed`·`merged_host`·`preserved`·`manual_review`로 분류한다.
- `setup-ignore`가 OMC 전용 파일만 literal pathspec으로 Git 추적에서 제외하고 로컬 파일을 보존하며, migration receipt 기반 rollback을 제공한다.
- 설치 SSOT는 고정된 `omc_kit/` 경로가 아니라 `.omc/install-source.json`의 `source_path`로 통일했다. 저장소에 추적되던 legacy `omc_kit/scripts` 복사본과 nested installer fallback은 제거했다.
- active migration의 receipt·`.gitignore` hash가 불일치하거나 receipt가 손상되면 manifest 생성과 파일 변경 전에 exit code `2`로 fail-close한다.
- receipt schema v3를 install audit·version 판정이 인식하며, legacy migration receipt v1의 안전한 v2 승격까지 지원한다.
- 실제 사용처 8곳에 `setup --force`를 적용했고 strict install audit `8/8`에서 source metadata·receipt schema·관리 block·설치 무결성·source freshness가 모두 통과했다. 비 Git 대상 1곳은 completion hook을 `not_applicable`로 판정하고 핵심 OMC 사용 준비 상태는 `ready`로 확인했다.

## 로드맵 검증 매트릭스

| 로드맵 완료 항목 | 실제 반영 증거 | Fugu 비교에 쓰는 축 | 판정 규칙 |
|---|---|---|---|
| V1~V4 routing | 코드·회귀 테스트·운영 telemetry | routing·cost·recovery | 문구만 있으면 `문서만 반영`, 코드와 테스트가 있으면 `반영 확인` |
| Operator Experience | observed request·latency·intervention evidence | 사용자 개입·지연 | 운영 표본까지 acceptance를 통과해야 `체감 개선 확인` |
| V5 orchestration | grant·scheduler·실제 child execution receipt | 자동 분해·위임 | grant만으로 실행 완료를 주장하지 않으며 실제 acceptance가 필요 |

Fugu 비교 문구는 `현재 상태 참조`와 `반영 검증 완료`를 구분한다. 경쟁 제품의 문서 주장과 OMC의 구현 근거도 같은 증거 수준처럼 혼합하지 않는다.

## 실행 우선순위

`P0 Product Value acceptance → P1 Operator Experience 측정·조정 → P1 Evidence 독립 검증 → P2 유지보수·멀티 호스트 UX` 순서로 고정한다. 경쟁 제품의 mode를 복제하거나 새 스킬을 늘리는 작업은 이 acceptance를 앞당길 때만 수행한다.

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

1. 현재 runner-owned transport attestation·backend snapshot 구현을 고정 커밋으로 만들고 no-key capability·smoke receipt를 durable evidence로 보존한다.
2. 해당 고정 커밋에서 승인된 workload 중 1건을 subscription 진단 pilot으로 실행해 성공·시간·post-call token·개입 receipt를 수집하되 Product Value acceptance나 hard-cap 증거로 승격하지 않는다.
3. 진단 결과가 실행 가능성을 뒷받침하면 같은 조건의 paired 진단 표본을 추가하고 single-agent baseline과 성공률·시간·token·개입을 비교한다.
4. `provider_enforced` strict acceptance는 API key를 사용자 기본 전제로 요구하지 않는다. no-key transport가 exact count·native cap을 제공할 때 재개하거나, 사용자가 별도로 credentialed transport를 선택한 경우에만 Responses canary와 9개 adversarial conformance를 수행한다.
5. strict transport가 확보되면 corpus v2-r1과 decomposition을 유지한 새 v4 candidate·schema v5 preregistration을 재동결하고 Git registry·RFC 3161 receipt를 갱신한다.
6. paired confirmatory 5건의 실제 3–5 child 성공·실패·timeout receipt를 수집한다. authoritative pilot 1건과 이 confirmatory 표본을 모두 확보한 뒤에만 Product Value `finalize` 판정을 발행한다.
7. 결과를 근거로 Lite/Full 경계를 조정하고 불확실한 요청은 Full로 fail-safe 승격한다.
8. Plan Batch B와 native Review 독립 검증을 계속해 대체 판정을 갱신한다.
9. README·CLI·로드맵 정합성을 맞춘 뒤 멀티 호스트 동일 fixture로 도구 중립 UX를 검증한다.

## 한 줄 결론

현재 OMC의 다음 제품 전환점은 기능 추가가 아니라, 완성된 bounded N-child 실행이 실제 작업의 성공률을 높이고 시간·token·개입을 줄이는지 독립 receipt로 증명하는 것이다.
