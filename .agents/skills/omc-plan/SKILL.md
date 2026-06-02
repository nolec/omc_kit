---
skill_name: omc-plan
description: "구현 전 계획·설계·TDD 태스크 분해. 트리거: 계획해줘, 설계해줘, 분해해줘, 어떻게 구현할지, 태스크 나눠줘. RED→GREEN→VERIFY 단계로 분해. omc-task 실행 전 반드시 사용."
---

# OMC 설계·계획

이 스킬의 목적은 구현 착수 전 범위와 DoD를 확정하는 것입니다.

## Phase 0. 상태 확인

```bash
python3 scripts/omc.py state status --target .
```

AGENTS.md Tier 1 작업은 이 Phase 1 요구사항을 CONTRACT 입력으로 삼습니다.

## Phase 1. 요구사항 CONTRACT

- 목표:
- 범위 (포함):
- 범위 (제외):
- DoD:
- 제약:
- 사용자 컨펌:

사용자에게 보여줄 단계: CONTRACT / 최소 설계 / TDD 태스크 / `$omc-task` handoff
시스템이 암묵적으로 처리: 자명한 재안내 / 선택 스킬 추천 / 반복 코칭

## Phase 2. 최소 설계

- 입력:
- 출력:
- 성공 지표:
- 실패 정책:
- 영향받는 파일:

## Phase 3. TDD 태스크 분해

```text
태스크 1: [기능]
  RED    : [실패 테스트 파일 + 케이스]
  GREEN  : [최소 구현 파일]
  VERIFY : [검증 커맨드]
```

## Phase 4. 세션 기록

사용자 컨펌 완료 전에는 아래 명령을 실행하지 않습니다.

```bash
python3 scripts/omc.py state confirm --target .
```

confirm 후에만 `$omc-task`로 넘깁니다.
