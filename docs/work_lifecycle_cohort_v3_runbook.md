# Work-lifecycle cohort v3 운영 전환

대상은 승인된 ai-cs와 sixshop3-storefront-fe 두 저장소다. 기존 activation은 운영 등록됐으며, 아래 업데이트 전환 도구의 로컬 검증과 실제 재배포·재등록·T0 시작은 구분한다.

## 계약

Candidate 정책 `omc-candidate-policy/v2`는 `.omc/skill-effectiveness-cohort-v3.json`과 `.omc/skill-effectiveness-cohort-v3.jsonl` 두 경로만 관찰 runtime artifact로 제외한다. tracked/untracked 및 commit 전후에 동일하게 적용하며 `.omc/` 전체, 제품 코드·테스트·OMC 정책·구현은 제외하지 않는다. 구정책 review receipt는 자동 승계하지 않고 재리뷰한다. `validate-ship`의 READY는 reviewed candidate 동일성이지 관찰 설정·원장 유효성 판정이 아니다. 관찰 config/ledger 오류는 v3 검증에서 실패하고 관찰만 무효화되며 별도 보고한다. 새 ship gate는 추가하지 않으며 파일 제외가 관찰 계약 변경 권한을 부여하지 않는다.

- raw-free: work/session ID, event hash, 스킬 노출, review verdict/taxonomy, choice와 사용자 보고 outcome만 기록한다. 원문 요청·코드·경로·검증 출력은 원장에 넣지 않는다.
- 작업 단위는 `work_id`이며 같은 work의 task/review 후보를 여러 skill exposure로 계산한다. 단일 스킬 조건으로 리뷰·후속을 제외하지 않는다.
- 핵심 지표는 `review_count`, `review_churn_work_items`, `review_stale_count`, `correction_after_approved_review`다. churn은 리뷰 2회 이상인 작업 수이며, 승인 후 correction은 후속이 직접 연결된 최신 review가 `APPROVE` 또는 `APPROVE_WITH_NOTES`인 경우다.
- `work_items`, `skill_exposures`, `review_unobserved`, `outcome_unobserved`, capture 실패는 관측 누락을 설명한다. 누락·침묵은 수용으로 계산하지 않는다. 자동 효과 판정이나 v2의 표본 threshold를 승계하지 않는다. 제품 효과는 `NOT_PROVEN`이다.
- v2 config/ledger는 보존하며 과거 기록을 v3에 backfill하지 않는다. v3 운영 config가 등록된 target은 v3만 기록한다. 손상·종료 상태에서도 v2로 fallback하지 않는다.
- `asserted_work_link`와 `operator_reported_unverified`는 관찰 증거다. 인간의 exact-candidate 승인 권한이나 제품 효과 증거로 승격하지 않는다.

## 설치 후 prospective 등록

리뷰된 Kit를 두 사용처에 배포하고 각 설치 audit이 통과한 다음 등록한다. 이후 설치 receipt/source identity를 바꾸면 v3는 binding 오류로 차단한다. 등록한 config를 덮어써 재결속하지 않는다.

아래 변수는 사용자 승인으로 정한 절대경로와 새 activation ID, 두 등록을 마칠 충분한 여유가 있는 미래 UTC T0다. `$ROSTER`는 두 target 밖의 사용자 custody 경로다.

```sh
python3 scripts/omc_skill_effectiveness_cohort_v3.py create-roster \
  --target "$AI_CS" --target "$STOREFRONT" --output "$ROSTER" \
  --activation-id "$ACTIVATION_ID" --activation-at "$FUTURE_UTC_T0"
python3 scripts/omc_skill_effectiveness_cohort_v3.py enroll --target "$AI_CS" --roster "$ROSTER"
python3 scripts/omc_skill_effectiveness_cohort_v3.py enroll --target "$STOREFRONT" --roster "$ROSTER"
python3 scripts/omc_skill_effectiveness_cohort_v3.py aggregate \
  --source "$AI_CS" --source "$STOREFRONT" --roster "$ROSTER"
```

외부 roster에는 정확히 두 Git-origin identity와 각 설치 receipt SHA·source SHA·version이 들어간다. 파일은 write-once다. 등록 전에 T0가 지나면 새 roster/T0가 필요하다. 동일 등록의 재시도는 bytes를 보존한다. `REGISTERED_NOT_STARTED`는 등록 완료·T0 이전, `ACTIVE_NATURAL_OBSERVATION`은 등록·설치 결속이 유효하고 T0가 지난 상태다. T0 이전 session과 등록 당시 존재하던 session은 표본에 포함하지 않는다.

## 자연 작업 기록

새 implementation work는 기존 `omc-task` Guard의 `start`로 생성한다. 수정은 명시적 work ID로 `continue`, 리뷰는 pending work를 보존한다. candidate는 confirmed session에서 자동 기록된다. synthetic·document-only·benchmark 작업은 운영 표본에서 제외한다. 기존 work의 class를 바꿔 표본으로 재사용하지 않는다.

`omc-review`는 승인 receipt 생성 시 현재 review session ID와 taxonomy를 명시한다. 승인 이벤트는 실제 receipt hash/verdict를 append 시점에 검증한다. 비승인 review는 `record-explicit-review`, 사용자의 명확한 응답은 반환된 choice ID로 `record-explicit-followup`을 실행한다. 원문 복사를 요구하지 않는다. 기록에 실패하면 그 사실을 보여준다. review 저장 후 choice만 실패하면 `resume-review-choice`로 복구하고 review를 중복 추가하지 않는다. 이미 소비한 choice는 재사용하지 않는다.

기록 경로를 점검하려고 가짜 자연 작업을 만들지 않는다. 로컬 통합 fixture의 `task → REVISE → 수정 → APPROVE → accepted` 결과는 작업 1건·리뷰 2건·수용 1건이지만 운영 표본은 아니다.

## 종료와 보존

```sh
python3 scripts/omc_skill_effectiveness_cohort_v3.py close --target "$AI_CS"
python3 scripts/omc_skill_effectiveness_cohort_v3.py close --target "$STOREFRONT"
```

종료 receipt는 activation·종료 시각·event 수·원장 projection hash를 고정하고 이후 append를 거부한다. 종료 후 원장 변경도 report가 거부한다. v2 파일은 그대로 보존한다. 재시작은 새 관찰 계약과 별도 보존 경계가 필요하며 기존 v3 config를 덮어쓰는 명령은 제공하지 않는다.

## 업데이트 전환 — 별도 운영 승인 필요

이 절은 실행 승인이 아니다. 종료·보존, 설치 대상/source, 새 activation/T0의 승인을 각각 확정한다. identity가 바뀌면 기존 승인을 재사용하지 않는다. 아래 CLI는 Kit 로컬 도구이며 운영 사용처에 자동 실행하지 않는다.

1. 두 사용처를 기존 설치 상태에서 `close`하고 종료를 확인한다. 한쪽 실패 시 설치하지 않는다.
2. 각 target에 `archive-closed --target <target> --output <외부 archive.json>`을 실행한다. `verify-archive --archive <archive.json>`은 archive bytes만 읽는다. config·roster·closure·event와 허용된 session/capture 필드만 포함하며, 원문·과거 live 설치의 전체 증명은 아니다. 원본 session은 보존한다.
3. 양쪽에 `prepare-transition --target <target> --archive <해당 archive> --pair-archive <A archive> --pair-archive <B archive>`를 실행한다. archive 검증 후 영속 marker를 먼저 게시하고 기존 파일은 `.omc/cohort-transition-previous/`에 보존한다. 이후 보고 상태는 `TRANSITION_BLOCKED`이며 v2로 fallback하지 않는다.
4. 승인된 Kit를 두 target에 설치하고 strict audit한다. 하나라도 실패하면 차단 상태를 유지한다. 성공을 추정하거나 이전 설치로 자동 rollback하지 않는다. prepare는 기존 v3 config 경로를 없애지 않고 차단용 regular file로 원자 교체한다. marker를 모르는 이전 버전도 v3를 선택한 뒤 invalid config로 기록을 거부하며, 신규 버전은 `TRANSITION_BLOCKED`로 보고한다. 새 enroll도 이 경로를 원자 교체하므로 config 부재에 따른 v2 fallback 구간을 만들지 않는다.
5. 새 activation과 충분히 미래인 T0로 외부 roster를 만들고 양쪽 `enroll`을 실행한다. 등록만으로는 marker가 해제되지 않는다. 이전 work 및 등록 시 존재한 session의 work는 신규 표본에서 제외한다.
6. `activate-pair --target <A> --target <B> --output <외부 joint.json>`으로 양쪽 등록·설치 결속을 확인하고 write-once 공동 receipt를 게시한다. 각 consumer는 자신의 결속을 다시 검증한다. 양쪽 동시 활성화나 분산 트랜잭션은 보장하지 않는다.
7. 양쪽 `report`의 `REGISTERED_NOT_STARTED`를 확인하고 T0 이후 자연 작업만 관찰한다. confirmed start/root session/lineage의 로컬 생성 근거가 없는 work는 제외한다. review receipt·choice·follow-up의 실제 기록은 별도 확인한다.

실패·재시도: archive 저장 실패는 원본을 변경하지 않는다. prepare 중단은 동일 archive 쌍으로 재실행하며, marker·보존 hash 충돌은 거부한다. activation pointer 일부 게시 실패는 같은 joint receipt로 미래 T0 전에 재시도한다. T0가 경과하거나 새로운 identity가 필요하면 자동 해제하지 않고 별도 복구 계획·승인을 요구한다. 이 도구는 같은 경로의 두 번째 전환을 자동 순환시키는 범용 migration manager가 아니다.

검증 범위: 로컬 synthetic CLI·실패 주입 회귀는 운영 표본이 아니다. process kill·전원 손실·두 Mac 동시 실행의 실환경 복구는 별도 검증 없이는 보장하지 않는다.
