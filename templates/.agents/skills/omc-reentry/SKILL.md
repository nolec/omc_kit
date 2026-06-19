---
skill_name: omc-reentry
description: "오랜만에 돌아온 프로젝트를 빠르게 파악하는 복귀용 맵. 트리거: 이 프로젝트 뭐였지, 오랜만에 왔어, 구조 파악, 빠르게 컨텍스트 복원, 어디부터 봐야 해."
---

# OMC Reentry

프로젝트 복귀 맥락 복원 전용입니다. `omc-status`가 현재 세션 상태를 보는 도구라면, 이 스킬은 프로젝트 자체를 다시 이해하는 용도입니다. 구현 태스크 분해를 하지 않습니다.

## Phase 0. 수집

```bash
# 문서 우선
README / ETHOS.md / AGENTS.md
git status -sb
git log --oneline -5
python3 scripts/omc.py state status --target .
```

사용자에게 보여줄 것: 프로젝트 한 줄 요약 / 핵심 구조 / 실행/검증 진입점 / 주의할 SSOT/금지 경로 / 최근 작업 흔적 / 다음 읽을 파일 3개 / 추천 다음 스킬
시스템이 암묵적으로 처리: 문서 기준과 현재 상태 기준을 분리, 문서 우선 후 git/OMC 상태 보강, OMC 미설치 fallback

## Phase 1. 복귀 요약 규칙

- 문서 기준: 프로젝트 목적·원칙은 README/ETHOS/AGENTS 기준
- 현재 상태 기준: 최근 작업 흔적·세션 신호는 git/OMC 기준
- 핵심 구조: 디렉토리 4~6개만
- 다음 읽을 파일 3개: 목적 문서 1개 + 진입점 1개 + 최근 변경/핵심 로직 1개, 정확히 3개
- 추천 다음 스킬: 정확히 1개
- tree dump 금지 / README 재요약 금지

## Phase 2. 출력

```text
프로젝트 한 줄 요약:
핵심 구조:
실행/검증 진입점:
주의할 SSOT/금지 경로:
최근 작업 흔적:
다음 읽을 파일 3개:
추천 다음 스킬:
```

## 경계 규칙

- `omc-status`: 현재 세션 상태, stale, commit 범위 확인
- `omc-reentry`: 프로젝트 복귀 맥락 복원
- 구현 설계가 필요하면 `$omc-plan`
- 세션 정합성이 먼저 의심되면 `$omc-status`

## 다음 추천

- 주추천 1개만 제시: 복귀 후 구현 설계 필요 → `$omc-plan`, 세션/상태 확인 우선 → `$omc-status`, 아직 결정 전이면 사용자 선택 대기
- 자동으로 진행하지는 않습니다.
