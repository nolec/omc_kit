---
skill_name: omc-task
description: "7단계 TDD 파이프라인으로 구현 실행. 트리거: 구현해줘, 만들어줘, 코딩해줘, 개발해줘, 이거 짜줘. CONTRACT→RED→GREEN→REFACTOR→GATE→REVIEW 순서. 단계 건너뜀 금지. omc-plan 이후 사용."
---

# OMC TDD 파이프라인

구현은 아래 순서로만 진행합니다. 계획이 없으면 `$omc-plan`, 원인 불명 버그면 `$omc-investigate`로 돌립니다.

## 0. Guard

```bash
python3 scripts/omc_guard.py sync-require --target . --mode autopilot --title "omc-task" --request "<현재 작업 한 줄 요약>" --roles senior_coding --for "task"
```

실패하면 세션 확인 또는 confirm부터 처리하고 중단합니다.

## 필수 체크

- CONTRACT 등록: `python3 scripts/omc_pipeline_guard.py contract-done`
- RED 등록 + TDD 게이트: FAIL 출력 첨부 + `red-done` 완료 / `python3 scripts/omc_tdd_check.py --staged` exit 0

사용자에게 보여줄 단계: CONTRACT / RED / TDD GATE / Handoff | 시스템이 암묵적으로 처리: 자명한 재안내 / 중복 설명 / 단계 사이 반복 코칭

안전 필수 항목: CONTRACT / RED / TDD GATE / Handoff는 압축해도 유지 | 작은 후속 수정도 Guard, CONTRACT, RED 등록 순서는 유지 | 범위 분리: 현재 dirty 변경과 이번 구현 범위를 섞지 않음

## PHASE 1 ▸ CONTRACT — 목표 / 범위 / DoD / 제약 / 사용자 컨펌:
- 컨펌 전 구현 파일을 수정하지 않습니다.

## PHASE 2 ▸ DESIGN — 입력/출력 계약 / 실패·에러 정책 / 영향받는 파일:
- decision / risk / next_action: 구현 상태 / 변경 위험도 / 다음 스킬 1개
- 공통 결정표: stage=task / outcome=unresolved|done / user_selection_needed=yes|no
- 계약이 약한 작업도 빈칸 없이 적고, 작은 후속 수정도 영향 파일과 실패 정책은 생략하지 않습니다.

## PHASE 3 ▸ RED — 테스트 파일 / 테스트 케이스 / 실제 FAIL 출력:

```bash
python3 scripts/omc_pipeline_guard.py red-done <테스트파일>
```

- FAIL 출력과 `red-done` 등록 없이 구현 파일을 만들지 않습니다.

## PHASE 4 ▸ GREEN — 구현 파일 / 핵심 변경 / 테스트 PASS / 기존 회귀 없음:
- 최소 구현만 먼저 합니다.

## PHASE 5 ▸ REFACTOR — 정리 항목 / 리팩터링 후 PASS:
- 이름, 중복, 책임만 정리합니다.

## PHASE 6 ▸ TDD GATE

```bash
python3 scripts/omc_tdd_check.py --staged
```

- 실패하면 수정 후 다시 확인합니다.

## PHASE 7 ▸ COMPOUND ENGINEERING — 교훈 없음 / 교훈 기록 완료:

```bash
python3 scripts/omc_lesson.py add -i
```

- 막히면 `교훈 없음`을 명시합니다.

## Handoff
- 모든 단계가 끝나면 `$omc-review`로 넘깁니다.

## 다음 추천

- 우선순위는 항상 `현재 병목 > 기본 파이프라인`
- fast 기준: 구현 범위와 DoD가 이미 고정됐으면 바로 `$omc-review`로 넘긴다.
- normal 기준: 새 파일/API 변경, 다중 파일 연쇄 변경, 검증 경로가 길면 결과 확인 전 사용자 선택이나 재설계를 우선한다.
- 주추천 1개만 제시, 우선순위: 구현 완료 + 게이트 통과면 `$omc-review`
- 구현 완료 + 사용자가 일단 결과만 확인하려는 상태면 사용자 선택 대기
- 실패 원인 불명 → `$omc-investigate`
- 자동으로 진행하지는 않습니다.

---

## ⛔ 자동 진입 금지

이 스킬이 완료된 후 자동으로 다음 스킬을 실행하지 않고, 사용자가 명시적으로 다음 스킬을 요청할 때까지 멈추고 기다린다.
