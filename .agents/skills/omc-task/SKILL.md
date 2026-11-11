---
name: omc-task
description: "7단계 TDD 파이프라인으로 구현 실행. 트리거: 구현해줘, 만들어줘, 코딩해줘, 개발해줘, 이거 짜줘. CONTRACT→RED→GREEN→REFACTOR→GATE→REVIEW 순서. Tier별 허용 생략 외 단계 건너뜀 금지. omc-plan 이후 사용."
---

# OMC TDD 파이프라인

Tier별 허용 생략 외에는 단계를 건너뛰지 않습니다. 적용되는 구현 단계는 아래 순서로 진행합니다. 계획이 없으면 `$omc-plan`, 원인 불명 버그면 `$omc-investigate`로 돌립니다. 속도 기준은 `docs/orchestration_usage.md`와 같습니다: DoD가 고정되면 fast, 새 파일/API/다중 파일이면 normal.

## 작업별 검증과 준비 확인

- 로컬 커밋 전에는 pre-commit의 승인·TDD 검사뿐 아니라 post-commit의 완료 기록 소비 조건도 확인한다. 현재 완료 연결은 단일 커밋 기준이며, 기능별 다중 커밋의 전체 작업 연결은 지원하지 않는다. 대기 기록 없음(`no_pending_completion`)은 정상 생략이다. 손상·불일치·읽기 실패는 `state complete`의 구조화 오류와 종료1로 구분한다. post-commit 기록 실패에도 Git 커밋은 이미 생성됐으므로 Git 성공·기록 실패·검증 결과를 별도로 보고하고 커밋을 다시 실행하지 않는다. 이 안내가 에이전트 준수나 다중 커밋 지원을 보장하지는 않는다.

- 변경 범위와 필수 검수를 먼저 정한다. 기존 품질 설정의 명령·대상·범위를 이번 작업과 대조하고, 지정 검사 파일이 해당 기준에 존재하는지 확인한다. 기준은 초기 HEAD 또는 명시된 기준 스냅샷이며, 최종 커밋을 먼저 만들라는 요구가 아니다. 검증 시에는 최종 후보와 결과를 다시 연결한다. 일반 git status만으로 준비 완료를 판단하지 않는다. 관련 입력과 AGENTS.md 등의 skip-worktree·assume-unchanged 여부 및 실제 파일/기준 차이를 읽기 전용으로 확인하고 필요한 서버·화면 접근도 점검한다. 준비 확인은 수정 후 실제 화면 검수를 대체하지 않는다. 계속 진행할 수 있는 것은 범위·안전·입력 무결성에 영향이 없다고 확인한 독립 작업만이다. 부족한 환경을 자동으로 수리하거나 권한을 확대하지 않는다. 설정 승인됨 / 코드 리뷰 승인됨 / 검사 통과 / 실제 화면 확인은 별개 사실이다. 해당하는 근거와 미확인 항목을 구분하며, ready만으로 이번 작업 적합성이나 검사 통과를 주장하지 않는다. 별도 고정 출력 양식이나 새 가드를 추가하는 규칙은 아니다. 부족한 승인은 설정 적용·검사 대상·횟수·시간·중단 조건을 한 번에 제시한다. 이미 승인된 범위는 재확인하지 않는다. 각 단계의 승인 기록은 유지하며, 실행 오류를 자동 재시도 승인으로 해석하지 않는다. 이 안내와 지침 회귀는 실제 모델 준수나 사용자 개입 감소의 증거가 아니다.

| 발견 사항 | 중단·진행할 범위 | 보존·금지 행동 |
| --- | --- | --- |
| 과거 품질 설정 불일치 | 해당 설정 실행만 보류; 승인된 독립 검수는 구분해 진행 | 기존 설정 보존; 자동 덮어쓰기 금지 |
| 숨겨진 로컬 변경 | 영향 확인 후 영향받는 격리 준비만 중단 | 파일·Git 플래그·원문 보존; 자동 해제·폐기·커밋 포함 금지 |
| 실제 화면 접근 불가 | 필수 화면은 미검증; 승인된 안전한 구현·정적 검수는 구분해 진행 | Draft PR은 별도 승인된 경우에만 공유; 자동 환경 수리·필수 검수 생략·작업 전체 또는 출하 준비 완료 주장 금지 |

단순 운영 기록 정리 절의 보고 예외는 코드·스킬·설정 변경과 이어갈 pending 구현이 없을 때만 검토한다. [상세 조건과 보고 경로](references/verification.md#단순-운영-기록-정리)를 먼저 읽고, 구현 작업의 RED·완료 검사를 대신하지 않는다.

## 0. Guard

### 사전 승인된 비교 실행의 진입

준비 담당이 고정한 policy 해시·session_id·run_id·basis_sha256을 모두 명시한 비교 작업은 아래 두 명령으로 진입합니다. 일반 새 작업용 `sync-require`로 세션을 교체하지 않습니다.

```bash
python3 scripts/omc_validation_command.py task-context --target . --sha256 <policy_sha256> --session-id <session_id> --run-id <run_id> --basis-sha256 <basis_sha256>
python3 scripts/omc_validation_command.py run --target . --profile omc-guard-task
```

두 명령 모두 통과한 경우에만 기존 CONTRACT·승인 기준으로 진행합니다. 미지정·불일치·실패는 중단하고 원문을 보존합니다. 새 세션 생성, 승인 복사, 기준 축소로 복구하지 않습니다. 일반 작업은 아래 Guard를 그대로 사용합니다.

비교 결과 선택 파일은 `.omc/comparisons/<run_id>/final-selection.json`입니다. 고정 전에 runtime 경로를 검사하고, 고정 후 `python3 scripts/omc_validation_command.py selection-path --target .`가 반환한 경로에 기록합니다. 수집기의 `compare-entry`는 같은 경로를 기본으로 읽습니다. 루트 `final-selection.json` 작성·스테이징으로 검증 증거를 무효화하지 않습니다. 수집 오류 원인은 `.omc/native-stream-errors/`에 별도 보존하며 native stdout에 섞지 않습니다. 오류는 성공이나 재실행 승인이 아닙니다.

### 일반 작업의 진입

`python3 scripts/omc_guard.py sync-require --target . --mode autopilot --title "omc-task" --request "<현재 작업 한 줄 요약>" --roles senior_coding --work-class "<implementation|synthetic|document_only|benchmark_maintenance>" --completion-action "<start|continue|preserve>" --for "task"`

Guard 실행 전에 작업 성격을 분류합니다. 제품 코드 변경은 `implementation`, 합성 fixture는 `synthetic`, 문서만 변경하면 `document_only`, benchmark 도구·측정 변경은 `benchmark_maintenance`입니다. 모호하면 임의 기본값을 쓰지 말고 범위를 확인합니다.

새 자연 작업은 `start`, 같은 작업의 리뷰 수정은 pending receipt의 `work_id`와 함께 `continue`, 승인된 기존 변경의 문서 정리·로컬 커밋만 수행하면 `preserve`를 사용합니다. `preserve`는 기존 pending 작업이 없으면 실패합니다. 같은 baseline이라는 이유만으로 `work_id`를 추론하거나 다른 작업에 재사용하지 않습니다.

### 코드 변경 없는 검증·완료 기록 재개 — 위 `senior_coding` 명령은 구현 진입용입니다. 기존 승인·RED를 사용해 남은 검증이나 완료 기록만 이어갈 때는 `senior_coding + preserve`를 사용하지 않습니다. 읽기 검토는 기존 보존 역할을 사용하고, 검증 실행·완료 발행은 비구현 역할로 명시적으로 연결합니다:

`python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-task" --request "<승인된 같은 작업의 남은 검증·완료 기록 재개>" --roles directive --completion-action preserve`

기존 pending 작업·work_id·baseline·승인 기준·코드 상태가 일치하는지 확인한 뒤 원래 소유 세션의 검증 기록을 사용합니다. 역할 전환 자체가 승인·검증 성공을 뜻하지 않습니다. 코드 수정이 필요하면 이 경로를 중단하고 기존 work_id를 명시한 구현 `continue`로 별도 진입합니다.  인계 없는 session_start는 CONTRACT 연결을 초기화합니다. `preserve`만으로 초기화된 승인을 복원할 수 없습니다. 인계는 기존 승인된 실행 경로에서 발급·소비하고, 지원되지 않는 일반 재개에서는 원본을 보존한 채 차단 사유를 보고합니다. 새 세션에 승인·RED를 복사하거나 재등록해 통과시키지 않습니다.  8시간 만료는 역할 변경으로 연장하지 않습니다. 기존 명시적 완료 재승인 경로의 조건과 사용자 승인을 확인하며, 실패·미실행 검증은 재승인으로 통과하지 않습니다. 기능 검증 결과와 완료 기록 처리 결과를 구분해 보고합니다.
실패하면 세션 확인 또는 confirm부터 처리하고 중단합니다. Prospective 수집은 `python3 scripts/omc_work_class_lock.py init --target .`으로 저장소 밖 사용자 custody를 한 번 생성하고 `preflight`로 확인합니다. Guard는 이 설정을 자동 로드해 세션 생성 즉시 lock을 봉인합니다. CI처럼 환경변수가 필요한 곳은 `OMC_REQUIRE_WORK_CLASS_LOCK=1`, 외부 `OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE`, pinned `OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY`를 함께 설정합니다. 등록 저장소의 새 implementation session은 Guard가 `live-start`를 자동 실행합니다. `COLLECTING`이면 최초 완료 보고 직전 보고·검증 원문을 외부 임시 파일에 보존해 `python3 scripts/omc_completion_observation.py live-capture --target . --report <임시보고> --verification-output <임시검증> [--unrun <항목>]`으로 기록합니다. 자동 start 실패는 제품 작업을 차단하지 않고 failure receipt를 남기며 해당 표본만 무효화합니다. 미등록 저장소는 관찰을 시작하지 않습니다. 사용자에게 원문 복사를 요구하지 않고 `commit_bound=false` snapshot을 signed completion receipt나 Pilot 근거로 사용하지 않습니다.

## Tier별 필수 체크

사용자 지시와 프로젝트 AGENTS.md의 기존 Tier 정책을 따른다. 파일명만으로 Tier0을 판정하지 않는다. 새로운 생략 권한을 추가하지 않는다.

| 구분 | 적용 조건 | 검증 규칙 |
| --- | --- | --- |
| Tier0 | 동작·API·새 파일 없음 | 기존 정책이 허용하는 CONTRACT·RED 생략 가능; 변경에 필요한 린트·공백·화면 검수는 별도 유지 |
| Tier1 기존 파일 | 동작·API·로직 변경 | CONTRACT 필수; 일반 수정의 RED는 기존 정책에 따르며 버그 재현·새 함수 등의 기존 RED 의무 유지 |
| Tier1 신규 파일 | 신규 구현 파일·컴포넌트·훅·함수 | CONTRACT + 실제 실패 테스트·RED 등록 필수, 구현 전에 수행 |

- 해당 Tier에서 요구하는 CONTRACT 등록: `python3 scripts/omc_pipeline_guard.py contract-done`
- 기존 정책상 RED 등록이 필요한 경우 FAIL 출력과 `red-done`을 등록한다. TDD 게이트는 해당 변경의 기존 요건으로 `python3 scripts/omc_tdd_check.py --staged`를 확인한다.
- 사용자에게는 해당 Tier에 적용되는 체크포인트만 표시한다. 아래 단계 설명은 해당 의무가 있는 작업에 적용하며, 기존 CLI의 승인·완료 요건을 우회하거나 근거를 꾸며 생략하지 않는다.

사용자에게 보여줄 단계: CONTRACT / RED / TDD GATE / Handoff | 시스템이 암묵적으로 처리: 자명한 재안내 / 중복 설명 / 단계 사이 반복 코칭 | 정상 최종 출력: 결과 → 변경 → 검증 → 다음 행동, 첫 3줄 안에 결론, 20줄 이하, 같은 사실 반복 0회, 다음 행동은 정확히 1개, Machine output contract 두 줄은 줄 수·중복·다음 행동 측정에서 제외, 내부 상태인 `사용자 선택 대기` 직접 노출 금지 | 진행 commentary는 최대 3회(시작 / 실제 RED 결과 / 최종 TDD GATE), 최종 답변에서 단계별 진행 로그를 재서술하지 않는다. 안전 필수 항목: CONTRACT / RED / TDD GATE / Handoff는 압축해도 유지 | 작은 후속 수정도 해당 Tier의 Guard·CONTRACT·RED 적용 요건과 순서는 유지 | 범위 분리: 현재 dirty 변경과 이번 구현 범위를 섞지 않음

## PHASE 1 ▸ CONTRACT — 목표 / 범위 / DoD / 제약 / 사용자 컨펌: 컨펌 전 구현 파일을 수정하지 않습니다. 완료 판정 계약: CONTRACT에서 필수 검증의 대상·방법·통과 조건을 확정한다. 통제된 컴포넌트 검증과 실제 서비스 검증은 별개이며 필수 대상 대신 다른 화면·버전의 증거를 쓰면 증거 불일치다. 필수 검증 통과일 때만 전체 완료를 보고한다. 미실행·확인 불가는 구현됨·검증 미완료, 실패는 수정 필요, 증거 불일치는 재검증 필요로 보고한다. 브라우저 불필요가 계약상 명확하면 브라우저 미실행만으로 보류하지 않는다. 기존 실패는 동일 기준의 비교 근거가 있을 때만 신규 실패와 구분한다. 코드 승인과 전체 완료를 구분하며 미충족 검증·사유·다음 행동 한 가지를 명시한다. 이 안내는 물리적 차단을 보장하지 않는다. task의 필수 검증이 미충족이면 본문 완료 주장 금지, outcome=blocked 및 reason_code=verification_incomplete로 보고하고 충족한 경우에만 outcome=done을 사용한다. 검증을 수행할 수 없으면 통과로 추정하거나 필수 조건을 임의로 낮추지 않는다. 기존 기계 계약: VERDICT는 PROCEED 또는 BLOCK만 사용한다. PROCEED=>done,omc-review,false; BLOCK=>blocked,null,true (순서: outcome,next_skill,user_selection_needed). risk는 low/medium/high이고 user_selection_needed는 boolean으로 출력한다. 검증 상태 첫 문장: 최종 task 보고 첫 줄은 정확히 다음 상태 문장 하나다: 통과=필수 검증 통과·작업 완료; 미실행/확인 불가=구현됨·검증 미완료; 실패=검증 실패·수정 필요; 증거 불일치=증거 불일치·재검증 필요. 실패 > 증거 불일치 > 미실행 > 통과 순으로 판정한다. 구현 반영이 확인되지 않았으면 구현 여부 확인 불가·검증 미완료를 사용한다. 첫 줄에 괄호나 부분 완료 주장을 붙이지 않는다. blocked 보고에는 작업 완료/전체 완료/구현 완료라는 부분 완료 제목을 쓰지 말고 확인된 반영 사실은 별도 줄의 구현 상태: 반영됨으로 쓴다.

## PHASE 2 ▸ DESIGN — 입력/출력 계약 / 실패·에러 정책 / 영향받는 파일: decision / risk / next_action: 구현 상태 / 변경 위험도 / 다음 스킬 1개; 공통 결정표: stage=task / outcome=blocked|done / user_selection_needed=yes|no; 계약이 약한 작업도 빈칸 없이 적고, 작은 후속 수정도 영향 파일과 실패 정책은 생략하지 않습니다.

## PHASE 3 ▸ RED — 테스트 파일 / 테스트 케이스 / 실제 FAIL 출력: `python3 scripts/omc_pipeline_guard.py red-done <테스트파일> --evidence-file <RED근거JSON>` — FAIL 출력과 `red-done` 등록 없이 구현 파일을 만들지 않습니다.

## PHASE 4 ▸ GREEN — 구현 파일 / 핵심 변경 / 테스트 PASS / 기존 회귀 없음: 최소 구현만 먼저 합니다.

## PHASE 5 ▸ REFACTOR — 정리 항목 / 리팩터링 후 PASS: 이름, 중복, 책임만 정리합니다.

## PHASE 6 ▸ TDD GATE — `python3 scripts/omc_tdd_check.py --staged`; 실패하면 수정 후 다시 확인합니다.

## PHASE 7 ▸ COMPOUND ENGINEERING — `python3 scripts/omc_lesson.py add -i`로 교훈 기록. 막히면 `교훈 없음`을 명시합니다.

Handoff — 모든 단계가 끝나면 `$omc-review`로 넘깁니다.


## 완료 보고 생성 게이트 — 실행 기록과 native 호출 영수증은 별개입니다. 영수증이 없으면 `python3 scripts/omc_native_red_evidence.py diagnose --target .`로 읽기 전용 진단을 확인합니다. `receipts`의 배열 형식은 유지합니다. 명령은 한 줄로 단독 실행하며 끝의 `2>&1` 한 개만 허용합니다. 기록 삭제·덮어쓰기나 runner 중첩 재실행은 하지 않습니다. 최종 공식 상태는 `complete-task` 출력 그대로 사용하고, 수집 결과의 `report-comparison.txt`에서 공식 판정과 모델 원문의 상태 불일치를 별도로 확인합니다. 이 안내는 모델 준수나 실제 실행 성공을 보장하지 않습니다.

승인된 기준을 `approve-checks --file <JSON>`으로 등록하고 CONTRACT에 같은 검증 ID를 고정합니다. RED는 기존 실패와 구분한 실제 새 실패를 `red-done <테스트파일> --evidence-file <RED근거JSON>`으로 등록합니다. 검증 직전 `verification-template`을 생성해 실행 결과를 기록하고 `complete-task --verification-file <내부 경로>`의 상태·기계 판정을 그대로 사용합니다. 필수 검증을 임의 제외하지 않습니다. 승인 기준에 `validation_commands_policy_sha256`가 있으면 `checks.tests.validation_receipt`에 고정 tests 실행이 반환한 `{path, sha256}`를 연결합니다. 설명문이나 `status=passed`로 원문 판정을 바꾸지 않습니다. 누락·구분 불가·대상/해시/상태 불일치는 완료로 인정하지 않습니다. 이 연결이 없는 일반 작업은 기존 형식을 유지합니다. 실행 전 필수 상세: [승인 기준·RED JSON·스냅샷·폴더 링크 정책](references/verification.md)을 읽습니다. 기록은 실행 진위·요구의 의미 관련성 인증이 아니며, 정상 내부 폴더 링크는 결속하고 외부·순환·검사 제외 링크는 거부합니다.

## 다음 추천

- 우선순위는 항상 `현재 병목 > 기본 파이프라인`; 주추천 1개만 제시, 우선순위: 구현 완료 + 게이트 통과면 `$omc-review`
- 구현 완료 + 사용자가 일단 결과만 확인하려는 상태면 내부 라우팅은 사용자 선택 대기, 화면에는 `결과를 확인한 뒤 리뷰 진행 여부를 알려주세요.`
- 실패 원인 불명 → `$omc-investigate`. 자동으로 진행하지는 않습니다.

## ⛔ 자동 진입 금지 — 이 스킬이 완료된 후 자동으로 다음 스킬을 실행하지 않고, 사용자가 명시적으로 다음 스킬을 요청할 때까지 멈추고 기다린다.


## 최종 보고와 작업 기록의 구분 — PHASE 7 기록은 최종 답변에 붙이지 않습니다. 단계별 기록과 교훈은 작업 중 처리하고, 사용자 최종 보고는 상태·변경·검증·다음 행동만 간결하게 작성합니다. complete-task의 별도 기록을 먼저 검증하고 한글 stdout만 최종 답변에 제공합니다. 기계 판정은 불변 기록으로 보존합니다. 이 최종 보고 계약이 위 단계별 양식보다 우선합니다.


## 검증된 동일 커밋 이관의 별도 완료 경로

이 스킬의 구현 작업과 종류가 없는 기존 승인 기준은 계속 `complete-task`와 현재 작업 RED를 요구합니다.
`benchmark_maintenance` 같은 work_class나 호출자의 RED 생략 선언으로 예외를 선택하지 않습니다.
이 기능 자체를 구현·수정하는 작업도 기존 구현 경로로 검증합니다.

새 동일 커밋 이관은 최초 승인 기준에 `completion_kind=verified_commit_transfer`와 `transfer` 정책을 고정한 경우에만 별도 판정합니다.
같은 작업의 개정·보존 역할 전환으로 종류나 이관 정책을 바꾸지 않습니다.

승인 후 순서(명령의 `--target`은 격리 운영 작업 폴더이며 실제 대상은 승인된 정책에서만 읽음):

```bash
python3 scripts/omc_pipeline_guard.py --target <운영폴더> contract-done --required-checks <승인ID>
python3 scripts/omc_pipeline_guard.py --target <운영폴더> register-transfer-baseline
# 등록 성공을 확인한 뒤 별도 승인된 실제 이관 절차 수행
python3 scripts/omc_pipeline_guard.py --target <운영폴더> complete-transfer --verification-file <운영폴더내검증JSON>
```

기준 등록은 대상 HEAD가 아직 승인된 기준 커밋일 때만 1회 수행합니다. 이미 대상 커밋이면 사후 등록을 거부합니다.
이관 전후 Git 상태·원래 구현의 검증 통과·리뷰 READY·요청 범위·원본/대상 내용·권한·보존 증거를 확인합니다.
등록 중 HEAD·인덱스·보존 대상이 바뀌면 기준을 게시하지 않습니다.
정확한 대상 커밋과 단일 부모, 승인된 전체 경로, 작업 ID·대상 Git/인덱스 경로를 결속합니다.
검증 근거 없음·failed·unrun·mismatch·현재 기준 만료·범위 불일치는 RED 제외 자격이 아닙니다.
원래 구현은 기존 판정기로 통과를 확인하며 이관의 새 RED만 제외합니다.

보존 예외는 승인된 커밋 diff의 정확한 파일·인덱스 전환과, 명시적으로 승인한
`.omc/state/review-snapshots/current.json`의 유효한 새 receipt 참조뿐입니다.
기존 receipt·snapshot·검증 원문은 불변이며 새 리뷰 JSON 기록만 정해진 영역에 추가할 수 있습니다.
다른 변경·플래그 차이·넓은 임의 제외 목록·다른 작업 기준 재사용은 차단합니다.

CLI와 수집기는 동일 승인 종류·공용 판정과 실제 반환 경로를 확인합니다.
이관 절차는 CONTRACT → 기준 등록 성공 → complete-transfer 반환이며 구현 절차의 RED를 대신 실행한 것으로 기록하지 않습니다.
이관 완료는 기능·배포·실제 서비스 검증 성공을 의미하지 않습니다.
기존 BLOCK 원문은 보존하며 과거 이관을 새 사전 기준으로 소급 완료하지 않습니다.
지원하지 않는 증거·분할/희소 인덱스·링크·등록 예산 초과는 통과로 추정하지 않습니다.
로컬 해시·Git·기록은 악의적 로컬 변조나 인간 승인·실행 진위의 인증이 아닙니다.

기계 기록의 단계: stage=task. 아래 별도 기록 규칙이 이전 출력 예시보다 우선한다.


## 승인된 비구현 캐시 정리의 완료 경로
캐시 정리는 최초 승인에 `completion_kind=verified_nonimplementation`·`maintenance`·`profile=cache_cleanup`이 고정된 경우만 `complete-maintenance`를 사용한다. 구현 RED 면제가 아니다. 실행 전 `references/verification.md`의 「승인된 비구현 캐시 정리의 완료 경로」와 「등록 실패의 제한된 복구 · baseline/v2와 operation/v2」를 반드시 읽고 기준·소유권·실제 prefix·해시·실패 중단 조건을 그대로 적용한다. 승인·기준·ready·TTL·원문을 임의 복구하거나 소급 변경하지 않는다.

릴리스 의도가 있으면 [최종 후보 준비](references/verification.md#최종-후보-준비--릴리스-의도가-있을-때) 상세를 먼저 읽고 `omc_ship_preflight.py diagnose`로 후보·리뷰·품질 연결을 대조한다. 준비 READY는 검사 통과가 아니다.


검증된 `omc-autopilot-mission/v2` 내부에서만 [승인된 단일 작업 V0 내부 호출](references/verification.md#승인된-단일-작업-v0-내부-호출)을 먼저 읽고 부모 실행기의 단계 전환을 따른다. 일반 대화의 자동 진입 금지는 유지한다.

## Machine output contract
별도 기록 모드 `detached_v1`을 명시적으로 사용한다. `omc-output/v1`의 stage/outcome/risk/next_skill/user_selection_needed/reason_code·VERDICT·기존 라우팅 의미는 바꾸지 않는다. `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`는 내부 판정 원문에만 두고 사용자 최종 답변에 붙이지 않는다. 한글 판정·이유·검증·다음 행동만 표시한다.
먼저 `python3 scripts/omc_output_contract.py capabilities`와 `python3 scripts/omc_workflow_validation.py capabilities`로 발행자·소비자의 지원을 각각 확인한다. 실행 전에 모드·현재 작업/세션·단계·보고 ID·마감과 명시 기록 경로를 고정한다. 문맥이 없으면 `prepare --target . --stage <단계> --report-id <새ID> --budget-sec <승인된남은초>`로 현재 상태에서 새 문맥을 만들고 반환된 context/path·SHA를 호출자 실행 메타데이터에 보존한다. 숫자 예산을 임의 연장하거나 이전 보고 ID를 재사용하지 않는다.
일반 plan/review/critique/investigate/ship 선언은 내부 원문을 작업의 .omc 안에 저장한 뒤 `python3 scripts/omc_output_contract.py publish --target . --output-context <문맥경로> --output-context-sha256 <해시> --source-file <내부원문> --source-sha256 <해시>`로 발행한다. 이는 모델 선언이며 해시 일치가 검증 통과나 사용자 승인 진위를 인증하지 않는다.
실제 task 완료는 기존 `complete-task --verification-file <내부검증>`에 `--output-mode detached_v1 --output-context <문맥경로> --output-context-sha256 <해시>`를 추가한다. 이관은 승인된 complete-transfer 경로를 유지한다. 일반 publish로 완료 가드의 발행 주체를 지정할 수 없다.
사용자 최종 답변은 발행 CLI의 한글 stdout과 같은 바이트로 제공한다. 경로/해시는 사용자 답변 꼬리표 대신 호출 인자와 실행 메타데이터로 전달한다. 수집기는 명시 manifest 경로/해시·현재 상태·native 호출/반환·실제 최종 원문을 독립 대조한다. CLI 표시 검사·수집기 fixture·실제 모델의 자유 응답 준수는 별도 증거다.
업무 차단 종료 코드1도 정상 판정으로 기록한다. 전달 실패 코드2는 업무 실패와 다르다. 저장·봉인·출력 실패 시 한글 오류만 알리고 완료/승인·자동 후속·구형 fallback을 금지한다. 실패 기록도 저장할 수 없으면 stderr·종료 원문만 보존한다. 봉인 후 표시 실패를 이유로 기존 manifest나 verdict를 수정하지 않는다.
구형 legacy_inline으로 시작한 입력만 기존 parser로 읽는다. 신형 기록 누락·변조·다른 세션·모드 혼합·표시 불일치는 보류하며 구형으로 우회하지 않는다. 구형 도구에서 신형 안내를 실행할 수 없으면 호출 전에 한글 지원 불가를 알린다. 이 안내는 플랫폼 화면 숨김이나 모델 준수를 물리적으로 보장하지 않는다.
