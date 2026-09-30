# Work-lifecycle cohort v3 운영 전환

대상은 승인된 ai-cs와 sixshop3-storefront-fe 두 저장소다. 운영 전환 코드의 로컬 검증과 실제 배포·등록·T0 시작을 구분한다. 현재 실제 전환은 미실행이다.

## 계약

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
