---
skill_name: omc-plan
description: "구현 전 계획·설계·TDD 태스크 분해. 트리거: 계획해줘, 설계해줘, 분해해줘, 어떻게 구현할지, 태스크 나눠줘. RED→GREEN→VERIFY 단계로 분해. omc-task 실행 전 반드시 사용."
---

# OMC 설계·계획

이 스킬의 목적은 구현 착수 전 범위와 DoD를 확정하고, 작은 작업은 lite로 압축하되 위험 작업은 full로 유지하는 것입니다.

## Phase 0. 상태 확인

```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-plan" --request "<현재 작업 한 줄 요약>" --roles analysis
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

사용자에게 보여줄 단계: CONTRACT / 최소 설계 / TDD 태스크 / `$omc-task` handoff | 시스템이 암묵적으로 처리: 자명한 재안내 / 선택 스킬 추천 / 반복 코칭

## Phase 2. 최소 설계

 - 입력 / 출력:
 - 성공 지표 / 실패 정책:
 - 영향받는 파일:
 - decision / risk / next_action: 진행 가능 여부 / 변경 위험도 / 다음 스킬 1개
 - 공통 결정표: stage=plan / outcome=unresolved|ready / user_selection_needed=yes|no

출력 모드:
- `plan full`: CONTRACT + 최소 설계 + 다중 TDD 태스크
- `plan lite`: CONTRACT + 최소 설계 + 태스크 2개 이하
- lite 조건: 기존 파일 중심, 검증 명령 1개로 충분, 범위를 한 문장으로 설명 가능
- full 조건: 새 파일 또는 신규 파일 생성 / API 또는 시그니처 변경 / 3개 이상 파일 / 검증 명령 축약 불가 / 범위가 불명확
- 애매하면 full, lite가 쓰였지만 설명이 약하면 `full 재계획`
- 현재 dirty 변경과 이번 계획 범위는 분리해서 다룹니다.

## Phase 3. TDD 태스크 분해

```text
plan full
태스크 1: [기능]
  RED    : [실패 테스트 파일 + 케이스]
  GREEN  : [최소 구현 파일]
  VERIFY : [검증 커맨드]

plan lite
태스크 1: [핵심 변경]
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

## 다음 추천

- 주추천 1개만 제시, 우선순위: 새 파일/API 변경/3개 이상 파일 같은 고위험이면 먼저 `$omc-critique`
- outcome=ready + user_selection_needed=no + 범위 고정 + 컨펌 완료면 `$omc-task`
- outcome=unresolved + risk=high 또는 범위 불명확이면 `$omc-critique`
- outcome=unresolved + risk=low + user_selection_needed=yes면 `$omc-office-hours`
- 사용자가 설계만 확인 중이거나 다음 단계를 아직 고르지 않음 → 사용자 선택 대기
- 자동으로 진행하지는 않습니다.

---

## ⛔ 자동 진입 금지

이 스킬이 완료된 후 자동으로 다음 스킬을 실행하지 않는다.
사용자가 명시적으로 다음 스킬을 요청할 때까지 멈추고 기다린다.
