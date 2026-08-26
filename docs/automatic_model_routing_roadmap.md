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

- 입력: 승인된 `3–5` child DAG grant, dependency, prompt, scope, aggregate budget
- 선행 정책: scope normalization과 child `approval_id` 고유성 정책은 완료
- 실행 전 계약: 승인 전 canonical proposal이 graph·prompt·grant·budget·target-bound scope를 결속하고, 승인 시 같은 의미와 expiry를 재검증한 v2 grant만 `scheduler_eligible=true`
- 구현 완료: ready child claim, 제한 병렬 실행, dependency 해제, scope 격리 patch, 별도 DAG·child ledger
- 안전 경계 완료: immutable provider snapshot, hard output bound, call·elapsed·token budget, idempotency·expiry 재검증
- 실패 정책 완료: retry·자동 재분배·fallback·resume 없이 bounded `parent_review`로 전환하고 실패 결과의 patch 적용 차단
- acceptance 계약 완료: 고정 5-case 카탈로그(성공 2·실패·timeout·scope violation), source commit·request hash·receipt·DAG/child ledger 결속, authoritative reload 전용 최종 판정
- 합성 E2E 통과: capability handshake·hard token/output limit·process-group timeout·scope/budget 위반 차단을 고정 fixture로 검증; 이 결과는 운영 표본으로 간주하지 않음
- Product Value preregistration 계약 완료: v1의 prospective 고정 5건과 v2·v3 등록 계약 호환성을 유지한다. v4는 1건 비판정 pilot과 paired confirmatory 5건, 최소 2개 저장소·2개 구현 유형, canonical workload·pair 순서·execution packet·environment receipt 해시, repository alias↔identity 일대일 매핑, 관측 창·등록 authority, provider/model/reasoning snapshot, immutable runner·arm adapter·scheduler·provider adapter bundle, 환경 정책, 실행 한도와 판정 threshold를 `frozen` manifest로 고정한다. scheduler bundle은 import dependency인 executor shadow까지 별도 해시로 결속한다. 이 상태는 `claim_eligible=false`이며 등록 완료나 운영 acceptance를 의미하지 않는다.
- Product Value 중립 등록 검증 경로 완료: Git registry의 exact preregistration record·ancestor와 RFC 3161 claim·authority·관측 시작 전 timestamp·receipt digest를 공통 primitive로 검증하고, Product Value schema v2의 `prepare-v2` → `registry-record` → `prepare-receipt` → `validate-registration` 경로를 연결했다. 정확한 registry anchor와 유효한 receipt를 모두 통과한 경우에만 `claim_eligible=true`다. 실제 외부 registration receipt와 운영 acceptance는 아직 확보하지 않았다.
- Product Value paired acceptance harness 완료: schema v3 호환 경로와 schema v4 manifest·등록 gate를 소비해 각 workload의 OMC/baseline arm을 동일 commit의 격리 clone에서 사전 등록 순서대로 실행한다. production dual-arm adapter는 backend 실행 파일 해시와 provider의 hard token/output capability를 호출 전에 확인하고, OMC arm은 승인된 v2 grant·child prompt·dependency·scope·aggregate budget을 executor shadow가 함께 동결된 bounded scheduler에 전달하며 baseline arm은 같은 provider adapter의 single-agent brief를 사용한다. 실행 bundle hash와 provider attestation을 검증하며 dependency lock·read-only cache inventory·readiness runtime identity를 실행 전후 재측정해 drift·timeout·malformed 입력을 `parent_review`로 보존한다. pilot 성공 전 confirmatory를 차단하고 token·runner 실측 elapsed·개입·review·scope·budget·중복 실행 telemetry와 raw output·verification·DAG/child ledger를 hash-bound artifact로 남긴다. 최종 판정은 저장된 원문과 environment receipt 결속을 authoritative reload해 confirmatory 5쌍의 모든 arm 성공을 전제로 비교하며 합성 E2E는 운영 표본으로 간주하지 않는다.
- Product Value v4 후보 동결 도구 완료: 실제 workload 정확히 6건의 v1 packet·승인 grant/prompt·environment receipt·source identity를 검증해 v3 packet과 immutable 5-file execution bundle hash를 결정론적으로 생성한다. acceptance는 기존 v2 packet 호환성을 유지한다. source commit이 실제 Git object인지 확인하고, 결과는 `candidate_frozen`·`not_registered`·`pending`으로만 원자 저장해 승인·등록 완료를 선점하지 않는다.
- Product Value corpus v2 재구성·실제 생성·승인 완료: 승인된 v1 corpus digest와 source-e/f dependency lock으로 실제 6-workload `product-value-batch-20260826-v2-r1`을 생성했다. 최초 v2는 source-e 실행 surface 불일치로 판정에서 제외해 보존하고, 승인된 source override의 commit·tree·필수 Git object mode를 재결속한 v2-r1으로 교정했다. 필수 경로는 Git tree의 일반 파일·실행 파일·디렉터리만 허용해 symlink·submodule·누락 경로를 fail-close한다. v1 source digest `73407a89...b5a3a`, v2-r1 source digest `0b545d23...918`, public payload hash `0f9b4e46...dc74`, workload `6`건을 validator `PASS`로 확인했고 별도 `corpus-approval.json`에 사용자 승인을 기록했다. 이 승인은 v4 후보 동결 진입을 허용하지만 아직 생성되지 않은 child decomposition·grant·environment receipt를 승인하거나 등록 완료를 의미하지 않는다.
- Product Value freeze 운영 surface 완료: dependency lock은 exact package/version만 허용하고 canonical 정렬하며, workload별 grant·prompt·runtime·read-only cache·직접 surface 검증 파일의 commit 결속 path·hash를 파일 기반 receipt와 v3 execution packet에 결속한다. `product-value-freeze prepare-inputs → prepare → validate` CLI가 v4 candidate, 분할 저장 artifact와 5-file execution bundle을 재계산 검증한다. Product Value 계약 회귀 `118 passed`, 최신 전체 회귀 `2730 passed, 3 skipped`, TDD gate와 diff check를 확인했다. 실제 source-e/f lock 기반 corpus v2-r1 생성은 완료됐고, corpus 승인 후 실행 spec·environment spec·provider snapshot·limits를 결속한 v4 후보 동결부터 남아 있다.
- 외부 등록 전 freeze 보강 완료: source-e/f lock 문법을 정규화된 배포 이름과 숫자 release segment만 허용하는 `name==numeric.release`로 고정해 range·wildcard·marker·extra·URL·symbolic version을 거부한다. `prepare-inputs` CLI surface에서 evidence가 worktree에만 있고 frozen source commit에는 없는 경우를 `freeze_direct_surface_unverified`로 차단하는 전용 회귀를 추가했다. Product Value 핵심 회귀 `86 passed`, 전체 회귀 `2730 passed, 3 skipped`, OMC review `APPROVE WITH NOTES`를 확인했다. 최상위 `scripts/omc.py` subprocess의 전체 인자 전달 회귀는 비차단 테스트 보강으로 남기며 실제 corpus v2 생성보다 우선하지 않는다.
- acceptance 복합 제한 분류 완료: verification timeout과 출력 상한 초과가 동시에 발생해도 timeout return code와 출력 초과 `parent_review` reason·`budget_violations`를 함께 보존한다.
- corpus 준비 완료: implementation completion receipt에서 선정한 실제 구현 workload 6건(1건 비판정 pilot과 동일 조건 paired 5건)을 정답 commit history가 없는 단일-commit 격리 source snapshot으로 재구성하고 request·DoD·verification·교차 실행 순서를 packet hash에 결속했다. private source mapping은 외부 payload에서 분리한다.
- 현재 병목: 실제 corpus v2-r1 생성·validator 검증·사람 승인은 완료됐다. 다음으로 동일 corpus에서 workload별 exact child decomposition proposal을 고정·승인하고 v2 grant를 발급한 뒤, execution spec·environment spec·provider snapshot·limits를 결속해 v4 packet·5-file execution bundle 후보를 동결해야 한다. 이후 exact Git registry record와 외부 RFC 3161 registration receipt를 관측 전에 확보·검증하고 실제 workload 6건을 `run-pilot` → `run-confirmatory` → `finalize` 순서로 실행해 single-agent baseline 대비 제품 가치를 판정한다.
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

## 제품 개선 실행 백로그

Oh My Claude Code `v4.15.10` 비교에서 확인한 운영 증거·자동 복구·자연어 UX 약점을 OMC 제품 축에 편입한다. 경쟁 제품의 mode를 복제하는 대신 Product Value, Operator Experience, Evidence 종료 기준을 직접 충족한다. 경쟁 제품의 토큰 절감 주장과 OMC의 안전성 우위 가설은 동일 작업 직접 측정 전까지 확정 사실로 사용하지 않는다.

| 우선순위 | 개선 축 | 다음 범위 | 종료 기준 |
|---|---|---|---|
| P0 | bounded N-child 운영 검증 | preregistration된 실제 구현 6건의 등록 receipt, 1건 pilot, confirmatory 5건 paired 실행·budget enforcement 증거 | 실제 3–5 child acceptance 통과 |
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

1. 승인된 corpus v2-r1의 workload 6건에 대해 exact child decomposition proposal과 v2 grant를 고정하고, read-only cache fixture·execution spec·environment spec·provider snapshot·limits를 결속해 v4 후보를 `prepare-inputs → prepare → validate`로 동결한다. 이어 exact Git registry record와 외부 RFC 3161 registration receipt를 확보하고 `claim_eligible=true`를 검증한다.
2. authoritative acceptance 진입점으로 pilot 1건을 실행하고 성공 gate를 확인한다.
3. paired confirmatory 5건의 실제 3–5 child 성공·실패·timeout receipt를 수집한다.
4. 같은 작업의 single-agent baseline과 성공률·시간·token·개입 telemetry를 비교하고 `finalize` 판정을 발행한다.
5. 결과를 근거로 Lite/Full 경계를 조정하고 불확실한 요청은 Full로 fail-safe 승격한다.
6. Plan Batch B와 native Review 독립 검증을 계속해 대체 판정을 갱신한다.
7. README·CLI·로드맵 정합성을 맞춘 뒤 멀티 호스트 동일 fixture로 도구 중립 UX를 검증한다.

## 한 줄 결론

현재 OMC의 다음 제품 전환점은 기능 추가가 아니라, 완성된 bounded N-child 실행이 실제 작업의 성공률을 높이고 시간·token·개입을 줄이는지 독립 receipt로 증명하는 것이다.
