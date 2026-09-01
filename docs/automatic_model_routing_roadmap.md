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
| Operator Experience | 진행중 | output contract, Lite/Full routing, Stage graph SSOT, resume identity fail-close, CLI fast-path 구축 | 지연·개입 횟수 운영 검증 |

현재 OMC는 운영 가능한 규칙 기반 코어이며, 고급 오케스트레이션의 제품 가치는 미검증 상태다. 승인된 v2 grant를 제한 병렬 실행하는 `bounded orchestration`과 authoritative acceptance 판정 코드는 갖췄지만 실제 운영 표본 acceptance, 실패 재분배, 자동 모델 전환, 자동 ship은 아직 완료되지 않았다.

### Evidence-state Scorecard

완성도 백분율 대신 코드, 회귀 테스트, 운영 표본, 독립 재현의 증거 단계를 분리한다. Product Value의 claim scope는 `bounded_n_child_execution`이며 다른 스킬이나 전체 OMC의 대체 판정으로 확대하지 않는다.

| 대상 | evidence-state | 현재 근거 | 승격 조건 |
|---|---|---|---|
| Routing V1–V4 | `OPERATIONALLY_VALIDATED` | 라우팅·실패 복구·telemetry 코드와 운영 receipt | 운영 drift 감시 유지 |
| Bounded scheduler | `IMPLEMENTED` | v2 grant 전용 N-child scheduler·provider adapter·회귀 테스트 | 실제 3–5 child acceptance |
| Product Value | `BLOCKED` | acceptance 코드와 등록 계약은 구현됐지만 기존 development evidence 원문이 소실되어 실행 불가 | 별도 prospective development study 6건을 사전 등록·수집·검증한 뒤 disjoint holdout을 새로 계획 |
| Product Value independence | `NOT_REPRODUCED` | 유효한 development evidence 없음 | 신규 development evidence 검증 후 별도 선정한 holdout에서 primary metric 충족 |
| Plan | `NOT_PROVEN` | 단일 저장소 pilot만 존재 | 독립 Batch B와 confirmatory batch 재현 |
| Review | `NOT_PROVEN` | durable native provider 원문 부재 | 동일 diff native 재실행과 blind adjudication |
| Autopilot | `LIMITED` | frozen work contract, 격리 candidate 실행, trusted-base critique/review, candidate branch 전용 promotion까지 fail-close | 외부 provider 실사용 smoke와 운영 latency·개입 acceptance |
| Setup | `OPERATIONALLY_VALIDATED` | 최신 배포 기준 사용처 8곳 `setup --force`·strict audit 통과, 기존 Git 상태 보존 | source freshness와 rollback 회귀 유지 |

과거 6건 corpus에서 `DEVELOPMENT_PASS`가 기록됐지만 manifest·workload inventory·execution packet 원문을 현재 검증할 수 없어 유효한 development evidence로 승계하지 않는다. 구현과 acceptance 계약은 유지하되 새 prospective development study에서 chronological first-N 6건을 다시 확보하고, 그 증거가 검증된 뒤에만 비중복 disjoint holdout을 별도로 계획한다. post-call token은 비교 지표로만 사용하며 strict hard-budget 증거로 취급하지 않는다. Product Value 결과는 Plan, Review 또는 전체 OMC 판정을 변경하지 않는다.

Work Packet prospective feasibility는 **capture-only schema v2 검증 코드 완료 / 실제 수집 0/5** 상태다. 5건 chronological first-N capture는 observation 시작 15분 전에 완료된 RFC 3161 registration, Git registry anchor, 서로 다른 registration·source snapshot·completion collector·executor 키와 custody identity, preregistration에 고정된 source inventory path, registry commit의 후손인 canonical inventory commit, 연속 sequence·entry hash·source snapshot checkpoint chain, source snapshot과 completion ledger의 exact equality, raw request·provider output의 execution receipt 결속을 모두 통과해야 한다. 실패·불확정 study는 서명된 failure receipt로 봉인하며 자동 재시작하지 않고, 새 study는 승인된 restart parent를 명시해야 한다. 완료 artifact는 임시 경로가 아닌 durable evidence root에 원자적으로 게시하고 digest 검증 후 reload한다. 이 계약은 독립적인 작은 운영 표본의 **수집 가능성만** 검증하며 실제 5건이 수집되기 전에는 제품 가치, 품질 projection 또는 Plan 대체 증거로 사용하지 않는다.

### 제품 약점 기반 개선 축

기능 수가 아니라 사용자가 실제 작업을 더 잘 끝내는지를 기준으로 남은 약점을 세 핵심 축과 하나의 지원 축으로 관리한다. Product Value·Operator Experience·Evidence를 핵심 축으로, Maintainability를 이를 지속시키는 지원 축으로 둔다. 구현 완료와 제품 효과 검증을 분리하며, 새 스킬·정책·benchmark fixture 수 증가는 완료 지표로 사용하지 않는다.

| 개선 축 | 우선순위 | 현재 약점 | 다음 범위 | 종료 기준 |
|---|---|---|---|---|
| Product Value | P0 | bounded scheduler는 완성됐지만 실제 다중 child 가치가 미검증 | 실제 3–5 child 운영 acceptance와 single-agent baseline 비교 | 중복 실행·scope·budget 위반 없이 완료하며, baseline 대비 성공률은 같거나 높고 시간·token·개입 횟수는 사전 등록된 개선 기준을 충족 |
| Operator Experience | P1 | 반복 승인·상태 확인·스킬 왕복이 작은 작업의 준비 시간을 키움 | Lite/Full observed 표본에서 단계별 latency·retry·개입 측정 후 안전한 자동 분기 조정 | 품질 gate를 유지하면서 p50/p95·token·사용자 개입 횟수 감소 |
| Evidence | P1 | Plan·Review 품질 우위와 비용 절감이 독립 운영 증거로 확정되지 않음 | single-agent baseline 대비 성공률·시간·token·개입 횟수, durable raw output, blind adjudication 수집 | 사전 등록된 독립 배치의 acceptance를 통과한 지표만 대체·우월 판정에 사용 |
| Maintainability | P2 | public/research CLI 경계와 setup 배포 SSOT는 정리됐지만 상태 수명주기·source freshness와 검증 도구 규모가 여전히 사용자 신뢰를 저해 | stale session과 운영 파일 기반 거짓 source drift 교정, 멀티 호스트 동일 fixture 검증 | README·CLI·로드맵 상태가 일치하고 일반 사용 경로가 setup·task·autopilot·status·ship 중심으로 동작하며 완료 상태와 freshness가 실제 Git·run 상태와 일치 |

Operator Experience의 반복 커밋 확인 병목은 `2026-08-31`에 코드 계약을 닫았다. 리뷰에서 이미 제시한 동일 범위 local commit 선택은 현재 confirmed session·TTL·선택 path·blob과 정확한 staged tree에 결속된 authorization으로 pre-commit에서 한 번만 검증하고, 실제 commit tree가 일치할 때 post-commit에서 receipt를 한 번 소비한다. 범위·내용·세션·만료가 달라지면 fail-close하며, 실패한 재검증은 이전 authorization을 즉시 폐기해 `--no-verify` 뒤 만료 receipt가 소비되는 경로도 막는다. push·PR·deploy 권한은 상속하지 않는다. 실제 post-commit hook 통합 테스트를 포함한 관련 회귀 `178 passed`, 문법 검사, staged TDD gate와 OMC review `APPROVE`를 확인했다. 이는 반복 확인 한 종류를 제거한 구현 완료 근거이며, Lite/Full 운영 표본의 p50/p95·token·전체 사용자 개입 감소를 증명한 것은 아니다.

운영 증거 없는 자동화 확대 금지를 공통 원칙으로 둔다. 실제 병목을 줄이지 않는 새 추상화, 정책, 스킬 추가는 위 종료 기준보다 우선하지 않는다.

### Real-use Product Observation

제품 가설은 `bounded N-child` 자체가 아니라 OMC가 실제 개발 작업에서 완료 신뢰성과 운영 부담의 절대 수용 기준을 함께 충족하는지다. 동일한 자연 작업 cohort를 사용하되 Completion Reliability와 Operator Experience를 독립 판정 축으로 분리하고, 두 detached outcome receipt와 최종 사용자 sign-off가 모두 유효할 때만 `OPERATIONAL_SAMPLE_READY`를 허용한다. 기존 workflow 대비 개선·우월성 delta는 이 연구에서 주장하지 않는다.

- v1 종료: canonical v1 SHA-256 `16aa508b5ec85f302e6d157895bf5e31bfb5d7007f9aaba3c449a4cbff45bc2c`는 변경하지 않는다. 별도 write-once `docs/real_use_product_observation_v1_supersession.json` SHA-256 `ec9bc00685affc857533c2b50bd22e8d11a6e1993f2fe07083510b2fc959bc5d`가 관찰 시작 전 `superseded_before_observation`, 실제 candidate `0건`, observation 불가를 기록하며 기존 표본·outcome은 v2로 승계하지 않는다. write-once authority는 roadmap hash binding과 같은 commit에 포함된 immutable Git blob이다.
- v2 상태: `docs/real_use_product_observation_preregistration_v2.json`은 SHA-256 `eb7738f82c18f304046315774c706a2c4e392b1eb9b26c8142e75ca7f1bb0841`의 `draft_unregistered` 계약이다. source commit·observer와 두 validator hash·exact window·immutable Git registry record·RFC 3161 receipt·3개 저장소 enrollment receipt가 비어 있으므로 현재 `claim_eligible=false`, `observation_allowed=false`다.
- 표본: 대상 저장소는 `sixshop3-storefront-fe`, `market-reasoning-engine`, `research-auto`다. enrollment 이후 전체 OMC session state stream을 모집단으로 삼아 chronological first-N 6건을 최소 2개 저장소에서 선택하고 교체하지 않는다. observer stream은 session baseline checkpoint와 전체 session inventory를 전수 대조하며 누락·후보 건너뛰기·mutating session 중첩이 있으면 사용자 작업은 계속하되 연구는 `OBSERVATION_INCONCLUSIVE`로 닫는다.
- Completion 기준: verification과 사용자 수용이 결속된 완료 `5/6 이상`, manual takeover `1/6 이하`, abandonment·missing evidence·skipped candidate·overlapping mutating session은 각각 `0`이어야 `COMPLETION_SAMPLE_READY`다.
- Operator 기준: comparative baseline은 `none_by_design`이며 우월성 주장을 금지한다. durable recorded decision 중앙값 `2 이하`, case별 friction acceptable `5/6 이상`, manual takeover `1/6 이하`, abandonment·observer event 누락은 각각 `0`, session-start와 terminal observer p95는 각각 `100ms 이하`여야 `OPERATOR_EXPERIENCE_SAMPLE_READY`다. decision은 terminal의 authoritative count를 eligibility부터 terminal까지의 unique durable receipt와 대조하며, 모든 selected task의 complete stream marker가 true여야 한다. false·누락은 count와 무관하게 Operator 축을 inconclusive로 닫고 `0건`도 complete marker가 있을 때만 유효하다. 짝수 중앙값은 가운데 두 값의 산술평균, p95는 nearest-rank로 계산한다. observer·decision metric은 user sign-off 전 terminal에서 마감하고 friction만 terminal 이후 final user signer의 case별 boolean sign-off에서 수집한다. task wall time은 report-only이며 누락돼도 축 판정에 영향을 주지 않는다.
- authority: observer collector, completion validator, operator validator, final user signer를 분리하고 key 재사용을 금지한다. collector는 outcome receipt를, validator는 최종 user sign-off를 발행할 수 없다.
- 활성화 순서: observer·validator 구현과 200회 surface별 preflight 통과 → source commit 동결 → v2 exact window와 source hash 확정 → immutable registry와 RFC 3161 등록 → 동일 source 설치·strict audit → 저장소별 enrollment → 마지막 enrollment 이후 최소 24시간 buffer 순서다. exact 14일 뒤 자동 연장 없이 종료하며 어느 단계도 생략하거나 사후 보정하지 않는다.
- 경계: invalid registration/source와 inventory 누락·후보 건너뛰기·중첩 session은 먼저 `OBSERVATION_INCONCLUSIVE`, 표본 6건 또는 저장소 2개 미달은 `LOW_NATURAL_DEMAND`, 완전한 evidence에서 threshold 미달은 축별 NOT_READY로 판정한다. 정확히 한 축만 READY이고 다른 축이 NOT_READY면 `OBSERVATION_INCONCLUSIVE`, 두 축 모두 NOT_READY면 `PRODUCT_WORKFLOW_NOT_READY`다. Product Value development·holdout, Plan·Review 대체 판정, 비교 우월성 증거로 자동 승격하지 않는다.

### Product Value P0 evidence-loss 종료 승인 대기와 신규 prospective study

- 상태 보고: 전체 완성도 백분율을 사용하지 않는다. 구현·검증 준비·운영 검증·독립 재현 evidence-state를 대상별로 보고한다.
- 기존 종료 상태: `product-value-batch-20260826-v5-r1`과 preregistration `69115b41210a14b42ea9096bf3cea98c8897a2047b5bc0a322e5f7a64c2af8df`는 manifest·workload inventory·execution packet 원문을 복구하지 못했으므로 `2026-08-30`에 `BLOCKED` / `evidence_loss` 종료 승인 대기로 전환했다. schema v1 Git registry blob, closure subject, Ed25519 authority receipt를 결속하고 no-replace marker로 acceptance 재개를 차단하는 fail-close 경로는 구현·검증했다. 실제 승인된 signer identity·서명·durable failure receipt 게시를 검증한 뒤에만 종료 완료로 승격한다. 기존 `2026-09-05` 최종 판정 기한은 연장하지 않는다.
- 복구 금지: 기존 batch의 manifest·workload inventory·execution packet을 추정하거나 재구성하지 않는다. hash-only registry record와 임시 진단 receipt는 실행·판정 입력으로 승격하지 않는다.
- study 분리: 신규 study는 기존 batch의 retry 또는 continuation이 아니다. 새 evaluation ID·selection policy·source universe·authority commitment·registration lineage를 사용한다.
- `2026-08-31`: selection policy·source universe·authority commitment를 observation 전에 등록한다.
- `2026-09-01`부터 `2026-09-07`: chronological first-N development case 6건을 수집하고 각 case의 source snapshot·inventory·completion evidence를 즉시 봉인한다.
- `2026-09-08`: schema v5 registration과 durable evidence bundle을 생성·검증한다.
- `2026-09-09`: development evidence 검증 후에만 별도 holdout 계획을 열 수 있다.
- authority 분리: source snapshot signer·preregistration signer·registration authority·inventory collector는 서로 다른 key·operator·custody identity를 사용하며 key 재사용 또는 provenance 불일치는 fail-close한다.
- 허용 범위: failure receipt, 신규 development preregistration, chronological capture, registration, durable evidence 검증과 이를 막는 최소 결함 수정만 허용한다.
- 금지 범위: development evidence 검증 전 provider 호출과 holdout 실행을 금지하며 신규 schema·transport·benchmark fixture를 추가하지 않는다.
- claim 제한: 신규 study는 development evidence만 생성한다. 결과를 Plan·Review 또는 전체 OMC의 대체·우월 증거로 사용하지 않는다.

### Operational P0

**bounded N-child 실제 acceptance**를 단일 운영 최우선 작업으로 둔다.

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

**P0 종료 기준**

- 실제 3–5 child 작업을 중복 실행·범위 침범·예산 초과 없이 완료한다.
- 실패·timeout은 동일한 `parent_review` 계약으로 수렴한다.
- baseline 대비 성공률은 같거나 높고 시간·token·개입 횟수는 사전 등록된 개선 기준을 충족한다.
- operational pilot은 등록된 `subscription_bounded` conformance를 통과하면 열 수 있다.
- strict certification은 `provider_enforced` backend가 adversarial conformance와 disposable shadow execution receipt를 통과한 경우에만 평가한다.

### Operational Obligation

**Plan Batch B receipt 수집**은 Implementation P0와 별개로 중단 없이 병행한다.

- 등록된 관측 창: `2026-08-20`부터 `2026-09-18`
- 수집 계약: lock-backed implementation receipt, 최소 3개 저장소, 저장소별 최대 5건, 전체 최대 15건
- 현재 감사 상태: raw provisional receipt `7건/3개 저장소`, cap 초과 `3건` 제외, source snapshot 검증 전 `validated_eligible=0`
- 금지: 관측 창 사후 연장, quota 변경, synthetic·document·benchmark maintenance 혼입
- 다음 단계: 관측 종료 후 source snapshot 동결, universe·shortlist 10건, 독립 gold sign-off, paired 실행, blind adjudication

**Work Packet 5건 feasibility**는 Batch B와 분리된 진단 lane으로 병행한다.

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
- 회귀 검증: 관련 테스트 `411 passed, 1 skipped`, 전체 테스트 `2582 passed, 3 skipped`, TDD gate 통과.
- 목표: 품질 gate를 유지하면서 반복 확인, p50/p95 지연, input/output/total token을 줄인다.
- 현재 유효 latency 표본은 history의 운영 기록을 따르며 표본 기준 충족 전 라우팅 경계를 확정하지 않는다.

### Setup Distribution Integrity

- 설치 receipt schema v3가 배포 파일을 `exclusive_managed`·`merged_host`·`preserved`·`manual_review`로 분류한다.
- `setup-ignore`가 OMC 전용 파일만 literal pathspec으로 Git 추적에서 제외하고 로컬 파일을 보존하며, migration receipt 기반 rollback을 제공한다.
- setup이 생성하는 OMC ignore block은 공유 `.gitignore`가 아니라 `git rev-parse --git-path info/exclude`로 찾은 repository-local exclude에 기록한다. 예약된 OMC namespace만 wildcard로 압축하고 일반 경로는 literal rule로 유지하며, linked worktree와 비 Git 대상의 동작을 분리한다.
- 설치 SSOT는 고정된 `omc_kit/` 경로가 아니라 `.omc/install-source.json`의 `source_path`로 통일했다. 저장소에 추적되던 legacy `omc_kit/scripts` 복사본과 nested installer fallback은 제거했다.
- active migration은 legacy schema v1/v2의 `.gitignore` 결속을 유지하고 schema v3부터 local exclude hash에 결속한다. receipt가 손상되거나 결속된 ignore surface가 달라지면 manifest 생성과 파일 변경 전에 exit code `2`로 fail-close한다.
- receipt schema v3를 install audit·version 판정이 인식하며, legacy migration receipt v1의 안전한 v2 승격까지 지원한다.
- local-exclude 전환은 관련 회귀 `185 passed`, 핵심 setup/gitignore 테스트 `126 passed`, staged TDD gate와 fresh setup smoke를 통과했다. smoke에서 공유 `.gitignore`는 그대로 유지됐고 exclusive receipt 214개 경로는 86개 rule로 압축됐다(59.8% 감소).
- 실제 사용처 8곳은 `55252ec` 기준 `setup --force` 재배포와 strict install audit `8/8`을 통과했다. 모든 대상에서 `installed_integrity_status=ok`, `core_usage_readiness=ready`, `source_freshness_status=up_to_date`, `verification_status=ok`를 확인했고 설치 전후 Git 상태가 동일해 기존 로컬 작업도 보존됐다. 소스 `omc_kit` 자체는 consumer inventory에서 제외했으며 비 Git 대상 1곳은 completion hook을 `not_applicable`로 유지했다.

## 로드맵 검증 매트릭스

| 로드맵 완료 항목 | 실제 반영 증거 | Fugu 비교에 쓰는 축 | 판정 규칙 |
|---|---|---|---|
| V1~V4 routing | 코드·회귀 테스트·운영 telemetry | routing·cost·recovery | 문구만 있으면 `문서만 반영`, 코드와 테스트가 있으면 `반영 확인` |
| Operator Experience | observed request·latency·intervention evidence | 사용자 개입·지연 | 운영 표본까지 acceptance를 통과해야 `체감 개선 확인` |
| V5 orchestration | grant·scheduler·실제 child execution receipt | 자동 분해·위임 | grant만으로 실행 완료를 주장하지 않으며 실제 acceptance가 필요 |

Fugu 비교 문구는 `현재 상태 참조`와 `반영 검증 완료`를 구분한다. 경쟁 제품의 문서 주장과 OMC의 구현 근거도 같은 증거 수준처럼 혼합하지 않는다.

## 실행 우선순위

`Real-use Product Observation → P0 Product Value acceptance → P1 Operator Experience 측정·조정 → P1 Evidence 독립 검증 → P2 유지보수·멀티 호스트 UX` 순서로 고정한다. 경쟁 제품의 mode를 복제하거나 새 스킬을 늘리는 작업은 이 acceptance를 앞당길 때만 수행한다.

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

1. **완료** — 기존 schema v1 Product Value batch를 실행 대상에서 제외하고 실제 6개 workload의 source commit·request·DoD·verification·environment artifact를 corpus v2-r1로 다시 수집했다. availability preflight가 6건 모두를 provider 호출 없이 `ready`로 확인한다.
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
13. **public CLI 경계 완료 / 상태 신뢰성·멀티 호스트 검증 대기** — 루트 help는 setup·task·status·review·ship 핵심 흐름과 orchestrate·autopilot·team advanced 흐름을 우선 표시한다. Product Value·N-child research 명령은 직접 호출 호환성을 유지한 채 루트 help에서 숨겼고 README 계약과 CLI 회귀 테스트를 고정했다. 다음은 stale active session과 `.omc` 운영 파일 기반 거짓 source drift를 교정한 뒤 멀티 호스트 동일 fixture로 도구 중립 UX를 검증하는 것이다.
14. **Autopilot 안전 실행 코드 완료 / 외부 smoke 대기** — frozen work contract, 격리 task workspace, trusted-base critique/review, immutable review packet과 candidate branch 전용 promotion을 고정했다. 다음은 고정 커밋의 격리 clone에서 실제 provider TASK→CRITIQUE→REVIEW smoke를 실행해 candidate 변경 보존, reviewer control-plane 비오염, 실패 시 promotion 차단, latency·token·사용자 개입 receipt를 함께 확인하는 것이다.

## 한 줄 결론

현재 OMC의 다음 제품 전환점은 기능 추가가 아니라, 자연 발생 작업에서 OMC가 반복 사용될 가치가 있는지 먼저 확인하고 그 gate를 통과한 뒤 bounded N-child가 성공률·시간·token·개입을 개선하는지 독립 receipt로 증명하는 것이다.
