---
skill_name: omc-office-hours
description: "코딩 전 제품 사고 강제. 6개 질문 필수 답변. 트리거: 기능 추가하려고, 만들어볼게, 제품 사고, 사용자 입장에서, 뭘 만들어야 해. 6개 답변 완료 전 코드 작성 금지."
---

# OMC Office Hours

코딩 전 제품 게이트입니다. 사용자에게 보여줄 것: lite/full, 질문, 판정, 다음 액션
시스템이 암묵적으로 처리: 6개 답변 완료 전 코딩 금지, 사용자 판단 전 PROCEED 금지. 모호하면 `$omc-brainstorm`, 범위 흔들리면 `$omc-ceo-review`.

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

- 주추천 1개만 제시: 현재 판정과 사용자 의도에 맞는 1개만 말합니다.
- PROCEED + 범위 구체화가 다음 병목일 때만 `$omc-plan`
- PROCEED + 사용자가 아직 구현/설계를 결정하지 않았으면 사용자 선택 대기
- RETHINK면 흐린 질문만 다시 답하거나 `full 재질문`
- HOLD면 `$omc-ceo-review`
- 출력 후 자동 진입 금지

## LLM 공통 규칙

- 모든 LLM 공통 출력 형식: lite/full, 질문, 판정, 다음 액션
- 입력 부족 시 중단
