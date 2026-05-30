---
skill_name: omc-critique
description: "계획·코드·전략에 대한 냉정한 비판 리뷰. 트리거: 이 계획 맞아, 비판해줘, 냉정하게 봐줘, 틀린 거 찾아줘, 약점이 뭐야, 플랜 리뷰해줘. 칭찬 금지, 약점·가정·리스크·누락만 찾는다."
---

# OMC Critique

Pre-mortem 전용 스킬입니다. 칭찬 금지. 약점, 가정, 누락, 리스크, 실패 조건만 찾고 근거와 대안을 함께 씁니다.

## Phase 0. 컨텍스트 감지

```bash
git diff --stat HEAD 2>/dev/null | head -20
git ls-files --others --exclude-standard 2>/dev/null | head -20
find . -newer .git/index -not -path './.git/*' \( -name '*.md' -o -name '*.py' -o -name '*.ts' \) 2>/dev/null | head -20
ls .omc/tasks/ 2>/dev/null
cat .omc/notepad.md 2>/dev/null
python3 scripts/omc.py state status --target .
```

- tracked diff, untracked, ignored/recent 파일을 모두 보고 모드를 고릅니다.
- 계획/전략/문서/스킬 평가면 PLAN 모드입니다.
- 실제 코드 변경, 신규 파일, diff가 있으면 CODE 모드입니다.
- 둘 다 있으면 사용자가 범위를 고르게 합니다.

## PLAN 모드

대상 계획을 Pre-mortem으로 깹니다.

- 가정: 핵심 가정 3개와 틀렸을 때 붕괴 여부
- 일정: 검증 불가 낙관론, 일정이 없으면 `N/A — 이유`
- 누락: 가장 어려운 1가지와 계획 내 명시 여부
- 실패 조건: 무엇이 발생하면 계획이 망하는지
- 의존성: 팀, 외부 시스템, 파일 경로, 스크립트 존재 여부
- 리스크: 기존 기능이나 워크플로가 깨지는 경로
- 성공 지표: 단계별 측정 가능한 완료 기준
- 범위: 나중에 "왜 안 했지?"가 될 항목
- 축소안: 범위를 절반으로 줄였을 때 남는 핵심 가치
- Red Team: 이 계획을 실패시키는 가장 쉬운 방법

## CODE 모드

변경사항을 머지 전 Pre-mortem으로 깹니다.

- 조용한 실패: 예외 무시, 빈 배열, null, fallback 오작동
- 테스트 누락: 엣지케이스와 신규 로직의 검증 공백
- 회귀 경로: 기존 기능/API/CLI/스킬 흐름이 깨지는 지점
- 최악 성능: O(N^2), 메모리, 렌더링, I/O 폭탄
- 유지보수: 6개월 뒤 이해하기 어려운 부분
- 파급효과: 다른 모듈, 팀, 템플릿, LLM별 사본 영향
- TDD 상태: CONTRACT/RED/GREEN/GATE 누락 여부
- 빠진 일: 이번 PR에서 함께 처리했어야 할 항목

## 출력 규칙

- 모든 지적은 `근거:`와 `대안:`을 포함합니다.
- 근거 없는 비판, 대안 없는 비판, 범위 밖 비판은 금지합니다.
- 확신이 없으면 낮은 판정이 아니라 HOLD/BLOCK 쪽으로 둡니다.
- `omc-review`처럼 품질 승인하지 말고, 실패 가능성을 먼저 찾습니다.

## Verdict

PLAN:
- CRITICAL 있음 → HOLD
- WARNING 있음 → REVISE
- MINOR만 있음 → PROCEED WITH CAUTION
- 이슈 없음 → PROCEED

CODE:
- CRITICAL 있음 → BLOCK
- WARNING 있음 → REVISE
- MINOR만 있음 → APPROVE WITH NOTES
- 이슈 없음 → APPROVE

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

권고 조치:
1. ...
2. ...
3. ...

VERDICT: HOLD / REVISE / PROCEED / BLOCK / APPROVE
```

## 이후 액션

- HOLD/REVISE면 `$omc-plan`으로 재설계합니다.
- 코드 품질 확인은 `$omc-review`로 넘깁니다.
