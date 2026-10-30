---
skill_name: omc-review
description: "코드 변경사항·diff·PR 리뷰. 치명/중대/경미/제안으로 분류하고 파일:라인 근거를 요구한다."
---
# OMC 코드 리뷰 — 승인 전 버그·회귀·테스트 누락을 이슈부터 확인한다.
## Step 0. 리뷰 범위 수집
blind/read-only 비교 평가는 state/session 명령과 변경 가능한 검증은 실행하지 않는다. `git diff HEAD`와 읽기 전용 확인만 하며 최종 계약은 유지한다.
```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-review" --request "<현재 작업 한 줄 요약>" --roles code_review --completion-action preserve-if-present
python3 scripts/omc_review_snapshot.py capture-review --target . --base-commit "$(git rev-parse HEAD)" && git status -sb
python3 scripts/omc_review_snapshot.py show-review-diff --review-snapshot <capture-path> --review-snapshot-sha256 <capture-sha256>
git diff HEAD # scope 확인용이며 판정은 frozen diff 기준
git ls-files --others --exclude-standard
find . -newer .git/index
python3 scripts/omc.py state status --target .
```
승인 기록 명령 실행 전에 v3 `report --target .`를 확인합니다. `ACTIVE_NATURAL_OBSERVATION`이면 아래 명령의 `<관찰 옵션>`을 `--cohort-session-id <현재 review session id> --cohort-taxonomy <확정한 taxonomy>`로 치환하며, 비활성·미등록이면 생략합니다. taxonomy를 확정할 근거가 없으면 임의 지정하지 않고 미관찰로 보고합니다. 반환된 `cohort_capture_v3.status=recorded`와 `choice_id`까지 확인하며 `REVIEW_RECORDED_OBSERVATION_UNOBSERVED`/exit 2는 승인 receipt만 생성된 상태입니다. `explicit_review_link_required` 등 실제 reason_code를 최종에 명시하고 다른 관찰 경로의 오류와 합치지 않습니다. capture가 반환한 immutable snapshot의 path·SHA를 보존하고 frozen diff만 판정한다. `APPROVE*` verdict 후 raw review body를 저장소 밖 regular file에 보존해 `seal-review-output --review-snapshot <capture-path> --review-snapshot-sha256 <capture-sha256> --review-output <raw-review-file>`로 evidence path·SHA를 만든 뒤 `omc_review_snapshot.py record-review --target . --review-snapshot <capture-path> --review-snapshot-sha256 <capture-sha256> --review-evidence <evidence-path> --review-evidence-sha256 <evidence-sha256> --verdict APPROVE <관찰 옵션>`를 실행한다. 이미 commit한 후보는 명시적 reviewed base와 `--candidate-commit HEAD`를 함께 사용한다. 단, amend·rebase로 reviewed base ancestry가 바뀌면 이전 receipt를 재사용하지 않고 새 snapshot을 capture한다. 현재 후보가 capture와 다르면 `review_stale`로 차단한다. review receipt의 `candidate_scope_sha256`는 ship 전용 결속이며 기록 실패는 승인으로 추정하지 않는다.
등록된 완료 품질 관찰이 있으면 현재 pending 작업은 `python3 scripts/omc_completion_observation.py live-status --target .`로 상태를 읽습니다. 완료 후 pending이 제거된 cross-session 작업은 immutable ledger의 명시적 work ID를 확인해 `python3 scripts/omc_completion_observation.py live-status --target . --work-id <work_id>`로 조회하며 다른 작업을 추정하지 않습니다. `AWAITING_USER_OUTCOME`이면 먼저 `python3 scripts/omc_completion_observation.py live-decision-context --target . --work-id <work_id>`의 read-only projection에서 agent 보고·독립 재검증 여부·미실행 항목·commit 결속·자동 추천 불가 사유를 사용자에게 보여준 뒤, 리뷰 판정과 별개로 마지막 행동을 **`수용 / 수정 필요 / 보류`** 중 하나로 묻습니다. 다음 일반 턴의 UserPromptSubmit hook이 원문을 자동 보존하므로 사용자에게 원문 복사를 요구하지 않습니다. 수용·보류 외 후속은 먼저 pending으로 저장하며 `defect_correction`, `missing_requirement`, `persona_mismatch`, `scope_change`, `clarification`, `preference` 중 사용자가 확정한 뒤 `live-classify`로 별도 결속합니다. 불명확하면 추정하지 않습니다. 관찰 실패는 리뷰 판정을 바꾸지 않고 `OBSERVATION_INVALID`와 실제 reason을 보고합니다. `FROZEN_INVALID`/`live_install_identity_invalid`이면 기존 등록은 현재 설치와 불일치하므로 v3 실패와 별도로 표시합니다. 기존 policy·원장·원문을 덮어쓰지 않습니다. 새 품질 관찰은 최소 2개 저장소 roster·미래 T0·현재 설치 결속을 새로 등록해야 하며, 기존 policy가 있으면 write-once `live-enable`로 교체할 수 없으므로 보존 검증을 포함한 전환 절차가 필요합니다.
## 필수 체크
승인 범위 계약: 리뷰 시작 전에 사용자 요청에서 승인 대상 파일 목록을 저장소 상대 경로로 확정한다. snapshot 경로 목록을 자동 복사해 승인 범위로 삼지 않는다. `record-review`에는 각 파일을 `--expected-path <경로>`로 반복 전달한다. 이 목록은 필터가 아니며 snapshot 전체 후보와 정확히 일치해야 한다. 범위 생략은 `review_scope_required`, 불일치는 `review_scope_mismatch`로 차단된다. 최종 보고에는 요청 범위와 실제 후보 범위를 함께 명시한다. 일부 파일만 읽고 전체 후보의 승인 기록을 만들지 않는다. `.omc/lessons` 등 내용 검토 제외 항목도 후보에 포함됐으면 승인 대상에서 조용히 빠진 것으로 간주하지 않는다. 불일치하면 추가·누락 파일을 보고하고 기존 기록을 보존한다. 부분 리뷰가 필요하면 기존 작업을 보존하는 별도 작업 환경에서 의도한 변경만 구성하고 새 snapshot으로 전체 후보를 다시 리뷰한다. 파일 삭제·stash·receipt 수동 편집·승인 목록 자동 확장으로 통과시키지 않는다. 내부 승인 기록 함수도 명시적 `expected_paths`를 요구한다. 기존 호출은 의도한 파일 목록을 전달하도록 갱신해야 하며, 생략 호출은 승인으로 보고하지 않는다.
- 범위 확정 / 파일:라인 근거 / 검증 커맨드를 기록한다. 완료 판정 계약: CONTRACT에서 필수 검증의 대상·방법·통과 조건을 확정한다. 통제된 컴포넌트 검증과 실제 서비스 검증은 별개이며 필수 대상 대신 다른 화면·버전의 증거를 쓰면 증거 불일치다. 필수 검증 통과일 때만 전체 완료를 보고한다. 미실행·확인 불가는 구현됨·검증 미완료, 실패는 수정 필요, 증거 불일치는 재검증 필요로 보고한다. 브라우저 불필요가 계약상 명확하면 브라우저 미실행만으로 보류하지 않는다. 기존 실패는 동일 기준의 비교 근거가 있을 때만 신규 실패와 구분한다. 코드 승인과 전체 완료를 구분하며 미충족 검증·사유·다음 행동 한 가지를 명시한다. 이 안내는 물리적 차단을 보장하지 않는다. review는 코드 승인 여부와 필수 검증 충족 여부를 각각 보고한다. 코드에 이슈가 없어도 전체 완료를 추정하지 않으며 필수 검증이 남으면 자동 ship 추천을 하지 않는다. 검증 미실행만으로 코드 결함을 만들어내거나 코드 승인 판정을 강제로 낮추지 않는다. 출력이 길어져도 마지막 `검증 커맨드 / 한글 판정 / 다음 추천`은 생략하지 않습니다. | 리뷰어가 사용자에게 바로 보여줄 것: 근거 이슈·검증·판정 | 시스템이 암묵적으로 처리: 분할·요약·범위 밖 제외 | 정상 최종 출력: finding → 검증 → 판정, 첫 3줄 안에 결론, 24줄 이하, 같은 사실 반복 0회, 다음 행동은 정확히 1개, Machine output contract 두 줄은 줄 수·중복·다음 행동 측정에서 제외, 내부 상태인 `사용자 선택 대기` 직접 노출 금지 | 모든 severity가 비면 한 줄로 합친다. REVISE/BLOCK은 원인·영향·수정 방향을 유지한다. | 안전 필수 항목: 파일:라인 / 별도 판정 기록 / [치명] [중대] [경미] [제안] 기존 기계 계약: APPROVE/APPROVE WITH NOTES=>approved,null; REVISE=>blocked,omc-task,false; BLOCK=>blocked,null,true. 코드 승인 보고에 outcome=blocked를 함께 쓰지 않는다. risk는 low/medium/high이고 user_selection_needed는 boolean으로 출력한다. 수동 제안과 기계 라우팅은 별개다. 본문의 수정 요청으로 /task를 제안해도 BLOCK의 next_skill은 null, user_selection_needed는 true다. REVISE일 때만 next_skill=omc-task,false를 쓴다. 아래 다음 추천은 수동 제안이며 기계 라우팅 표를 덮어쓰지 않는다.
리뷰 범위: `git diff HEAD` 전체와 필요한 untracked/ignored 파일을 직접 읽고, `.omc/runs` `.omc/lessons` `pipeline_run_result`는 제외한다. 200줄 이상은 파일별로 나눈다.
## Step 1. REVIEW CHECKLIST — C1 정확성/정합성: null·빈 배열·타임존·인덱스·정렬 / C2 조용한 실패 / C3 안전성·복구
C4 API·consumer 계약 / C5 새 로직 테스트·검증 / C6 성능·메모리·불필요한 반복 / C7 유지보수·책임·이름 / C8 외부 계약: optional/null/unknown fallback과 필드·타입·순서. 모르면 `N/A — 이유`.
## Evidence gate
- `behavioral_direct`: diff 안의 원인→분기→관찰 결과가 직접 연결될 때만 P1/P2 finding 가능.
- `non_behavioral`: 동작 영향 없음, `context_needed`: 외부 계약 필요, `test_quality_only`: 테스트 강도만 우려, `unresolved`: 근거 상충. 이들은 P1/P2 finding으로 출력하지 않는다.
- Direct-impact: 값의 출처→변환/분기→결과가 모두 diff 안이면 승격하고, 외부 계약 확인만을 이유로 `context_needed`로 내리지 않는다.
- Diff-local P1: 동적 값 이중 읽기 / 식별자→순서 매핑 / 측정값 없는 정책 성공 / 필수 메타데이터 조건부 검증을 확인한다.
## Step 2. REVIEW RESULT
```text
[치명]
- 없음 또는 `[파일:라인] evidence_class: behavioral_direct | evidence: 직접 인과와 관찰 영향 | 수정 방향`
[중대]
- 없음 또는 `[파일:라인] evidence_class: behavioral_direct | evidence: 직접 인과와 관찰 영향 | 수정 방향`
[경미]
- 없음 또는 `[파일:라인] evidence_class: behavioral_direct | evidence: 직접 인과와 관찰 영향 | 개선 방향`
[제안]
- 없음 또는 `[파일:라인] evidence_class: test_quality_only | evidence: 선택 개선 근거`
[확인 필요]
- 없음 또는 `[파일:라인] evidence_class: context_needed|unresolved | evidence: 필요한 검증`
검증 커맨드: ...
판정: BLOCK / REVISE / APPROVE WITH NOTES / APPROVE
판정: 한글로 한 번만 표시; VERDICT는 별도 기록
decision: REVISE / APPROVE (판정 결과) | risk: HIGH / MED / LOW (리스크 요약) | next_action: 다음 스킬 1개 | 공통 결정표: stage=review / outcome=approved|blocked / user_selection_needed=yes|no / ship_intent_explicit=yes|no
```
- 강한 finding은 `evidence_class: behavioral_direct`와 비어 있지 않은 `evidence:`가 필수다. 가설은 `[확인 필요]`로 내린다. 판정 규칙: 치명=BLOCK, 중대=REVISE, 경미/제안만=APPROVE WITH NOTES, 없음=APPROVE. REVISE/BLOCK면 수정 방향 포함.
- v1/v2 raw-free cohort가 활성이고 운영 v3 등록이 없으며 pending work가 있으면 taxonomy 하나를 선택해 `record-review`를 실행하고, 최종에는 `수용 / 수정 필요 / 보류`를 보여준다. ship 결속 검증이 `review_stale`로 차단된 같은 pending work는 `--verdict BLOCK --taxonomy review_stale`로 별도 review event를 기록한다. pending work 결속을 확인할 수 없으면 stale을 추정·기록하지 않고 관찰 불가로 남긴다. 다음 응답이 정확히 일치할 때만 agent가 `python3 scripts/omc.py skill-cohort record-followup --target . --outcome <accepted|correction|deferred>`를 실행하며, 불명확·다른 세션·침묵은 추정하지 않는다.
- **v3 명시적 등록 경로:** `report --target .`로 `ACTIVE_NATURAL_OBSERVATION`과 현재 work/session 결속을 확인한다. 운영 v3는 보존된 v2 설정보다 우선하며, v1/v2 `record-review`·`record-followup`으로 우회하지 않는다. `DRAFT_SYNTHETIC` fixture는 v2 설정이 없을 때만 기존 격리 경로를 사용한다. 승인 receipt 생성 명령에 `--cohort-session-id <현재 review session id> --cohort-taxonomy <taxonomy>`를 함께 전달한다. `REVISE/BLOCK` 판정은 `python3 scripts/omc_skill_effectiveness_cohort_v3.py record-explicit-review --target . --session-id <현재 review session id> --work-id <명시적 work id> --verdict <REVISE|BLOCK> --taxonomy <taxonomy>`로 기록한다. 반환된 `cohort_capture_v3.status=recorded` 또는 명시적 review event와 `choice_id`를 확인한다. 해당 review의 결과를 보여주고 정확히 대응하는 사용자 응답이 있을 때만 `python3 scripts/omc_skill_effectiveness_cohort_v3.py record-explicit-followup --target . --choice-id <choice_id> --outcome <accepted|correction|deferred>`를 실행한다. 실패·침묵·다른 작업은 미관찰로 남긴다. `REGISTERED_NOT_STARTED`·`CLOSED`·binding 오류에서는 기록하지 않는다. 이 기록은 관찰상 연결이며 인간 승인 권한이 아니다.
- v3에서 review event 기록 후 choice 저장만 실패했다면 새 review를 만들지 않고 `python3 scripts/omc_skill_effectiveness_cohort_v3.py resume-review-choice --target . --session-id <당시 review session id>`로 미완료 choice만 복구한다. 현재 confirmed review session이 아니거나 이미 choice가 있으면 거부한다.
- **분류 구분:** `behavioral_direct`·`test_quality_only` 등은 finding의 evidence class이며 cohort taxonomy가 아니다. `--cohort-taxonomy` 허용값은 `requirement_gap / verification_gap / scope_gap / output_confusion / gate_friction / environment_blocker / external_dependency / review_stale`뿐이다. finding 분류를 그대로 전달하거나 의미가 다른 taxonomy로 자동 치환하지 않는다. 적합한 관찰 분류를 확정할 수 없으면 미관찰로 보고한다.
- **기록 결과 확인:** 잘못된 cohort 옵션은 receipt 생성 전에 `cohort_review_input_invalid`로 거부된다. `REVIEW_RECORDED_OBSERVATION_UNOBSERVED` 및 exit 2는 receipt는 생성됐으나 관찰이 실패했다는 뜻이다. receipt와 capture 결과를 분리해 보고하고 실패를 수용으로 집계하거나 원장을 소급 수정하지 않는다.
## 다음 추천 — 우선순위는 `현재 병목 > 기본 파이프라인`, 주추천 1개만 제시한다.
- REVISE/BLOCK면 `$omc-task` (수동 제안; 기계 라우팅의 BLOCK은 null,true 유지)
- APPROVE/APPROVE WITH NOTES + 배포 준비 명시 + ship_intent_explicit=yes면 `$omc-ship`
- APPROVE/APPROVE WITH NOTES + 배포 준비 미명시 또는 user_selection_needed=yes면 내부 라우팅은 사용자 선택 대기, 화면에는 실제 선택(예: `변경사항을 커밋할지 결정해 주세요.`)을 요청. 자동으로 진행하지는 않습니다.

기계 기록의 단계: stage=review. 아래 별도 기록 규칙이 이전 출력 예시보다 우선한다.

## Machine output contract
별도 기록 모드 `detached_v1`을 명시적으로 사용한다. `omc-output/v1`의 stage/outcome/risk/next_skill/user_selection_needed/reason_code·VERDICT·기존 라우팅 의미는 바꾸지 않는다. `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`는 내부 판정 원문에만 두고 사용자 최종 답변에 붙이지 않는다. 한글 판정·이유·검증·다음 행동만 표시한다.
먼저 `python3 scripts/omc_output_contract.py capabilities`와 `python3 scripts/omc_workflow_validation.py capabilities`로 발행자·소비자의 지원을 각각 확인한다. 실행 전에 모드·현재 작업/세션·단계·보고 ID·마감과 명시 기록 경로를 고정한다. 문맥이 없으면 `prepare --target . --stage <단계> --report-id <새ID> --budget-sec <승인된남은초>`로 현재 상태에서 새 문맥을 만들고 반환된 context/path·SHA를 호출자 실행 메타데이터에 보존한다. 숫자 예산을 임의 연장하거나 이전 보고 ID를 재사용하지 않는다.
일반 plan/review/critique/investigate/ship 선언은 내부 원문을 작업의 .omc 안에 저장한 뒤 `python3 scripts/omc_output_contract.py publish --target . --output-context <문맥경로> --output-context-sha256 <해시> --source-file <내부원문> --source-sha256 <해시>`로 발행한다. 이는 모델 선언이며 해시 일치가 검증 통과나 사용자 승인 진위를 인증하지 않는다.
실제 task 완료는 기존 `complete-task --verification-file <내부검증>`에 `--output-mode detached_v1 --output-context <문맥경로> --output-context-sha256 <해시>`를 추가한다. 이관은 승인된 complete-transfer 경로를 유지한다. 일반 publish로 완료 가드의 발행 주체를 지정할 수 없다.
사용자 최종 답변은 발행 CLI의 한글 stdout과 같은 바이트로 제공한다. 경로/해시는 사용자 답변 꼬리표 대신 호출 인자와 실행 메타데이터로 전달한다. 수집기는 명시 manifest 경로/해시·현재 상태·native 호출/반환·실제 최종 원문을 독립 대조한다. CLI 표시 검사·수집기 fixture·실제 모델의 자유 응답 준수는 별도 증거다.
업무 차단 종료 코드1도 정상 판정으로 기록한다. 전달 실패 코드2는 업무 실패와 다르다. 저장·봉인·출력 실패 시 한글 오류만 알리고 완료/승인·자동 후속·구형 fallback을 금지한다. 실패 기록도 저장할 수 없으면 stderr·종료 원문만 보존한다. 봉인 후 표시 실패를 이유로 기존 manifest나 verdict를 수정하지 않는다.
구형 legacy_inline으로 시작한 입력만 기존 parser로 읽는다. 신형 기록 누락·변조·다른 세션·모드 혼합·표시 불일치는 보류하며 구형으로 우회하지 않는다. 구형 도구에서 신형 안내를 실행할 수 없으면 호출 전에 한글 지원 불가를 알린다. 이 안내는 플랫폼 화면 숨김이나 모델 준수를 물리적으로 보장하지 않는다.
