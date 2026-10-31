---
skill_name: omc-critique
description: "계획·코드·전략에 대한 냉정한 비판 리뷰. 트리거: 이 계획 맞아, 비판해줘, 냉정하게 봐줘, 틀린 거 찾아줘, 약점이 뭐야, 플랜 리뷰해줘. 칭찬 금지, 약점·가정·리스크·누락만 찾는다."
---

# OMC Critique

Pre-mortem 전용 스킬입니다. 승인보다 실패 가능성 탐지가 목적이며, 칭찬 금지, 근거와 대안만 남깁니다. 기본 속도는 normal이며, 실패 가능성·누락·리스크 탐지가 우선입니다.

## Phase 0. 컨텍스트 감지

```bash
git diff --stat HEAD 2>/dev/null | head -20
git ls-files --others --exclude-standard 2>/dev/null | head -20
find . -newer .git/index -not -path './.git/*' \( -name '*.md' -o -name '*.py' -o -name '*.ts' \) 2>/dev/null | head -20
ls .omc/tasks/ 2>/dev/null
cat .omc/notepad.md 2>/dev/null
python3 scripts/omc.py state status --target .
```

- `.omc/runs`, `.omc/lessons`, `.omc/pipeline_run_result.json` 기본 제외 | 계획/전략/문서/스킬 평가는 PLAN 모드 | 실제 코드 변경/신규 파일/diff는 CODE 모드 | 둘 다 있으면 사용자가 범위를 고르게 합니다.
사용자에게 보여줄 것: CRITICAL / WARNING / MINOR, 근거 / 대안, 권고 조치 / VERDICT | 시스템이 암묵적으로 처리: 컨텍스트 감지 / 범위 밖 제외 / tracked/untracked 최근 파일 스캔

## PLAN 모드 — 가정 / 일정 / 누락 / 실패 조건 / 의존성 / 리스크 / 성공 지표 / 범위 / 축소안 / Red Team

## CODE 모드 — 조용한 실패 / 테스트 누락 / 회귀 경로 / 최악 성능 / 유지보수 / 파급효과 / TDD 상태 / 빠진 일

## 출력 규칙

- 모든 지적은 `근거:`와 `대안:`을 포함 | 근거 없는 비판 / 대안 없는 비판 / 범위 밖 비판 금지 | 확신이 없으면 HOLD/BLOCK 쪽 | `omc-review`처럼 품질 승인하지 말고 실패 가능성을 먼저 찾습니다.
- 이벤트가 있을 때만 `reroute 이유 / delay 이유 / 재개 조건`을 적고, HOLD/REVISE면 생략하지 않습니다.

## Verdict

- PLAN: CRITICAL 있음 → HOLD / WARNING 있음 → REVISE / MINOR만 있음 → PROCEED WITH CAUTION / 이슈 없음 → PROCEED | CODE: CRITICAL 있음 → BLOCK / WARNING 있음 → REVISE / MINOR만 있음 → APPROVE WITH NOTES / 이슈 없음 → APPROVE

```text
CRITICAL:
- 근거: ...
  대안: ...

WARNING:
- 근거: ...
  대안: ...

MINOR:
- 근거: ...
  대안: ...

reroute 이유: ...
delay 이유: ...
재개 조건: ...

권고 조치:
1. ...
2. ...
3. ...

판정: 보류 / 수정 필요 / 진행 가능 / 승인 / 승인·보완 제안 있음 (기계 값은 별도 기록)
```

## 비용 체크포인트 — REVISE / HOLD 판정 후 `$omc-task` 진입 전 `변경 비용 추정`을 확인합니다.

```text
변경 비용 추정: 영향 파일 수 ___개 / 예상 변경 LOC ___줄 / 실질 효과 HIGH / MED / LOW
```

- 실질 효과 LOW + MINOR 지적만 → 건너뛰기 권장 | MED/HIGH → `$omc-plan`으로 범위 확정 후 `$omc-task`
- 같은 REVISE/HOLD 사유가 반복될 때만 이 체크포인트를 다시 엽니다. 반복 근거가 없으면 여기서 중단하고 현 상태를 유지합니다.
- ⛔ REVISE/HOLD 판정 직후 `$omc-task`를 바로 실행하지 않습니다. 이 체크포인트를 출력하고, 사용자가 "진행"을 명시할 때까지 멈춥니다.

## 이후 액션 — HOLD/REVISE + 변경 비용 MED/HIGH → `$omc-plan`으로 재설계 | HOLD/REVISE + 변경 비용 LOW + MINOR만 → 사용자가 건너뛰기 여부를 결정 | 코드 품질 확인은 `$omc-review`

## 다음 추천

- 주추천 1개, 우선순위: HOLD/REVISE면 `$omc-plan`
- PROCEED + PLAN 모드 + 범위 고정 → 사용자 선택 대기 (`$omc-task`)
- PROCEED + CODE 모드 → 사용자 선택 대기 (`$omc-review`)
- 자동으로 진행하지는 않습니다.

기계 기록의 단계: stage=critique-plan|critique-code. 아래 별도 기록 규칙이 이전 출력 예시보다 우선한다.

## Machine output contract
별도 기록 모드 `detached_v1`을 명시적으로 사용한다. `omc-output/v1`의 stage/outcome/risk/next_skill/user_selection_needed/reason_code·VERDICT·기존 라우팅 의미는 바꾸지 않는다. `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`는 내부 판정 원문에만 두고 사용자 최종 답변에 붙이지 않는다. 한글 판정·이유·검증·다음 행동만 표시한다.
먼저 `python3 scripts/omc_output_contract.py capabilities`와 `python3 scripts/omc_workflow_validation.py capabilities`로 발행자·소비자의 지원을 각각 확인한다. 실행 전에 모드·현재 작업/세션·단계·보고 ID·마감과 명시 기록 경로를 고정한다. 문맥이 없으면 `prepare --target . --stage <단계> --report-id <새ID> --budget-sec <승인된남은초>`로 현재 상태에서 새 문맥을 만들고 반환된 context/path·SHA를 호출자 실행 메타데이터에 보존한다. 숫자 예산을 임의 연장하거나 이전 보고 ID를 재사용하지 않는다.
일반 plan/review/critique/investigate/ship 선언은 내부 원문을 작업의 .omc 안에 저장한 뒤 `python3 scripts/omc_output_contract.py publish --target . --output-context <문맥경로> --output-context-sha256 <해시> --source-file <내부원문> --source-sha256 <해시>`로 발행한다. 이는 모델 선언이며 해시 일치가 검증 통과나 사용자 승인 진위를 인증하지 않는다.
실제 task 완료는 기존 `complete-task --verification-file <내부검증>`에 `--output-mode detached_v1 --output-context <문맥경로> --output-context-sha256 <해시>`를 추가한다. 이관은 승인된 complete-transfer 경로를 유지한다. 일반 publish로 완료 가드의 발행 주체를 지정할 수 없다.
사용자 최종 답변은 발행 CLI의 한글 stdout과 같은 바이트로 제공한다. 경로/해시는 사용자 답변 꼬리표 대신 호출 인자와 실행 메타데이터로 전달한다. 수집기는 명시 manifest 경로/해시·현재 상태·native 호출/반환·실제 최종 원문을 독립 대조한다. CLI 표시 검사·수집기 fixture·실제 모델의 자유 응답 준수는 별도 증거다.
업무 차단 종료 코드1도 정상 판정으로 기록한다. 전달 실패 코드2는 업무 실패와 다르다. 저장·봉인·출력 실패 시 한글 오류만 알리고 완료/승인·자동 후속·구형 fallback을 금지한다. 실패 기록도 저장할 수 없으면 stderr·종료 원문만 보존한다. 봉인 후 표시 실패를 이유로 기존 manifest나 verdict를 수정하지 않는다.
구형 legacy_inline으로 시작한 입력만 기존 parser로 읽는다. 신형 기록 누락·변조·다른 세션·모드 혼합·표시 불일치는 보류하며 구형으로 우회하지 않는다. 구형 도구에서 신형 안내를 실행할 수 없으면 호출 전에 한글 지원 불가를 알린다. 이 안내는 플랫폼 화면 숨김이나 모델 준수를 물리적으로 보장하지 않는다.
