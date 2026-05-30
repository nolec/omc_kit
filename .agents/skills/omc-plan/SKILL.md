---
skill_name: omc-plan
description: "구현 전 계획·설계·TDD 태스크 분해. 트리거: 계획해줘, 설계해줘, 분해해줘, 어떻게 구현할지, 태스크 나눠줘. RED→GREEN→VERIFY 단계로 분해. omc-task 실행 전 반드시 사용."
---

# OMC 설계·계획

구현 전에 요구사항, 최소 설계, TDD 태스크를 확정합니다. 모호하면 `$omc-brainstorm`, 방향 검증은 `$omc-office-hours`, 범위 재검토는 `$omc-ceo-review`로 돌립니다.

## Phase 0. 상태 확인

```bash
python3 scripts/omc.py state status --target .
```

상태와 최근 흐름을 보고 Phase 1로 갑니다.

AGENTS.md Tier 1 작업은 이 Phase 1 요구사항을 CONTRACT 입력으로 삼습니다.

## Phase 1. 요구사항 CONTRACT

- 목표:
- 범위 (포함):
- 범위 (제외):
- DoD:
- 제약:
- 사용자 컨펌:

모든 항목을 채웁니다. 모르면 `N/A — 이유`로 쓰고, 제외 범위는 반드시 둡니다.

## Phase 2. 최소 설계

- 입력:
- 출력:
- 성공 지표:
- 실패 정책:
- 영향받는 파일:

계약이 없는 문서/스타일 작업도 `해당 없음 — 이유`로 채웁니다.

## Phase 3. TDD 태스크 분해

각 태스크는 반드시 RED/GREEN/VERIFY를 가집니다.

```text
태스크 1: [기능]
  RED    : [실패 테스트 파일 + 케이스]
  GREEN  : [최소 구현 파일]
  VERIFY : [검증 커맨드]
```

테스트로 표현할 수 없을 만큼 큰 태스크는 쪼갭니다.

## Phase 4. 세션 기록

사용자 컨펌 완료 전에는 아래 명령을 실행하지 않습니다.

```bash
python3 scripts/omc.py state confirm --target .
```

confirm 후에만 `$omc-task`로 넘깁니다.

## 출력 조건

- 요구사항 5항목과 사용자 컨펌이 있어야 합니다.
- 설계에는 입력, 출력, 실패 정책, 영향받는 파일이 있어야 합니다.
- 각 태스크에는 RED, GREEN, VERIFY가 있어야 합니다.
- Phase 1~3이 비어 있으면 구현으로 넘어가지 않습니다.
