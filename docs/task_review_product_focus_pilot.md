# Task Review Product Focus Pilot

## 현재 판정

contract revision 7은 calibration에서 자유서술 문구가 아닌 구조화 판정만 비교하고 fixture에 정답 boundary를 노출하지 않는다. 또한 네 authority의 고유 키·정보 접근 경계, T0 전 calibration qualification과 표본 제외 protocol rehearsal, `INCONCLUSIVE` 뒤 동일 study 연장·표본 합산 금지를 registration v3에 봉인한다.

공동 서명 registration은 machine-readable preregistration의 hash와 contract revision, 30% 최소 감소율, baseline correction event 최소 3건을 직접 포함해 판정 계약까지 동결한다. revision 3부터 primary metric은 실제 대화 횟수가 아니라 익명 evaluation packet을 본 blind adjudicator가 기록한 correction-required case rate다. revision 4는 anonymous artifact 본문 어디서든 금지 identity token을 대소문자와 wrapper에 무관하게 차단하고, persona fidelity를 동결된 criterion별 pass·evidence와 그 논리곱으로 판정한다. requirement coverage·persona fidelity·DoD completeness·incorrect completion도 quality non-inferiority gate로 판정한다.

외부 custody의 canonical payload 생성·서명·최종 decision 재검증 절차는 [Persona Pilot Operator Runbook](task_review_persona_operator_runbook.md)을 따른다. 모든 terminal outcome은 registration·mapping·enrollment·terminal·adjudication sealed bundle과 `decision_sha256`을 보존한다.

pilot v2는 `ARCHIVED_INCOMPLETE`로 동결한다. roster와 T0는 보존됐지만 readiness, paired provider execution, terminal receipt, decision receipt가 없어 제품 효과를 판정할 수 없다. v2는 재개·보정·case 추가 없이 reconciliation과 감사용으로만 보존하며, v2의 T0·roster·inventory·readiness·receipt를 승계하지 않는다.

사용자가 승인한 다음 검증은 `task-review-persona-effectiveness-20260904-v1`이다. 여러 레포를 운영하는 1인 SaaS 창업자의 자연 발생 implementation 10건 paired case에서 direct Codex와 OMC persona 경로를 비교한다. 공식 claim은 blind 평가에서 correction-required case 비율 30% 이상 감소하는지이며 실제 일상 사용자의 수정 지시 30% 감소는 아직 증명하지 않는다. completion과 verification pass rate 및 blind quality는 악화되면 안 된다. 실제 provider 실행은 아직 시작하지 않았다.

실행은 새 adapter 개발 대신 external Codex executor의 수동 signed receipt 프로토콜을 사용한다. case 1 전에 calibration qualification과 four-authority synthetic rehearsal을 완료하고, study·reconciliation authority가 함께 서명한 registration으로 T0, 21일 deadline, canonical origin+root commit 저장소 roster, chronological first eligible implementation 10건·무대체, 최소 2개 저장소·저장소당 최대 7건, arm mapping hash, 고유한 네 authority key와 판정 임계값을 고정한다. 10건을 미리 기다리지 않고 각 자연 작업마다 `enrollment → persona-freeze-case → persona-paired-dry-run → execution → terminal → anonymous evaluation packet`을 즉시 수행한다. enrollment는 직전 enrollment hash와 state stream cursor 구간을 결속하며, 해당 구간 원문을 `O_NOFOLLOW`로 다시 열어 먼저 나타난 적격 작업을 건너뛰지 않았는지 검증한다. persona frozen case와 dry-run은 v2 readiness를 재사용하지 않고 registration·현재 enrollment hash를 signed execution receipt와 terminal bundle까지 계승한다. case 10 뒤 decision은 enrollment chain과 terminal의 repository·base commit·registration hash, packet과 terminal evaluation artifact의 canonical equality를 다시 대조한다. 기존 v2 `paired-dry-run`과 3건 readiness 계약은 archive 상태로 동결한다. primary correction event는 anonymous packet을 본 blind adjudicator가 frozen request·persona contract·DoD·verification command를 충족하려면 추가 코드 변경이 필요하다고 판정하고 비어 있지 않은 correction instruction을 기록한 case다. 실제 일상 사용자의 수정 지시 감소는 이 study의 claim이 아니다. adjudicator는 실제 arm identity를 모른 채 `arm_a`/`arm_b`의 requirement coverage, persona fidelity criterion, DoD completeness, incorrect completion, correction-required와 강제 arm 추측을 v4 receipt에 서명한다. `CONTINUE`는 blind correction-required case 30% 이상 감소뿐 아니라 completion·verification·총 사람 개입·blind quality·median wall-clock(+15% 이내)의 비열화를 모두 요구한다. 유효하지 않은 운영 입력은 구조화된 `blocked`와 exit 2로 차단한다. 21일 안에 10건을 채우지 못한 경우에는 reconciliation authority가 registration hash·현재 enrollment count·마지막 enrollment hash·deadline 이후 observed time을 묶은 collection-close receipt를 서명해야만 `INCONCLUSIVE`로 종결한다. 이 결정은 검증된 close receipt 전체와 canonical hash를 함께 봉인하여 deadline 근거를 다시 검증할 수 있어야 한다. 정확한 machine-readable 계약은 [persona-effectiveness preregistration](task_review_persona_effectiveness_preregistration_v1.json)을 SSOT로 삼는다.

## v2 보존 계약

아래는 OMC의 기존 3건 `task → review` v2 계약을 변경하지 않고 보존한 archive다. 이 3건 pilot은 kill-or-continue 운영 판단용이었으며 통계적 우월성이나 Codex 전체 대체를 주장하지 않는다. provider 실행은 이 문서 작성 범위에 포함하지 않는다.

T0는 사용자가 `task_review_pilot_start` 결정을 승인하고 그 `pilot-start receipt`가 소비된 `consumed_at`이다. T0부터 최대 7일 동안 최소 2개 저장소에서 발생한 `chronological first eligible 3`을 사용하고, 탈락하거나 불리한 case를 교체하지 않는다. 첫 3건이 한 저장소에 집중되면 이후 case로 다양성을 맞추지 않고 `STOP_ELIGIBILITY_DIVERSITY`로 종료한다. 적격 case는 T0 이후 시작된 실제 자연 발생 implementation 작업이며 합성 fixture, 문서 전용 작업, benchmark 유지보수는 제외한다.

T0 전에는 참여 저장소 roster를 먼저 동결한다. 각 identity는 credential과 protocol을 제거한 canonical `origin`과 단일 root commit의 SHA-256이며 local-only 저장소, 중복 identity, 사후 remote/root 변경은 차단한다. roster는 저장소별 `.omc/state` 위치와 마지막 `(created_at, session_id)` checkpoint를 포함하고 `pilot contract hash`, source commit과 함께 승인 decision에 결속한다. consumed decision 원문과 roster는 `.omc/state/task-review-pilot/<pilot-id>/`에 no-replace로 보존한다.

실행 전 `scripts/omc_task_review_pilot.py`의 readiness preflight인 `prepare-roster → inventory-dry-run → readiness`를 적용한다. collector는 기존 `session state stream`의 `session.json`과 `completion.json`만 읽고 checkpoint 이후 stream 전체를 검사한다. 시작과 완료 work class가 모두 `implementation`이어야 하며 baseline/followup의 실제 Git diff가 receipt의 changed paths와 일치해야 한다. 누락·혼합·불일치는 자동 보정하지 않고 `classification_review_required` 또는 명시적 부적격 사유로 남긴다. dry-run은 scanned session IDs, 저장소별 terminal cursor, disposition과 inventory hash를 저장하며 provider call 수는 항상 0이다.

roster, consumed T0 receipt, inventory가 같은 hash로 결속되고 first eligible 3건과 저장소 다양성을 만족할 때만 `PILOT_READY` receipt를 발행한다. readiness 이전에는 paired 실행을 시작하지 않는다. 이 검증은 기존 state evidence를 소비하며 provider runner를 만들지 않는다.

## v2 증거 상태

pilot v2는 source commit `b0d62fcf66a54ac9f077afea46191880ad5e8dc7`, roster hash `609a575b9f9a58cf9e7b32b604c4f14f8d89cd277b3ab3f00e33f42aeb082933`, T0 `2026-09-03T16:34:19+09:00`에 결속됐다. 그러나 현재 선언된 local v2 state의 inventory는 `WAITING_FOR_CASES`이며 선택 case는 비어 있다. readiness receipt가 아직 없으므로 `PILOT_READY`가 아니다. provider 호출, paired arm terminal receipt와 decision receipt도 아직 없다. 수동 execution packet은 독립 anchor와 provider receipt가 확보되기 전까지 `MANUAL_CHECKLIST_ONLY`이며 `CONTINUE`, `REDUCE`, `STOP`의 근거로 사용하지 않는다.

실행 capability matrix는 `scripts/omc_task_review_pilot.py capability-matrix`로 생성한다. 입력 repository는 실행 스크립트가 속한 OMC Git root와 같아야 하고, tracked source가 clean하며 전달한 `source_commit`이 그 repository의 `HEAD`와 일치해야 한다. 이 gate는 실행 전 provenance만 보장하며 paired arm·provider session·terminal receipt 또는 pilot 종료 판정을 대체하지 않는다.

v2 artifact의 local 관찰은 `prepare-reconciliation`으로 declared root의 regular-file manifest와 execution evidence schema를 subject로 만든 뒤, operator custody의 **별도** reconciliation authority가 서명한 receipt만 `record-reconciliation`으로 게시한다. `OMC_TASK_REVIEW_PILOT_TRUSTED_RECONCILIATION_PUBLIC_KEY`는 executor trust anchor와 분리하며, 선언 root가 완전하지 않으면 `LOCAL_ARTIFACT_SNAPSHOT_INCOMPLETE`, 모든 선언 root에서 readiness·terminal·decision evidence가 없으면 `NO_EXECUTION_EVIDENCE_IN_DECLARED_ROOTS`만 기록한다. 이는 선언하지 않은 외부 root나 실제 provider 실행의 부재를 주장하지 않으며, 서명 receipt가 발행되기 전에는 v2 상태를 변경하지 않는다.

## Frozen Case

각 case는 실행 전에 다음 값을 하나의 case receipt로 고정한다.

- request, base commit, DoD, verification command
- provider, model, reasoning, timeout
- 저장소 identity와 dependency 준비 조건

실행 권한은 case 입력에 포함하지 않는다. T0 승인 binding에 `execution_authority`(executor 공개키와 그 hash)를 먼저 고정하고, 이를 포함한 readiness v2만 paired 실행에 사용할 수 있다. 실행 전에 operator custody에서 `OMC_TASK_REVIEW_PILOT_TRUSTED_EXECUTION_PUBLIC_KEY`를 설정하며, readiness·frozen case·dry-run의 executor 공개키가 이 값과 다르면 모두 거부한다. 따라서 receipt 자체의 self-hash는 무결성 검증일 뿐 trust anchor가 될 수 없다. legacy readiness v1은 관찰 증거로 보존하되 실행 입력으로는 거부한다. 각 arm은 signed execution receipt 파일의 상대 경로와 파일 hash를 arm receipt에 결속하며, terminal은 해당 파일을 `O_NOFOLLOW`로 다시 열어 파일 hash, receipt hash, 공개키 서명, frozen configuration과 raw output을 모두 재검증한다. terminal은 검증한 dry-run과 두 arm receipt를 sealed bundle로 보존하고, `decide --readiness <readiness.json>`은 이를 다시 열어 해당 readiness의 roster·inventory·T0 binding과 일치할 때만 최종 판정한다.

준비 상태 또는 dependency가 arm 사이에서 다르거나 frozen evidence를 복구할 수 없으면 품질 실패로 세지 않고 전체 pilot을 `INCONCLUSIVE`로 종료한다.

## Paired Arms

두 arm은 별도 격리 clone에서 실행하며 상대 arm의 출력, review, patch, terminal receipt를 노출하지 않는다. 같은 provider, model, reasoning, timeout, verification command와 review 횟수를 사용하고 arm별 재시도 1회만 허용한다. 순서 효과를 줄이기 위해 case 1·3은 OMC 먼저, case 2는 Baseline 먼저 실행한다.

### OMC

1. frozen request와 base commit으로 격리 clone을 materialize한다.
2. `$omc-task`를 한 번 실행한다.
3. 실패 시 동일 입력으로 한 번만 재시도한다.
4. 별도 `$omc-review`를 한 번 실행한다.
5. frozen verification command를 실행한다.
6. 결과와 측정을 terminal receipt로 봉인한다.

### Baseline

1. 같은 frozen request와 base commit으로 별도 격리 clone을 materialize한다.
2. OMC skill/state injection 없이 직접 구현을 한 번 실행한다.
3. 실패 시 동일 입력으로 한 번만 재시도한다.
4. 별도 native Codex review를 한 번 실행한다.
5. 같은 frozen verification command를 실행한다.
6. 결과와 측정을 terminal receipt로 봉인한다.

## 측정 계약

- `completion`: DoD와 verification이 모두 통과하고 해당 arm review가 `APPROVE` 또는 `APPROVE WITH NOTES`일 때만 완료다. machine output을 소비할 때는 두 verdict에 대응하는 `outcome=approved`를 사용한다.
- `review normalization`: OMC arm은 기존 output contract의 `stage=review` envelope를, Baseline arm은 기존 `native review adapter` verdict를 사용한다. 빈 출력, 파싱 실패, 상충 evidence는 승인으로 보정하지 않고 전체 pilot을 `INCONCLUSIVE`로 종료한다.
- `end-to-end wall-clock`: 격리 clone materialization부터 terminal receipt까지의 경과 시간이며 primary 시간 지표다.
- `workflow time`: 첫 provider call부터 terminal receipt까지의 경과 시간이며 secondary 지표다.
- `user intervention`: case 적격 판정부터 terminal까지 작업 진행에 필요했던 실제 사용자 응답 turn의 고유 개수이며 case별 최대 3회다. T0 승인과 최종 채택 결정은 제외한다.
- `rework`: 첫 구현 응답 이후 verification 또는 review 실패 때문에 발생한 추가 구현 시도 횟수다.
- `fatal violation`: 잘못된 저장소나 base 사용, scope 이탈, verification 누락, 핵심 요구사항 누락, arm 간 정보 누출 또는 중복 실행이다.

측정 receipt가 없거나 서로 결속되지 않으면 해당 case만 보정하거나 교체하지 않고 전체 pilot을 `INCONCLUSIVE`로 종료한다.

## 종료 판정

- `CONTINUE`: 3건 중 2건 이상 완료하고 Baseline보다 completion이 낮지 않다. end-to-end wall-clock과 user intervention 중앙값이 모두 악화되지 않으며 둘 중 하나 이상이 15% 이상 개선되고, rework가 증가하지 않으며 fatal violation이 없다.
- `REDUCE`: completion은 유지하지만 효율 기준을 충족하지 못하거나 case 하나라도 user intervention 3회를 초과하면 `REDUCE`로 종료한다. 실행을 위해 새 schema, transport, runner가 필요한 경우도 동일하며 제품 범위를 단일-agent `task → review` guard로 축소한다.
- `STOP`: completion이 Baseline보다 낮거나 fatal violation이 발생하거나 최대 7일 안에 적격 3건이 모이지 않는다.

`INCONCLUSIVE`는 `CONTINUE`, `REDUCE`, `STOP`으로 바꾸지 않으며 replacement case를 허용하지 않는다.

## 사람의 결정

pilot 실행은 T0의 명시적 사용자 승인 뒤에만 시작한다. 결과가 `CONTINUE`여도 원본 저장소에 자동 적용하지 않는다. 사용자가 선택한 arm만 별도 작업에서 적용하며 push, PR, 배포는 각각의 기존 승인 계약을 따른다.
