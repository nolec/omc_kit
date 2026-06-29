---
skill_name: omc-critique
description: "계획·코드·전략에 대한 냉정한 비판 리뷰. 트리거: 이 계획 맞아, 비판해줘, 냉정하게 봐줘, 틀린 거 찾아줘, 약점이 뭐야, 플랜 리뷰해줘. 칭찬 금지, 약점·가정·리스크·누락만 찾는다."
---

# OMC Critique

Pre-mortem 전용 스킬입니다. 승인보다 실패 가능성 탐지가 목적이며, 칭찬 금지, 근거와 대안만 남깁니다.

## Phase 0. 컨텍스트 감지

```bash
git diff --stat HEAD 2>/dev/null | head -20
git ls-files --others --exclude-standard 2>/dev/null | head -20
find . -newer .git/index -not -path './.git/*' \( -name '*.md' -o -name '*.py' -o -name '*.ts' \) 2>/dev/null | head -20
ls .omc/tasks/ 2>/dev/null
cat .omc/notepad.md 2>/dev/null
python3 scripts/omc.py state status --target .
```

- `.omc/runs`, `.omc/lessons`, `.omc/pipeline_run_result.json`은 실행 산출물로 기본 제외합니다.
- 계획/전략/문서/스킬 평가면 PLAN 모드입니다.
- 실제 코드 변경, 신규 파일, diff가 있으면 CODE 모드입니다.
- 둘 다 있으면 사용자가 범위를 고르게 합니다.
사용자에게 보여줄 것: CRITICAL / WARNING / MINOR, 근거 / 대안, 권고 조치 / VERDICT
시스템이 암묵적으로 처리: 컨텍스트 감지, 범위 밖 제외, tracked/untracked 최근 파일 스캔

## PLAN 모드

- 가정 / 일정 / 누락 / 실패 조건 / 의존성 / 리스크 / 성공 지표
- 범위 / 축소안 / Red Team

## CODE 모드

- 조용한 실패 / 테스트 누락 / 회귀 경로 / 최악 성능 / 유지보수 / 파급효과
- TDD 상태 / 빠진 일

## 출력 규칙

- 모든 지적은 `근거:`와 `대안:`을 포함합니다.
- 근거 없는 비판, 대안 없는 비판, 범위 밖 비판은 금지합니다.
- 확신이 없으면 낮은 판정이 아니라 HOLD/BLOCK 쪽으로 둡니다.
- `omc-review`처럼 품질 승인하지 말고, 실패 가능성을 먼저 찾습니다.
- 이벤트가 있을 때만 `reroute 이유 / delay 이유 / 재개 조건`을 적고, HOLD/REVISE면 생략하지 않습니다.

## Verdict

- PLAN: CRITICAL 있음 → HOLD / WARNING 있음 → REVISE / MINOR만 있음 → PROCEED WITH CAUTION / 이슈 없음 → PROCEED
- CODE: CRITICAL 있음 → BLOCK / WARNING 있음 → REVISE / MINOR만 있음 → APPROVE WITH NOTES / 이슈 없음 → APPROVE

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

VERDICT: HOLD / REVISE / PROCEED / BLOCK / APPROVE
```

## 비용 체크포인트

REVISE / HOLD 판정 후 `$omc-task` 진입 전 반드시 아래를 확인합니다.

```text
변경 비용 추정:
  영향 파일 수  : ___개
  예상 변경 LOC : ___줄
  실질 효과     : HIGH / MED / LOW
```

- 실질 효과 LOW + MINOR 지적만 → 건너뛰기 권장 / MED/HIGH → `$omc-plan`으로 범위 확정 후 `$omc-task`
- 같은 REVISE/HOLD 사유가 반복될 때만 이 체크포인트를 다시 엽니다.
- 반복 근거가 없으면 여기서 중단하고 현 상태를 유지합니다.

> ⛔ REVISE/HOLD 판정 직후 `$omc-task`를 바로 실행하지 않습니다.
> 이 체크포인트를 출력하고, 사용자가 "진행"을 명시할 때까지 멈춥니다.

## 이후 액션

- HOLD/REVISE이고 변경 비용 MED/HIGH → `$omc-plan`으로 재설계합니다.
- HOLD/REVISE이고 변경 비용 LOW + MINOR만 → 사용자가 건너뛰기 여부를 결정합니다.
- 코드 품질 확인은 `$omc-review`로 넘깁니다.

## 다음 추천

- 주추천 1개, 우선순위: HOLD/REVISE면 `$omc-plan`
- PROCEED + PLAN 모드 + 범위 고정 → 사용자 선택 대기 (`$omc-task`)
- PROCEED + CODE 모드 → 사용자 선택 대기 (`$omc-review`)
- 자동으로 진행하지는 않습니다.

## ⛔ 자동 진입 금지

이 스킬이 완료되면 사용자가 명시적으로 다음 스킬을 요청할 때까지 멈추고 기다린다.
