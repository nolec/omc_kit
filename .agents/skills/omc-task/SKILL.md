---
skill_name: omc-task
description: "7단계 TDD 파이프라인으로 구현 실행. 트리거: 구현해줘, 만들어줘, 코딩해줘, 개발해줘, 이거 짜줘. CONTRACT→RED→GREEN→REFACTOR→GATE→REVIEW 순서. 단계 건너뜀 금지. omc-plan 이후 사용."
---

# OMC TDD 파이프라인

구현은 아래 순서로만 진행합니다. 계획이 없으면 `$omc-plan`, 원인 불명 버그면 `$omc-investigate`로 돌립니다. 속도 기준은 `docs/orchestration_usage.md`와 같습니다: DoD가 고정되면 fast, 새 파일/API/다중 파일이면 normal.

## 0. Guard

```bash
python3 scripts/omc_guard.py sync-require --target . --mode autopilot --title "omc-task" --request "<현재 작업 한 줄 요약>" --roles senior_coding --work-class "<implementation|synthetic|document_only|benchmark_maintenance>" --completion-action "<start|continue|preserve>" --for "task"
```

Guard 실행 전에 작업 성격을 분류합니다. 제품 코드 변경은 `implementation`, 합성 fixture는 `synthetic`, 문서만 변경하면 `document_only`, benchmark 도구·측정 변경은 `benchmark_maintenance`입니다. 모호하면 임의 기본값을 쓰지 말고 범위를 확인합니다.

새 자연 작업은 `start`, 같은 작업의 리뷰 수정은 pending receipt의 `work_id`와 함께 `continue`, 승인된 기존 변경의 문서 정리·로컬 커밋만 수행하면 `preserve`를 사용합니다. `preserve`는 기존 pending 작업이 없으면 실패합니다. 같은 baseline이라는 이유만으로 `work_id`를 추론하거나 다른 작업에 재사용하지 않습니다.

실패하면 세션 확인 또는 confirm부터 처리하고 중단합니다.
Prospective 수집은 `python3 scripts/omc_work_class_lock.py init --target .`으로 저장소 밖 사용자 custody를 한 번 생성하고 `preflight`로 확인합니다. Guard는 이 설정을 자동 로드해 세션 생성 즉시 lock을 봉인합니다. CI처럼 환경변수가 필요한 곳은 `OMC_REQUIRE_WORK_CLASS_LOCK=1`, 외부 `OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE`, pinned `OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY`를 함께 설정합니다. 등록 저장소의 새 implementation session은 Guard가 `live-start`를 자동 실행합니다. `COLLECTING`이면 최초 완료 보고 직전 보고·검증 원문을 외부 임시 파일에 보존해 `python3 scripts/omc_completion_observation.py live-capture --target . --report <임시보고> --verification-output <임시검증> [--unrun <항목>]`으로 기록합니다. 자동 start 실패는 제품 작업을 차단하지 않고 failure receipt를 남기며 해당 표본만 무효화합니다. 미등록 저장소는 관찰을 시작하지 않습니다. 사용자에게 원문 복사를 요구하지 않고 `commit_bound=false` snapshot을 signed completion receipt나 Pilot 근거로 사용하지 않습니다.

## 필수 체크

- CONTRACT 등록: `python3 scripts/omc_pipeline_guard.py contract-done`
- RED 등록 + TDD 게이트: FAIL 출력 첨부 + `red-done` 완료 / `python3 scripts/omc_tdd_check.py --staged` exit 0

사용자에게 보여줄 단계: CONTRACT / RED / TDD GATE / Handoff | 시스템이 암묵적으로 처리: 자명한 재안내 / 중복 설명 / 단계 사이 반복 코칭 | 정상 최종 출력: 결과 → 변경 → 검증 → 다음 행동, 첫 3줄 안에 결론, 20줄 이하, 같은 사실 반복 0회, 다음 행동은 정확히 1개, Machine output contract 두 줄은 줄 수·중복·다음 행동 측정에서 제외, 내부 상태인 `사용자 선택 대기` 직접 노출 금지 | 진행 commentary는 최대 3회(시작 / 실제 RED 결과 / 최종 TDD GATE), 최종 답변에서 단계별 진행 로그를 재서술하지 않는다.

안전 필수 항목: CONTRACT / RED / TDD GATE / Handoff는 압축해도 유지 | 작은 후속 수정도 Guard, CONTRACT, RED 등록 순서는 유지 | 범위 분리: 현재 dirty 변경과 이번 구현 범위를 섞지 않음

## PHASE 1 ▸ CONTRACT — 목표 / 범위 / DoD / 제약 / 사용자 컨펌:
- 컨펌 전 구현 파일을 수정하지 않습니다. 완료 판정 계약: CONTRACT에서 필수 검증의 대상·방법·통과 조건을 확정한다. 통제된 컴포넌트 검증과 실제 서비스 검증은 별개이며 필수 대상 대신 다른 화면·버전의 증거를 쓰면 증거 불일치다. 필수 검증 통과일 때만 전체 완료를 보고한다. 미실행·확인 불가는 구현됨·검증 미완료, 실패는 수정 필요, 증거 불일치는 재검증 필요로 보고한다. 브라우저 불필요가 계약상 명확하면 브라우저 미실행만으로 보류하지 않는다. 기존 실패는 동일 기준의 비교 근거가 있을 때만 신규 실패와 구분한다. 코드 승인과 전체 완료를 구분하며 미충족 검증·사유·다음 행동 한 가지를 명시한다. 이 안내는 물리적 차단을 보장하지 않는다. task의 필수 검증이 미충족이면 본문 완료 주장 금지, outcome=blocked 및 reason_code=verification_incomplete로 보고하고 충족한 경우에만 outcome=done을 사용한다. 검증을 수행할 수 없으면 통과로 추정하거나 필수 조건을 임의로 낮추지 않는다. 기존 기계 계약: VERDICT는 PROCEED 또는 BLOCK만 사용한다. PROCEED=>done,omc-review,false; BLOCK=>blocked,null,true (순서: outcome,next_skill,user_selection_needed). risk는 low/medium/high이고 user_selection_needed는 boolean으로 출력한다. 검증 상태 첫 문장: 최종 task 보고 첫 줄은 정확히 다음 상태 문장 하나다: 통과=필수 검증 통과·작업 완료; 미실행/확인 불가=구현됨·검증 미완료; 실패=검증 실패·수정 필요; 증거 불일치=증거 불일치·재검증 필요. 실패 > 증거 불일치 > 미실행 > 통과 순으로 판정한다. 구현 반영이 확인되지 않았으면 구현 여부 확인 불가·검증 미완료를 사용한다. 첫 줄에 괄호나 부분 완료 주장을 붙이지 않는다. blocked 보고에는 작업 완료/전체 완료/구현 완료라는 부분 완료 제목을 쓰지 말고 확인된 반영 사실은 별도 줄의 구현 상태: 반영됨으로 쓴다.

## PHASE 2 ▸ DESIGN — 입력/출력 계약 / 실패·에러 정책 / 영향받는 파일:
- decision / risk / next_action: 구현 상태 / 변경 위험도 / 다음 스킬 1개
- 공통 결정표: stage=task / outcome=blocked|done / user_selection_needed=yes|no
- 계약이 약한 작업도 빈칸 없이 적고, 작은 후속 수정도 영향 파일과 실패 정책은 생략하지 않습니다.

## PHASE 3 ▸ RED — 테스트 파일 / 테스트 케이스 / 실제 FAIL 출력:

```bash
python3 scripts/omc_pipeline_guard.py red-done <테스트파일>
```

- FAIL 출력과 `red-done` 등록 없이 구현 파일을 만들지 않습니다.

## PHASE 4 ▸ GREEN — 구현 파일 / 핵심 변경 / 테스트 PASS / 기존 회귀 없음:
- 최소 구현만 먼저 합니다.

## PHASE 5 ▸ REFACTOR — 정리 항목 / 리팩터링 후 PASS:
- 이름, 중복, 책임만 정리합니다.

## PHASE 6 ▸ TDD GATE

```bash
python3 scripts/omc_tdd_check.py --staged
```

- 실패하면 수정 후 다시 확인합니다.

## PHASE 7 ▸ COMPOUND ENGINEERING — 교훈 없음 / 교훈 기록 완료:

```bash
python3 scripts/omc_lesson.py add -i
```

- 막히면 `교훈 없음`을 명시합니다.

## Handoff — 모든 단계가 끝나면 `$omc-review`로 넘깁니다.


## 완료 보고 생성 게이트

CONTRACT 등록 시 이번 작업의 필수 검증 ID를 고정합니다:
`python3 scripts/omc_pipeline_guard.py contract-done --required-checks tests,lint,browser`
브라우저가 불필요한 작업은 계약에서 제외하고 그 이유를 설명합니다. 이후 현재 작업에서 실제 FAIL을 확인하고 `red-done <테스트파일>`을 등록합니다.
검증 JSON은 먼저 `python3 scripts/omc_pipeline_guard.py verification-path`로 현재 작업 폴더 내부 경로를 받아 그 경로에만 작성합니다. /tmp 등 외부 경로나 외부를 가리키는 심볼릭 링크를 사용하지 않습니다. 최종 보고 직전 `python3 scripts/omc_pipeline_guard.py complete-task --verification-file <제공받은 내부 경로>`를 실행하고 첫 상태 문장과 기계 판정을 그대로 사용합니다.
검증 JSON 형식: `{"session_id":"현재 confirmed session ID","implementation":{"status":"applied","evidence":"구현 반영 확인 근거"},"checks":{"tests":{"status":"passed","evidence":"검증 출력 위치"},"lint":{"status":"passed","evidence":"검증 출력 위치"},"browser":{"status":"unrun","evidence":"미실행 이유"}}}`.
허용 상태는 passed/failed/mismatch/unrun이며 누락·알 수 없는 상태·빈 근거는 미완료입니다. 필수 검증은 계약에 고정된 전체 목록을 확인합니다. 구현의 반영 상태·근거가 없으면 `구현 여부 확인 불가·검증 미완료`로 보고합니다. 현재 작업의 CONTRACT·RED 등록이 없거나 계약 최초 등록 이후 새로 생기거나 내용·권한이 달라진 미스테이징 구현 변경이 남으면 완료로 출력하지 않습니다. 계약 최초 등록에 보존한 기존 변경은 그대로면 제외하고, 같은 세션에서 계약을 다시 등록해도 기준을 갱신하지 않습니다. `--staged`의 신규 구현 없음은 전체 완료 증거가 아닙니다.
도구 출력 뒤 최종 답변을 다시 길게 작성하지 말고, 상태 문장을 첫 줄에 한 번만 두며 상태·다음 행동을 반복하지 않습니다. 수집자는 모델 종료 후 최종 원문 전체를 수정 없이 보존하고 서식 품질과 필수 의미 판정을 따로 검사합니다. 이 명령은 호출자가 제공한 상태·근거 문자열을 판정합니다. 실행 결과의 진위나 브라우저 실제 수행을 인증하지 않으며, 명령을 우회한 자유 응답을 물리적으로 차단하지 않습니다. 증거를 만들어내거나 완료를 위해 필수 검증 목록을 줄이지 않습니다.


### 승인된 검증 기준 연결

CONTRACT 목록을 모델이 새로 정하지 않습니다. 사용자에게 제시해 승인받은 항목을 세션별 JSON으로 기록하고 `python3 scripts/omc_pipeline_guard.py approve-checks --file <기준 JSON>`으로 등록한 뒤 CONTRACT가 정확히 같은 ID를 참조하도록 합니다. 이 명령은 이미 받은 승인을 기록하며 승인 자체나 증거 진위를 인증하지 않습니다. 명령을 실행했다는 이유로 사용자 승인을 추정하지 않습니다.

기준 JSON은 `session_id`, 1부터 증가하는 `revision`, 요청 원문 `request`, 실제 사용자 승인 메시지를 가리키는 `approval_reference`, `checks` 배열을 포함합니다. 각 항목은 `id`, `target`, `method`, `pass_condition`, `target_version`을 명시합니다. 통제 화면과 로그인 후 실제 홈은 서로 다른 ID로 두며 실제 홈 검수를 통제 화면 증거로 대체하지 않습니다. 대상 버전은 검증할 코드 snapshot이나 commit을 식별합니다.

`python3 scripts/omc_pipeline_guard.py verification-template`의 JSON 출력을 현재 작업 내부 `verification-path`에 저장합니다. 출력의 `basis_sha256`와 항목별 `definition_sha256`, `code_snapshot_sha256`는 유지하고 실제 수행한 상태·근거만 채웁니다. 템플릿은 검증 직전에 생성하며 코드가 바뀌면 이전 passed 항목을 복사하지 않고 새 템플릿으로 해당 검증을 다시 수행합니다. 코드 스냅샷은 Git tracked 파일과 Git이 무시하지 않는 모든 untracked 파일의 경로·내용·권한을 대조합니다. 확장자로 실행 설정이나 자산을 추정해 제외하지 않습니다. `.omc` 실행 기록과 검증 기록 파일 자체는 제외합니다. Git ignored 파일·외부 의존성은 이 스냅샷의 검증 대상이 아닙니다. 스테이징 위치만 바뀌고 내용이 같으면 같은 스냅샷입니다. Git이 없는 환경은 파일 전체를 검사하되 `.git`, `.omc`, `node_modules`, `__pycache__`, `.pytest_cache` 폴더와 검증 기록 자체는 제외합니다. `target_version`은 검증 대상 설명이고 실제 코드 재사용 차단은 `code_snapshot_sha256` 대조가 담당합니다. 모든 항목의 초기값은 unrun이며 실제 실행 결과를 확인하기 전 passed로 바꾸지 않습니다. 이 해시는 기준 연결 검사이며 브라우저 실제 수행 인증이 아닙니다.

범위 변경은 변경할 항목을 사용자에게 제시해 승인받은 새 개정으로만 추가합니다. 이전 파일을 덮어쓰거나 환경 부족을 이유로 임의 축소하지 않습니다. CONTRACT를 새 개정에 다시 연결하고 이전 개정 증거는 자동 재사용하지 않습니다. 변경되지 않은 항목도 대상·통과 조건·버전 일치와 실제 증거를 확인한 뒤 새 개정 기록에 연결합니다. 기준이 없는 기존 세션은 작업물을 유지하고 편집은 허용하되 전체 완료 확정만 보류합니다.

완료 명령과 수집기는 동일한 읽기 전용 전체 판정 함수를 사용해 세션·등록·승인 기준·코드 스냅샷·미스테이징 차단을 함께 확인합니다. 최종 수집기는 호출자가 지정한 expected-outcome만 신뢰하지 않고 승인 기준·개정·개별 대상과 원문 보고를 다시 대조합니다. blocked 여부만이 아니라 실패·증거 불일치·미실행·구현 확인 불가의 정확한 상태 문장을 도구 출력과 최종 보고 모두에서 대조하며 다른 상태로 완화하거나 과장하면 통과시키지 않습니다. 기준이 없거나 완료 명령을 생략하면 비교 진입을 보류합니다. 로컬 파일 직접 변조 방지는 이 기능의 보장 범위가 아닙니다.

## Machine output contract — 마지막 두 줄은 `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT: <VALUE>`; JSON은 `schema_version=omc-output/v1`, `stage`, `outcome`, `risk`, `next_skill`, `user_selection_needed`, `reason_code`; `next_skill`은 canonical `omc-*` 또는 null; unresolved/blocked는 `reason_code` 필수; legacy 평문 입력은 허용하되 새 출력은 숨김 형식만 사용하고 명시적 오류는 보정하지 않습니다.

## 다음 추천

- 우선순위는 항상 `현재 병목 > 기본 파이프라인`
- 주추천 1개만 제시, 우선순위: 구현 완료 + 게이트 통과면 `$omc-review`
- 구현 완료 + 사용자가 일단 결과만 확인하려는 상태면 내부 라우팅은 사용자 선택 대기, 화면에는 `결과를 확인한 뒤 리뷰 진행 여부를 알려주세요.`
- 실패 원인 불명 → `$omc-investigate`
- 자동으로 진행하지는 않습니다.

---

## ⛔ 자동 진입 금지

이 스킬이 완료된 후 자동으로 다음 스킬을 실행하지 않고, 사용자가 명시적으로 다음 스킬을 요청할 때까지 멈추고 기다린다.


## 최종 보고와 작업 기록의 구분

PHASE 7 기록은 최종 답변에 붙이지 않습니다. 단계별 기록과 교훈은 작업 중 처리하고, 사용자 최종 보고는 상태·변경·검증·다음 행동만 간결하게 작성합니다. complete-task의 기계 판정 두 줄을 최종 답변 끝에 그대로 복사합니다. HTML 주석과 VERDICT를 생략하거나 평문 요약으로 대체하지 않습니다. 이 최종 보고 계약이 위 단계별 양식보다 우선합니다.
