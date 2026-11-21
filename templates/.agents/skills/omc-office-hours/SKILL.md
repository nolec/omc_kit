---
name: omc-office-hours
description: "$omc-office-hours를 직접 호출하거나, 사용자가 OMC 작업 흐름을 명시적으로 선택한 뒤 제품 대상과 성공 기준을 구체화해야 할 때 사용합니다."
---

# OMC Office Hours

명시적으로 선택한 OMC 작업의 코딩 전 제품 게이트입니다. 사용자에게 보여줄 것: lite/full, 질문, 판정, 다음 액션
시스템이 암묵적으로 처리: 6개 답변 완료 전 코딩 금지, 사용자 판단 전 PROCEED 금지. 요구사항이 모호하면 막히는 질문부터 확인하고, 범위가 흔들리면 `$omc-ceo-review`를 제안합니다.

## Phase 0. 컨텍스트 확인

```bash
python3 scripts/omc.py state status --target .
```

## lite / full 분기

- `full`: 권한, 돈, 운영, 정책, 범위 불명확, 새 사용자 플로우, 전략성
- `lite`: 방향 확정된 후속 개선, 정렬/문구/작은 UX
- 애매하면 `full`

## 6개 질문

```text
full: Q1 정확히 누구 / Q2 실제 고통 / Q3 측정 가능한 성공 기준 / Q4 MVP+사용자 시한·위험 판단 / Q5 제외 / Q6 10점 버전
lite: Q1 / Q2 / Q3 / Q4 / Q5 유지, Q6만 기본 생략
```

## 판정

- PROCEED: full은 Q1~Q6, lite는 Q1~Q5가 구체적이고 Q3이 측정 가능하며 사용자가 시한/위험을 판단
- RETHINK: Q1 사용자, Q2 고통, Q3 성공 기준 중 하나가 흐립니다.
- HOLD: Q3이 측정 불가이거나 Q5 제외 범위가 너무 넓습니다. `$omc-ceo-review`로 넘깁니다.
- lite에서 Q1, Q2, Q3, Q4, Q5 중 하나라도 흐리면 `full 재질문` 또는 `RETHINK/HOLD`

AI가 임의로 PROCEED를 결정하지 않습니다. 사용자가 판단해야 합니다.

## 이후 액션

- 주추천 1개만 제시: 현재 판정에 맞는 1개를 먼저 말합니다.
- PROCEED + 범위 구체화가 다음 병목일 때만 `$omc-plan`
- PROCEED + 사용자가 아직 구현/설계를 결정하지 않았으면 사용자 선택 대기
- RETHINK면 흐린 질문만 다시 답하거나 `full 재질문`
- HOLD면 `$omc-ceo-review`
- 출력 후 자동 진입 금지

## LLM 공통 규칙

- 모든 LLM 공통 출력 형식: lite/full, 질문, 판정, 다음 액션
- 입력 부족 시 중단
