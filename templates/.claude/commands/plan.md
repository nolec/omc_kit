# /plan — OMC 설계·계획

구현 전 범위와 DoD를 확정하고, 작은 작업은 lite로 압축하되 위험 작업은 full로 유지합니다.

## Phase 0. 상태 확인

주입된 OMC 세션 문맥을 먼저 사용합니다. 현재 요청과 동기화되어 있으면 상태 명령을 실행하지 않습니다. `scripts/omc.py`가 제공된 정확한 context path에 없으면 격리 입력으로 보고 탐색·상태 명령을 생략합니다. 문맥이 없거나 오래됐고 해당 경로가 명시적으로 제공된 경우에만 아래 동기화를 한 번 실행합니다.

```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-plan" --request "<현재 작업 한 줄 요약>" --roles analysis
```

AGENTS.md Tier 1 작업은 아래 Phase 1 요구사항을 CONTRACT 입력으로 사용합니다.

## Phase 1. 요구사항 CONTRACT

- 목표:
- 범위 (포함):
- 범위 (제외):
- DoD:
- 제약:
- 사용자 컨펌:

사용자에게 보여줄 단계: CONTRACT / 최소 설계 / TDD 태스크 / `/task` handoff
시스템이 암묵적으로 처리: 자명한 재안내 / 선택 스킬 추천 / 반복 코칭

## Phase 2. 최소 설계

- 입력:
- 출력:
- 성공 지표:
- 실패 정책:
- 영향받는 파일:

출력 모드:
- `plan full`: CONTRACT + 최소 설계 + 다중 TDD 태스크
- `plan lite`: CONTRACT + 최소 설계 + 태스크 2개 이하
- lite 조건: 기존 파일 중심, 검증 명령 1개로 충분, 범위를 한 문장으로 설명 가능
- full 조건: 새 파일 또는 신규 파일 생성 / API 또는 시그니처 변경 / 3개 이상 파일 / 검증 명령 축약 불가 / 범위가 불명확
- 애매하면 full
- lite가 쓰였지만 설명이 약하면 `full 재계획`
- 현재 dirty 변경과 이번 계획 범위는 분리해서 다룹니다.

## Phase 3. TDD 태스크 분해

- 출력 절약: 요구사항은 짧은 ID로 고정하고 `supports에는 ID만` 재사용 / 같은 target과 검증 목적이고 의존성 경계를 넘지 않을 때만 병합 / 확인하지 않은 예시 명령 금지 / assumptions·decisions는 구현을 막는 항목만

```text
plan full
태스크 1: [기능]
  RED    : [실패 테스트 파일 + 케이스]
  GREEN  : [최소 구현 파일]
  VERIFY : [검증 커맨드]

plan lite
태스크 1: [핵심 변경]
  RED    : [실패 테스트 파일 + 케이스]
  GREEN  : [최소 구현 파일]
  VERIFY : [검증 커맨드]
```

## Phase 4. 세션 기록

사용자 컨펌 완료 전에는 아래 명령을 실행하지 않습니다.

```bash
python3 scripts/omc.py state confirm --target .
```

confirm 후에만 `/task`로 넘깁니다.

## 다음 추천

- 주추천 1개만 제시: 범위 고정 + 컨펌 완료면 `/task`
- 범위 불명확 또는 흔들림 → `/critique` / `/office-hours`
- 자동으로 진행하지는 않습니다.

---

계획할 작업: $ARGUMENTS

기계 기록의 단계: stage=plan. 아래 별도 기록 규칙이 이전 출력 예시보다 우선한다.

## Machine output contract
별도 기록 모드 `detached_v1`을 명시적으로 사용한다. `omc-output/v1`의 stage/outcome/risk/next_skill/user_selection_needed/reason_code·VERDICT·기존 라우팅 의미는 바꾸지 않는다. `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`는 내부 판정 원문에만 두고 사용자 최종 답변에 붙이지 않는다. 한글 판정·이유·검증·다음 행동만 표시한다.
먼저 `python3 scripts/omc_output_contract.py capabilities`와 `python3 scripts/omc_workflow_validation.py capabilities`로 발행자·소비자의 지원을 각각 확인한다. 실행 전에 모드·현재 작업/세션·단계·보고 ID·마감과 명시 기록 경로를 고정한다. 문맥이 없으면 `prepare --target . --stage <단계> --report-id <새ID> --budget-sec <승인된남은초>`로 현재 상태에서 새 문맥을 만들고 반환된 context/path·SHA를 호출자 실행 메타데이터에 보존한다. 숫자 예산을 임의 연장하거나 이전 보고 ID를 재사용하지 않는다.
일반 plan/review/critique/investigate/ship 선언은 내부 원문을 작업의 .omc 안에 저장한 뒤 `python3 scripts/omc_output_contract.py publish --target . --output-context <문맥경로> --output-context-sha256 <해시> --source-file <내부원문> --source-sha256 <해시>`로 발행한다. 이는 모델 선언이며 해시 일치가 검증 통과나 사용자 승인 진위를 인증하지 않는다.
실제 task 완료는 기존 `complete-task --verification-file <내부검증>`에 `--output-mode detached_v1 --output-context <문맥경로> --output-context-sha256 <해시>`를 추가한다. 이관은 승인된 complete-transfer 경로를 유지한다. 일반 publish로 완료 가드의 발행 주체를 지정할 수 없다.
사용자 최종 답변은 발행 CLI의 한글 stdout과 같은 바이트로 제공한다. 경로/해시는 사용자 답변 꼬리표 대신 호출 인자와 실행 메타데이터로 전달한다. 수집기는 명시 manifest 경로/해시·현재 상태·native 호출/반환·실제 최종 원문을 독립 대조한다. CLI 표시 검사·수집기 fixture·실제 모델의 자유 응답 준수는 별도 증거다.
업무 차단 종료 코드1도 정상 판정으로 기록한다. 전달 실패 코드2는 업무 실패와 다르다. 저장·봉인·출력 실패 시 한글 오류만 알리고 완료/승인·자동 후속·구형 fallback을 금지한다. 실패 기록도 저장할 수 없으면 stderr·종료 원문만 보존한다. 봉인 후 표시 실패를 이유로 기존 manifest나 verdict를 수정하지 않는다.
구형 legacy_inline으로 시작한 입력만 기존 parser로 읽는다. 신형 기록 누락·변조·다른 세션·모드 혼합·표시 불일치는 보류하며 구형으로 우회하지 않는다. 구형 도구에서 신형 안내를 실행할 수 없으면 호출 전에 한글 지원 불가를 알린다. 이 안내는 플랫폼 화면 숨김이나 모델 준수를 물리적으로 보장하지 않는다.
