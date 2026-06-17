# /plan — OMC 설계·계획

구현 전 범위와 DoD를 확정하고, 작은 작업은 lite로 압축하되 위험 작업은 full로 유지합니다.

## Phase 0. 상태 확인

```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-plan" --request "<현재 작업 한 줄 요약>" --roles analysis
python3 scripts/omc.py state status --target .
```

AGENTS.md Tier 1 작업은 아래 Phase 1 요구사항을 CONTRACT 입력으로 사용합니다.

## Phase 1. 요구사항 CONTRACT

- 목표:
- 범위 (포함):
- 범위 (제외):
- DoD:
- 제약:
- 사용자 컨펌:

사용자에게 보여줄 단계: CONTRACT / 최소 설계 / TDD 태스크 / `/task` handoff
시스템이 암묵적으로 처리: 자명한 재안내 / 선택 스킬 추천 / 반복 코칭

## Phase 2. 최소 설계

- 입력:
- 출력:
- 성공 지표:
- 실패 정책:
- 영향받는 파일:

출력 모드:
- `plan full`: CONTRACT + 최소 설계 + 다중 TDD 태스크
- `plan lite`: CONTRACT + 최소 설계 + 태스크 2개 이하
- lite 조건: 기존 파일 중심, 검증 명령 1개로 충분, 범위를 한 문장으로 설명 가능
- full 조건: 새 파일 또는 신규 파일 생성 / API 또는 시그니처 변경 / 3개 이상 파일 / 검증 명령 축약 불가 / 범위가 불명확
- 애매하면 full
- lite가 쓰였지만 설명이 약하면 `full 재계획`
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

confirm 후에만 `/task`로 넘깁니다.

## 다음 추천

- 주추천 1개만 제시: 범위 고정 + 컨펌 완료면 `/task`
- 범위 불명확 또는 흔들림 → `/critique` / `/office-hours`
- 자동으로 진행하지는 않습니다.

---

계획할 작업: $ARGUMENTS
