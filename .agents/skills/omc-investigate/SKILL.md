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
- 입력 부족 시 중단: 재현 조건 또는 검증 결과가 비면 수정 단계로 넘기지 않습니다.
