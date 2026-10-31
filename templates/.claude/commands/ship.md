# /ship — 고정 후보의 준비·검사·배포를 구분한다

## 입력과 실행 범위
- 구현·문서·버전을 포함한 최종 커밋, 기준 OID, 정확한 전체 변경 목록을 먼저 고정한다. 후보가 바뀌면 이전 승인을 재사용하지 않는다.
- `source`: 원본 후보. 읽기 전용이며 세션 초기화·설정 변경·승인 포인터 쓰기를 하지 않는다.
- `control`: 저장소 밖의 이번 작업 전용 제어 폴더. 세션 생성·실행 원문·외부 시각은 여기에 기록한다.
- `candidate`: prepare가 새로 만드는 격리 후보. 원본과 경로가 겹치면 중단한다. prepare 전에 생성·설치·세션 초기화하지 않는다.
- `prepare_record`와 `check_record`: 원본·후보 밖의 서로 다른 새 경로. `preflight`는 omc-ship-preflight/v1의 8개 필드를 고정한 파일이다.
- `kit`: 승인한 OMC 스크립트가 있는 절대 경로. 아래 argv는 셸 문자열로 합치지 않고 인자 배열로 사용한다. 경로·명령·중복 인자·알 수 없는 placeholder를 검증한다.
- source의 리뷰 READY, 품질 설정 및 사용자 실행 승인(후보·범위·횟수·마감)을 확인한 뒤만 진행한다. 누락 시 원문을 보존하고 중단한다. 기록 생성은 인간 승인 자체를 인증하지 않는다.

## 승인 연결
`base`/`head`는 승인 당시 기준/최종 커밋이다. 원본 영수증·snapshot·봉인된 리뷰 원문과 현재 전체 후보를 먼저 대조한다.
`review_output`은 승인 원문의 저장소 밖 regular file이며 SHA-256도 고정한다. 승인 원문을 새로 작성해 대체하지 않는다.
`expected_path_args`는 승인된 정확한 전체 경로에 대한 `--expected-path` 쌍이다. snapshot 목록을 자동으로 승인 목록에 복사하지 않는다.
`config_sha256`는 품질 승인용 정규화된 설정 해시다. preflight의 `quality_config_sha256`는 파일 원문 해시이므로 서로 대신 쓰지 않는다.
`quality_scope_args`는 기존 명시적 실행 승인이 full 범위를 포함할 때만 `["--allow-full"]`, 아니면 빈 배열이다. 필요한 full 승인이 없으면 중단한다.
prepare는 승인 영수증을 복사하지 않는다. 격리 후보에서 아래 공식 capture→seal→record와 quality approve로 연결하고 다시 확인한다. 기존 영수증·원장·원본 포인터를 편집하거나 복사해 통과시키지 않는다.

## 공통 명령 계약
아래 블록은 Codex·Claude 안내와 직접 회귀가 공유하는 계약이다. 자동 출하 실행기나 권한 부여 API가 아니며 모델의 절차 준수를 보장하지 않는다.
각 단계가 성공하고 원문·대상·시간을 확인한 경우에만 다음 단계로 간다. `.`, 원본 작업 디렉터리, 이전 임시 후보로 대체하지 않는다.
<!-- OMC_SHIP_ENTRY_CONTRACT:BEGIN -->
```json
{
  "schema_version": "omc-ship-entry/v1",
  "total_prepare_seconds": 480,
  "budget_seconds": {"input":40,"prepare":240,"bind":40,"check":90,"finalize":70},
  "steps": [
    {"id": "control_init", "stage": "input", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc.py", "state", "init", "--target", "{control}"]},
    {"id": "control_guard", "stage": "input", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_guard.py", "sync-require", "--target", "{control}", "--mode", "autopilot", "--title", "ship", "--request", "approved frozen ship candidate", "--roles", "directive", "--for", "ship"]},
    {"id": "source_review", "stage": "input", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_review_snapshot.py", "validate-ship", "--target", "{source}"], "json_status": "READY"},
    {"id": "source_quality", "stage": "input", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_quality_gate.py", "--target", "{source}", "status"], "json_status": "ready"},
    {"id": "prepare", "stage": "prepare", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_ship_preflight.py", "prepare", "--contract", "{preflight}", "--target", "{candidate}", "--record-dir", "{prepare_record}"], "json_status": "baseline_verified"},
    {"id": "capture", "stage": "bind", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_review_snapshot.py", "capture-review", "--target", "{candidate}", "--base-commit", "{base}", "--candidate-commit", "{head}"]},
    {"id": "seal", "stage": "bind", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_review_snapshot.py", "seal-review-output", "--target", "{candidate}", "--review-snapshot", "{capture.snapshot_path}", "--review-snapshot-sha256", "{capture.snapshot_sha256}", "--review-output", "{review_output}"]},
    {"id": "record", "stage": "bind", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_review_snapshot.py", "record-review", "--target", "{candidate}", "--review-snapshot", "{capture.snapshot_path}", "--review-snapshot-sha256", "{capture.snapshot_sha256}", "--review-evidence", "{seal.evidence_path}", "--review-evidence-sha256", "{seal.evidence_sha256}", "--verdict", "APPROVE", "{expected_path_args}"]},
    {"id": "approve", "stage": "bind", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_quality_gate.py", "--target", "{candidate}", "approve", "--config-sha256", "{config_sha256}", "{quality_scope_args}"]},
    {"id": "check", "stage": "check", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_ship_preflight.py", "check", "--contract", "{preflight}", "--target", "{candidate}", "--record-dir", "{check_record}"], "json_status": "baseline_verified"},
    {"id": "candidate_review", "stage": "check", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_review_snapshot.py", "validate-ship", "--target", "{candidate}"], "json_status": "READY"},
    {"id": "candidate_quality", "stage": "check", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_quality_gate.py", "--target", "{candidate}", "status"], "json_status": "ready"},
    {"id": "tdd", "stage": "check", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_tdd_check.py", "--target", "{candidate}", "--staged"]},
    {"id": "run", "stage": "run", "cwd": "{control}", "argv": ["{python}", "{kit}/scripts/omc_quality_gate.py", "--target", "{candidate}", "run"], "json_status": "passed"}
  ]
}
```
<!-- OMC_SHIP_ENTRY_CONTRACT:END -->

## 시간·증거와 중단
- `environment_not_ready`이면 누락된 실행 파일·의존성과 preflight 요구를 한글로 알리고 품질 명령0회로 중단한다. runtime/PATH를 자동 변경하거나 준비되지 않은 일부 검사로 대체하지 않는다.
- 준비 총480초: 입력40·prepare240·승인 연결40·check 및 최종 준비 확인90·외부 정리/보관70. prepare/check 내부 정리·기록은 각각240/90초에 포함한다. 남은 단계 전체 한도와 마지막70초를 확보하지 못하면 진입하지 않는다. 자동 재시도·예산 연장 없음.
- 외부에서 T0와 단계 시작/종료·보관 완료를 기록한다. 조기 중단의 정리/보관 마감은 min(중단 시각+70초,T0+480초)다. 이번 소유 프로세스만 종료 확인하며 미확인이면 부분 증거만 보존한다.
- prepare/check는 실제 종료 코드0·해당 operation의 stdout baseline_verified·cleanup_confirmed=true·evidence_complete=true·계약/후보/기준/경로 일치·시간 준수를 모두 요구한다. stdout/stderr/종료 코드와 디스크 원문·해시를 호출별 보존한다. final.json의 requires_cli_completion·observed_check_status나 파일 존재만으로 성공을 판단하지 않는다.
- 전체 검사 run은 별도로 승인한 총마감·횟수·정리/보관 예산이 있어야 한다. 누락 시0회다. 실제 품질 명령·환경은 docs/omc_quality_gates.md의 계약과 승인 원문을 사용하며 runtime/PATH를 자동 변경하지 않는다.
- 준비 실패·승인 불일치·증거/종료 미확인에서는 품질 명령0회다. 필수 검사 실패·시간 초과·허용하지 않은 생략은 통과로 바꾸지 않는다. 기존 실패·미실행·구분 불가 원문을 보존한다.

## 보고와 권한 경계
- 설정 승인됨: quality status=ready. 실행 준비 전체를 뜻하지 않는다.
- 실행 준비됨: 후보·기준·환경·승인·정리/증거를 종합 확인한 스킬 보고다.
- 검사 통과: 실제 필수 검사·종료·보관 확인. 배포 완료: 별도 승인된 배포 실행과 확인.
기존 CLI status·종료 코드는 수정하지 않고 보고용 판정을 별도 표시한다. 준비 불가 / 승인 불일치 / 코드 실패 / 증거·종료 미확인을 구분한다.
비밀값 검사는 고정 후보의 diff·untracked에서 실제 비밀값 노출을 확인한다. 범위 밖 기존 작업을 이번 커밋에 섞지 않는다.
원문상 실패·불확실성을 성공으로 완화하지 않는다. push·PR·태그·릴리스·사용처 설치는 각각 명시적 승인이 필요하며 ship 준비·검사 승인에서 추정하지 않는다.
완료 후 다음 스킬로 자동 진입하지 않는다. SHIP READY이면 승인된 다음 행동의 선택을 기다리고, 실제 실패 원인이 확인된 경우에만 해당 조사·수정 경로를 제안한다.

## 배포 후 기록
실제 배포가 완료된 경우에만 기존 작업 규모 기록을 유지한다: `python3 scripts/omc_cost.py record --executor claude-code --task "$ARGUMENTS"`. 토큰 원문이 있으면 실제 모델과 `--llm-json` 경로를 함께 지정하며 추정값을 실측으로 쓰지 않는다. `python3 scripts/omc_cost.py report`로 기록을 확인한다.
배포에서 놓친 요구나 반복 방지 규칙이 있으면 기존 교훈 절차로 남긴다. 준비·검사만 수행한 경우 배포 후 기록을 실행하지 않는다.

## 다음 추천
- 주추천 1개만 제시한다. 우선순위는 현재 차단 원인이다.
- BLOCKED + 테스트·회귀 실패면 `$omc-investigate` 한 가지를 제안한다.
- BLOCKED + 신규 테스트·TDD 누락이면 `$omc-task` 한 가지를 제안한다.
- SHIP READY이면 승인된 다음 행동의 선택을 기다린다.
- 실제 배포 후에는 `$omc-retro` 한 가지를 제안한다.
- 자동으로 진행하지는 않습니다. 사용자가 다음 스킬을 명시하기 전에는 진입하지 않는다.

기계 기록의 단계: stage=ship. 아래 별도 기록 규칙이 이전 출력 예시보다 우선한다.

## Machine output contract
별도 기록 모드 `detached_v1`을 명시적으로 사용한다. `omc-output/v1`의 stage/outcome/risk/next_skill/user_selection_needed/reason_code·VERDICT·기존 라우팅 의미는 바꾸지 않는다. `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`는 내부 판정 원문에만 두고 사용자 최종 답변에 붙이지 않는다. 한글 판정·이유·검증·다음 행동만 표시한다.
먼저 `python3 scripts/omc_output_contract.py capabilities`와 `python3 scripts/omc_workflow_validation.py capabilities`로 발행자·소비자의 지원을 각각 확인한다. 실행 전에 모드·현재 작업/세션·단계·보고 ID·마감과 명시 기록 경로를 고정한다. 문맥이 없으면 `prepare --target . --stage <단계> --report-id <새ID> --budget-sec <승인된남은초>`로 현재 상태에서 새 문맥을 만들고 반환된 context/path·SHA를 호출자 실행 메타데이터에 보존한다. 숫자 예산을 임의 연장하거나 이전 보고 ID를 재사용하지 않는다.
일반 plan/review/critique/investigate/ship 선언은 내부 원문을 작업의 .omc 안에 저장한 뒤 `python3 scripts/omc_output_contract.py publish --target . --output-context <문맥경로> --output-context-sha256 <해시> --source-file <내부원문> --source-sha256 <해시>`로 발행한다. 이는 모델 선언이며 해시 일치가 검증 통과나 사용자 승인 진위를 인증하지 않는다.
실제 task 완료는 기존 `complete-task --verification-file <내부검증>`에 `--output-mode detached_v1 --output-context <문맥경로> --output-context-sha256 <해시>`를 추가한다. 이관은 승인된 complete-transfer 경로를 유지한다. 일반 publish로 완료 가드의 발행 주체를 지정할 수 없다.
사용자 최종 답변은 발행 CLI의 한글 stdout과 같은 바이트로 제공한다. 경로/해시는 사용자 답변 꼬리표 대신 호출 인자와 실행 메타데이터로 전달한다. 수집기는 명시 manifest 경로/해시·현재 상태·native 호출/반환·실제 최종 원문을 독립 대조한다. CLI 표시 검사·수집기 fixture·실제 모델의 자유 응답 준수는 별도 증거다.
업무 차단 종료 코드1도 정상 판정으로 기록한다. 전달 실패 코드2는 업무 실패와 다르다. 저장·봉인·출력 실패 시 한글 오류만 알리고 완료/승인·자동 후속·구형 fallback을 금지한다. 실패 기록도 저장할 수 없으면 stderr·종료 원문만 보존한다. 봉인 후 표시 실패를 이유로 기존 manifest나 verdict를 수정하지 않는다.
구형 legacy_inline으로 시작한 입력만 기존 parser로 읽는다. 신형 기록 누락·변조·다른 세션·모드 혼합·표시 불일치는 보류하며 구형으로 우회하지 않는다. 구형 도구에서 신형 안내를 실행할 수 없으면 호출 전에 한글 지원 불가를 알린다. 이 안내는 플랫폼 화면 숨김이나 모델 준수를 물리적으로 보장하지 않는다.
