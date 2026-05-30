---
skill_name: omc-task
description: "7단계 TDD 파이프라인으로 구현 실행. 트리거: 구현해줘, 만들어줘, 코딩해줘, 개발해줘, 이거 짜줘. CONTRACT→RED→GREEN→REFACTOR→GATE→REVIEW 순서. 단계 건너뜀 금지. omc-plan 이후 사용."
---

# OMC TDD 파이프라인

구현은 아래 순서로만 진행합니다. 계획이 없으면 `$omc-plan`, 원인 불명 버그면 `$omc-investigate`로 돌립니다.

## 0. Guard

```bash
python3 scripts/omc_guard.py require --for "task" --target .
```

실패하면 세션 확인 또는 confirm부터 처리하고 중단합니다.

## PHASE 1 ▸ CONTRACT

- 목표:
- 범위:
- DoD:
- 제약:
- 사용자 컨펌:

컨펌 전 구현 파일을 수정하지 않습니다.

## PHASE 2 ▸ DESIGN

- 입력/출력 계약:
- 실패·에러 정책:
- 영향받는 파일:

UI·문서처럼 계약이 없으면 `해당 없음 — 이유`로 채웁니다.

## PHASE 3 ▸ RED

- 테스트 파일:
- 테스트 케이스:
- 실제 FAIL 출력:

```bash
python3 scripts/omc_pipeline_guard.py red-done <테스트파일>
```

FAIL 출력과 `red-done` 등록 없이 구현 파일을 만들지 않습니다.

## PHASE 4 ▸ GREEN

- 구현 파일:
- 핵심 변경:
- 테스트 PASS:
- 기존 회귀 없음:

테스트를 통과하는 최소 구현만 먼저 합니다.

## PHASE 5 ▸ REFACTOR

- 정리 항목:
- 리팩터링 후 PASS:

GREEN 상태를 유지한 채 이름, 중복, 책임만 정리합니다.

## PHASE 6 ▸ TDD GATE

```bash
python3 scripts/omc_tdd_check.py --staged
```

반환값 0이 아니면 수정 후 다시 확인합니다.

## PHASE 7 ▸ COMPOUND ENGINEERING

- 교훈 없음 / 교훈 기록 완료:

```bash
python3 scripts/omc_lesson.py add -i
```

인터랙티브가 막히면 비인터랙티브 추가 또는 `교훈 없음`을 명시합니다.

## Handoff

모든 단계가 끝나면 `$omc-review`로 넘깁니다. 치명/중대 이슈가 없을 때만 ship으로 갑니다.
