# /critique — 냉정한 Pre-mortem 비판 리뷰

계획·코드·전략에서 **칭찬 없이 약점·가정·리스크·누락만** 찾는 Pre-mortem 리뷰입니다.
`/review`(품질 체크)나 `/office-hours`(제품 사고)와 다릅니다 — 이건 "왜 실패하는가"만 찾습니다.

---

## 컨텍스트 자동 감지

먼저 아래를 실행해 모드를 결정합니다:

```bash
git diff --stat HEAD 2>/dev/null | head -20
cat .omc/notepad.md 2>/dev/null
ls .omc/tasks/ 2>/dev/null
```

- `git diff`에 내용 있음 → **CODE 모드**
- `.omc/notepad.md` 또는 사용자가 계획 텍스트를 붙여넣음 → **PLAN 모드**
- 둘 다 있음 → 사용자에게 선택 요청

---

## PLAN 모드 — 전략·계획 Pre-mortem (강제 질문 10개)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITIQUE REPORT — PLAN MODE
대상: [플랜 제목]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[가정 검증]
Q1. 이 계획이 전제하는 가정 3가지는? 그 가정이 틀리면 계획 전체가 무너지는가?
Q2. "N주 안에 가능하다"는 어떤 근거인가? 검증 불가 낙관론이 포함되어 있는가?

[누락 탐지]
Q3. 가장 어려운 1가지는? 계획에 명시되어 있는가?
Q4. 실패 조건(이렇게 되면 망한다)이 정의되어 있는가?
Q5. 의존성(다른 팀, 외부 시스템, 데이터)이 명시되어 있는가?

[리스크 탐지]
Q6. 실행 중 기존 기능이 깨질 수 있는 부분은?
Q7. 측정 가능한 성공 지표가 각 단계에 있는가?

[범위 문제]
Q8. 빠진 것 중 나중에 "왜 안 했지?"가 될 항목은?
Q9. 범위를 절반으로 줄이면 핵심 가치의 몇 %를 달성할 수 있는가?

[Red Team]
Q10. 이 계획을 실패시키려면 어떻게 하면 되는가?
```

### PLAN 판정 포맷

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITIQUE VERDICT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL (이대로 진행하면 실패 확률 높음):
  - [근거 포함]

WARNING (보완 없이 진행하면 예측 불가 리스크):
  - [근거 포함]

MINOR (개선 권장):
  - [근거 포함]

권고 조치 (우선순위 순):
  1. ___
  2. ___

판정: HOLD / REVISE / PROCEED WITH CAUTION
이유: ___
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## CODE 모드 — 코드 변경 Pre-mortem (강제 질문 8개)

```bash
git diff HEAD
python3 scripts/omc_tdd_check.py --staged --report-only 2>/dev/null
```

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITIQUE REPORT — CODE MODE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Q1. "조용히 실패"할 수 있는 케이스는? (예외 무시, 빈 배열, null)
Q2. 테스트로 검증되지 않은 엣지케이스는?
Q3. 기존 기능을 깨뜨릴 수 있는 경로는?
Q4. 성능 최악 케이스는? (O(N^2), 메모리, 렌더링 폭탄)
Q5. 6개월 후 유지보수할 때 가장 이해하기 어려운 부분은?
Q6. 다른 모듈/팀이 뭔가 바꿔야 하는 상황이 생기는가?
Q7. 새 로직 중 테스트 없는 것은? 없어도 충분한 근거가 있는가?
Q8. "아, 이것도 해야 했는데" 싶은 항목은?
```

### CODE 판정 포맷

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITIQUE VERDICT — CODE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL (머지 전 반드시 수정):
  - [파일:라인] 근거

WARNING (강력 권고):
  - [파일:라인] 근거

MINOR (개선 권장):
  - [파일:라인] 근거

머지 전 체크리스트:
  □ ___

판정: BLOCK / REVISE / APPROVE WITH NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 행동 원칙

1. **칭찬 문장 금지** — "잘 설계됐습니다" 같은 문장은 출력하지 않는다
2. **가정 명시** — 판단 근거를 반드시 명시한다. 근거 없는 비판 금지
3. **대안 제시 의무** — 문제 지적 시 개선 방향 1개 이상 함께 제시
4. **HOLD 판정 우선** — 확신 없으면 PROCEED가 아닌 HOLD

---

## 변경 비용 체크포인트

**REVISE / HOLD 판정 후 — `/task` 진입 전 반드시 아래를 확인합니다.**

```
변경 비용 추정:
  영향 파일 수  : ___개
  예상 변경 LOC : ___줄
  실질 효과     : HIGH / MED / LOW
```

- **실질 효과 LOW + MINOR 지적만** → 건너뛰기 권장. 적용 시 득보다 비용이 클 수 있음.
- **실질 효과 MED/HIGH** → 진행. `/plan`으로 범위 확정 후 `/task`.

> ⛔ REVISE/HOLD 판정 직후 자동으로 `/task`를 실행하지 않습니다.
> 변경 비용 체크포인트를 출력하고, 사용자가 "진행"을 명시할 때까지 멈춥니다.

## 다음 추천

- 주추천 1개만 제시: HOLD/REVISE면 `/plan`, 원인 불명 버그면 `/investigate`, MINOR 수정 후 확인이면 `/review`
- 자동으로 진행하지는 않습니다.

---

대상: $ARGUMENTS (미지정 시 현재 git diff 또는 .omc/notepad.md 기준)

기계 기록의 단계: stage=critique-plan|critique-code. 아래 별도 기록 규칙이 이전 출력 예시보다 우선한다.

## Machine output contract
별도 기록 모드 `detached_v1`을 명시적으로 사용한다. `omc-output/v1`의 stage/outcome/risk/next_skill/user_selection_needed/reason_code·VERDICT·기존 라우팅 의미는 바꾸지 않는다. `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`는 내부 판정 원문에만 두고 사용자 최종 답변에 붙이지 않는다. 한글 판정·이유·검증·다음 행동만 표시한다.
먼저 `python3 scripts/omc_output_contract.py capabilities`와 `python3 scripts/omc_workflow_validation.py capabilities`로 발행자·소비자의 지원을 각각 확인한다. 실행 전에 모드·현재 작업/세션·단계·보고 ID·마감과 명시 기록 경로를 고정한다. 문맥이 없으면 `prepare --target . --stage <단계> --report-id <새ID> --budget-sec <승인된남은초>`로 현재 상태에서 새 문맥을 만들고 반환된 context/path·SHA를 호출자 실행 메타데이터에 보존한다. 숫자 예산을 임의 연장하거나 이전 보고 ID를 재사용하지 않는다.
일반 plan/review/critique/investigate/ship 선언은 내부 원문을 작업의 .omc 안에 저장한 뒤 `python3 scripts/omc_output_contract.py publish --target . --output-context <문맥경로> --output-context-sha256 <해시> --source-file <내부원문> --source-sha256 <해시>`로 발행한다. 이는 모델 선언이며 해시 일치가 검증 통과나 사용자 승인 진위를 인증하지 않는다.
실제 task 완료는 기존 `complete-task --verification-file <내부검증>`에 `--output-mode detached_v1 --output-context <문맥경로> --output-context-sha256 <해시>`를 추가한다. 이관은 승인된 complete-transfer 경로를 유지한다. 일반 publish로 완료 가드의 발행 주체를 지정할 수 없다.
사용자 최종 답변은 발행 CLI의 한글 stdout과 같은 바이트로 제공한다. 경로/해시는 사용자 답변 꼬리표 대신 호출 인자와 실행 메타데이터로 전달한다. 수집기는 명시 manifest 경로/해시·현재 상태·native 호출/반환·실제 최종 원문을 독립 대조한다. CLI 표시 검사·수집기 fixture·실제 모델의 자유 응답 준수는 별도 증거다.
업무 차단 종료 코드1도 정상 판정으로 기록한다. 전달 실패 코드2는 업무 실패와 다르다. 저장·봉인·출력 실패 시 한글 오류만 알리고 완료/승인·자동 후속·구형 fallback을 금지한다. 실패 기록도 저장할 수 없으면 stderr·종료 원문만 보존한다. 봉인 후 표시 실패를 이유로 기존 manifest나 verdict를 수정하지 않는다.
구형 legacy_inline으로 시작한 입력만 기존 parser로 읽는다. 신형 기록 누락·변조·다른 세션·모드 혼합·표시 불일치는 보류하며 구형으로 우회하지 않는다. 구형 도구에서 신형 안내를 실행할 수 없으면 호출 전에 한글 지원 불가를 알린다. 이 안내는 플랫폼 화면 숨김이나 모델 준수를 물리적으로 보장하지 않는다.
