---
skill_name: omc-investigate
description: "버그 디버그·에러 추적·근본 원인 파악. 트리거: 디버그해줘, 왜 실패해, 원인 찾아줘, 에러 추적, 버그 잡아줘. 근본 원인→패턴 분석→가설 검증→수정 4단계. 증상 패치 금지."
---

# OMC Investigate

수정 전 근본 원인을 확인합니다. 사용자에게 보여줄 것: 현상 / 가설 / 검증 / 근본 원인 / FIX PLAN / 시스템이 암묵적으로 처리: 증상 패치 금지, 추측 금지, 데이터·로그·코드 근거 부족 시 중단, 기각 시 PHASE 2 복귀

## Phase 0. 컨텍스트

```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-investigate" --request "<현재 작업 한 줄 요약>" --roles analysis
git diff --stat HEAD 2>/dev/null | head -20
git log --oneline -5 2>/dev/null
git ls-files --others --exclude-standard 2>/dev/null | head -20
python3 scripts/omc.py state status --target .
```

에러 키워드가 있으면 `rg "[에러 키워드]"`로 관련 파일을 찾습니다.

## PHASE 1. ROOT CAUSE

- 현상: 로그, 에러, 재현 조건 / 기대 동작 / 재현 불가: 로그 추가 또는 간헐적 발생 패턴 기록

## PHASE 2. PATTERN ANALYSIS

- 원인 가설을 우선순위 순으로 적습니다. 최소 1개, 필요하면 4개 이상 추가합니다. 예: 1. [높음] 2. [중간] 3. [낮음]

## PHASE 3. HYPOTHESIS TESTING

- 검증 커맨드:
- 결과: 확인됨 / 기각됨
- 근본 원인:

가설이 모두 기각되면 새 가설을 추가해 PHASE 2로 돌아간다. 3회 연속 기각이면 `$omc-plan`으로 범위를 재설계합니다.

## PHASE 4. FIX PLAN

근본 원인 확정 전 구현 금지. 수정이 필요하면 `$omc-task`로 넘깁니다.

- 수정 대상:
- 검증 커맨드:
- 회귀 위험:

- 원인 확정 + 수정 필요 → `$omc-task`
- 수정 후 품질 확인 → `$omc-review`
- 아키텍처/범위 문제 → `$omc-ceo-review`

## 다음 추천

- 주추천 1개만 제시: 현재 근거에 가장 가까운 후속 1개를 먼저 말합니다.
- 재현 조건/검증 결과/근본 원인이 아직 비면 → 사용자 선택 대기
- 원인 확정 + 수정 필요 → `$omc-task`
- 수정 후 품질 확인 → `$omc-review`
- 아키텍처/범위 문제 → `$omc-ceo-review`
- 자동으로 진행하지는 않습니다.

- 모든 LLM 공통 출력 형식: 현상, 가설, 검증, 근본 원인, FIX PLAN 순서 고정
- 입력 부족 시 중단: 재현 조건 또는 검증 결과가 비면 수정 단계로 넘기지 않습니다. Machine output contract는 `omc-output/v1`, `stage=investigate`, JSON 필드 `outcome/risk/next_skill/user_selection_needed/reason_code`; `PROCEED+root_cause_confirmed→ready,omc-task,false`, `PROCEED+fix_already_applied→ready,omc-review,false`, `REVISE+architecture_scope_issue→unresolved,omc-ceo-review,false`, `HOLD+insufficient_evidence→unresolved,null,true`; 마지막 두 줄은 `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`입니다.

기계 기록의 단계: stage=investigate. 아래 별도 기록 규칙이 이전 출력 예시보다 우선한다.

## Machine output contract
별도 기록 모드 `detached_v1`을 명시적으로 사용한다. `omc-output/v1`의 stage/outcome/risk/next_skill/user_selection_needed/reason_code·VERDICT·기존 라우팅 의미는 바꾸지 않는다. `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`는 내부 판정 원문에만 두고 사용자 최종 답변에 붙이지 않는다. 한글 판정·이유·검증·다음 행동만 표시한다.
먼저 `python3 scripts/omc_output_contract.py capabilities`와 `python3 scripts/omc_workflow_validation.py capabilities`로 발행자·소비자의 지원을 각각 확인한다. 실행 전에 모드·현재 작업/세션·단계·보고 ID·마감과 명시 기록 경로를 고정한다. 문맥이 없으면 `prepare --target . --stage <단계> --report-id <새ID> --budget-sec <승인된남은초>`로 현재 상태에서 새 문맥을 만들고 반환된 context/path·SHA를 호출자 실행 메타데이터에 보존한다. 숫자 예산을 임의 연장하거나 이전 보고 ID를 재사용하지 않는다.
일반 plan/review/critique/investigate/ship 선언은 내부 원문을 작업의 .omc 안에 저장한 뒤 `python3 scripts/omc_output_contract.py publish --target . --output-context <문맥경로> --output-context-sha256 <해시> --source-file <내부원문> --source-sha256 <해시>`로 발행한다. 이는 모델 선언이며 해시 일치가 검증 통과나 사용자 승인 진위를 인증하지 않는다.
실제 task 완료는 기존 `complete-task --verification-file <내부검증>`에 `--output-mode detached_v1 --output-context <문맥경로> --output-context-sha256 <해시>`를 추가한다. 이관은 승인된 complete-transfer 경로를 유지한다. 일반 publish로 완료 가드의 발행 주체를 지정할 수 없다.
사용자 최종 답변은 발행 CLI의 한글 stdout과 같은 바이트로 제공한다. 경로/해시는 사용자 답변 꼬리표 대신 호출 인자와 실행 메타데이터로 전달한다. 수집기는 명시 manifest 경로/해시·현재 상태·native 호출/반환·실제 최종 원문을 독립 대조한다. CLI 표시 검사·수집기 fixture·실제 모델의 자유 응답 준수는 별도 증거다.
업무 차단 종료 코드1도 정상 판정으로 기록한다. 전달 실패 코드2는 업무 실패와 다르다. 저장·봉인·출력 실패 시 한글 오류만 알리고 완료/승인·자동 후속·구형 fallback을 금지한다. 실패 기록도 저장할 수 없으면 stderr·종료 원문만 보존한다. 봉인 후 표시 실패를 이유로 기존 manifest나 verdict를 수정하지 않는다.
구형 legacy_inline으로 시작한 입력만 기존 parser로 읽는다. 신형 기록 누락·변조·다른 세션·모드 혼합·표시 불일치는 보류하며 구형으로 우회하지 않는다. 구형 도구에서 신형 안내를 실행할 수 없으면 호출 전에 한글 지원 불가를 알린다. 이 안내는 플랫폼 화면 숨김이나 모델 준수를 물리적으로 보장하지 않는다.
