# Task Review Product Focus Pilot

## 목적과 범위

이 문서는 OMC의 첫 제품 범위인 복잡한 코드 변경의 `task → review` 흐름을 Baseline과 비교하는 최소 실행 SSOT다. 이 3건 pilot은 kill-or-continue 운영 판단용이며 통계적 우월성이나 Codex 전체 대체를 주장하지 않는다. provider 실행은 이 문서 작성 범위에 포함하지 않는다.

T0는 사용자가 `task_review_pilot_start` 결정을 승인하고 그 `pilot-start receipt`가 소비된 `consumed_at`이다. T0부터 최대 7일 동안 최소 2개 저장소에서 발생한 `chronological first eligible 3`을 사용하고, 탈락하거나 불리한 case를 교체하지 않는다. 첫 3건이 한 저장소에 집중되면 이후 case로 다양성을 맞추지 않고 `STOP_ELIGIBILITY_DIVERSITY`로 종료한다. 적격 case는 T0 이후 시작된 실제 자연 발생 implementation 작업이며 합성 fixture, 문서 전용 작업, benchmark 유지보수는 제외한다.

T0 전에는 참여 저장소 roster를 먼저 동결한다. 각 identity는 credential과 protocol을 제거한 canonical `origin`과 단일 root commit의 SHA-256이며 local-only 저장소, 중복 identity, 사후 remote/root 변경은 차단한다. roster는 저장소별 `.omc/state` 위치와 마지막 `(created_at, session_id)` checkpoint를 포함하고 `pilot contract hash`, source commit과 함께 승인 decision에 결속한다. consumed decision 원문과 roster는 `.omc/state/task-review-pilot/<pilot-id>/`에 no-replace로 보존한다.

실행 전 `scripts/omc_task_review_pilot.py`의 readiness preflight인 `prepare-roster → inventory-dry-run → readiness`를 적용한다. collector는 기존 `session state stream`의 `session.json`과 `completion.json`만 읽고 checkpoint 이후 stream 전체를 검사한다. 시작과 완료 work class가 모두 `implementation`이어야 하며 baseline/followup의 실제 Git diff가 receipt의 changed paths와 일치해야 한다. 누락·혼합·불일치는 자동 보정하지 않고 `classification_review_required` 또는 명시적 부적격 사유로 남긴다. dry-run은 scanned session IDs, 저장소별 terminal cursor, disposition과 inventory hash를 저장하며 provider call 수는 항상 0이다.

roster, consumed T0 receipt, inventory가 같은 hash로 결속되고 first eligible 3건과 저장소 다양성을 만족할 때만 `PILOT_READY` receipt를 발행한다. readiness 이전에는 paired 실행을 시작하지 않는다. 이 검증은 기존 state evidence를 소비하며 provider runner를 만들지 않는다.

## Frozen Case

각 case는 실행 전에 다음 값을 하나의 case receipt로 고정한다.

- request, base commit, DoD, verification command
- provider, model, reasoning, timeout
- 저장소 identity와 dependency 준비 조건

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
