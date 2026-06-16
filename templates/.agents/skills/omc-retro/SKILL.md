---
skill_name: omc-retro
description: "세션 히스토리 분석 후 주간 회고 실행. 트리거: 회고, 이번 주 리뷰, 돌아보기, 주간 정리, 회고해줘. 완료·실패 세션 분석 후 교훈 캡처."
---

# OMC Retro

읽기 전용 회고 스킬입니다. 사용자에게 보여줄 것: 날짜 범위 / 출처·충돌 / 완료·미완료 / 반복 패턴 / 다음 우선순위 / 교훈 필요 여부
시스템이 암묵적으로 처리: 기간 해석, 증거 수집, 세션 stale 판정, 쓰기 금지 유지

## Phase 0. 기간

- 기간 미지정 → 최근 7일 / "이번 주" → Asia/Seoul 기준 현재 주간 / 출력에는 실제 날짜 범위를 씁니다.

## Phase 1. 수집

```bash
python3 scripts/omc.py state status --target .
cat .omc/notepad.md
python3 scripts/omc_lesson.py list
git log --oneline --since="7 days ago"
```

실패한 명령은 `N/A — 이유`, 데이터가 없으면 `없음`으로 씁니다.

## Phase 2. 출처/충돌

- git log 기준: 완료된 작업 후보 / omc state 기준: 세션 상태와 미완료 작업 / notepad 기준: handoff, current_request, 이월 후보
- lesson list 기준: 반복 패턴 후보
- git log 최신 작업과 state/notepad current_request가 다르면 세션 불일치 또는 stale로 표시하고 `$omc-status`를 제안

## Phase 3. RETRO 출력

```text
RETRO — [날짜 범위]

출처/충돌:
- git log 기준:
- omc state 기준:
- notepad 기준:
- lesson list 기준:
- stale:

완료된 작업:

반복되는 문제 패턴:

완료되지 못한 작업:

다음 우선순위 최대 3개:

교훈 필요 여부: 교훈 없음 / $omc-lesson 필요 / 기존 교훈 업데이트 후보
```

- 교훈 필요: 같은 실패/차단/재시도 2회 이상, 같은 스킬에서 반복 수정, 테스트/가드 실패 반복, 사용자가 같은 질문을 반복
- stale이면 `$omc-status`, 반복 패턴이 있으면 `$omc-lesson`, 이월 작업이 있으면 `$omc-plan` 또는 `$omc-task`
- notepad 업데이트 필요만 표시하고, 사용자 명시 승인 전 `.omc/notepad.md`와 `.omc/lessons/`는 직접 수정하지 않음
- 최종 우선순위는 미완료/차단 먼저, 반복 패턴 개선, 가치 큰 다음 스킬, 리스크 큰 자동화는 뒤 순서로 씁니다
- 데이터가 없으면 없음으로 명시합니다
- 다음 추천 — 주추천 1개: stale → `$omc-status`, 반복 패턴 → `$omc-lesson`, 이월 작업 → `$omc-plan` 또는 `$omc-task`
- 위 조건이 없고 사용자가 회고만 확인 중이면 사용자 선택 대기
