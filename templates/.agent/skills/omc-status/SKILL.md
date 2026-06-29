---
skill_name: omc-status
description: "현재 세션 상태·작업 컨텍스트 출력. 트리거: 지금 상태, 뭐하고 있었어, 현재 작업, 어디까지 했어, 상태 보여줘. confirmed 태스크·pending 항목·다음 액션 요약."
---

# OMC Status

조회 전용 상태판입니다. 현재 상태를 바꾸지 않고 다음 1액션만 정리하는 것이 목적입니다.

## Phase 0. 수집

```bash
python3 scripts/omc.py state status --target .
cat .omc/notepad.md
git status -sb
git diff --stat HEAD
git ls-files --others --exclude-standard
git log --oneline -5
```

## Phase 1. 세션 판정

사용자에게 보여줄 것: 세션 요약 / git 요약 / 변경 카테고리 / 차단·주의 / 다음 액션 / 남은 스킬 후보
시스템이 암묵적으로 처리: 현재 사용자 요청 vs latest request / confirmed_request / pending_request 비교, read-only 유지
조회 전용 안전 항목: read-only / 커밋 대상 아님 / 다음 액션 1개 추천 / 세션 불일치 / stale / 소스/스킬 변경 / .omc 실행 아티팩트 / untracked 판단 + 현재 커밋 범위 vs 범위 밖 dirty 변경 분리 + ship 차단 힌트 / 실패한 명령은 `N/A — 이유`

## Phase 2. 변경 카테고리

- 소스/스킬 변경: 현재 커밋 범위와 범위 밖 dirty 변경으로 분리해 요약
- .omc 실행 아티팩트: `.omc/pipeline_run_result.json`, `.omc/runs`, `.omc/lessons`, `.omc/allow_log.jsonl` / untracked: 새 파일 후보와 커밋 대상 아님 후보 분리

## Phase 3. 출력

```text
OMC 세션: latest/confirmed/pending, stale
Git 상태: branch/ahead/behind, staged/unstaged/untracked
변경 분류: 현재 커밋 범위, 범위 밖 dirty 변경, .omc 실행 아티팩트, untracked
차단/주의: 미확정 세션, 범위 밖 dirty 변경, 커밋 대상 아님, 테스트/리뷰/ship 차단, ship 차단 힌트
이벤트가 있을 때만 reroute 이유 / delay 이유 / 재개 조건: ...
다음 액션: $omc-plan / $omc-task / $omc-review / $omc-ship / $omc-retro
남은 스킬 후보:
```

## 판단 기준

- 우선순위는 항상 `현재 병목 > 기본 파이프라인`
- 요청 stale 또는 문맥 엇갈림 → `$omc-plan`
- 사용자가 상태만 확인 중이거나 다음 단계를 아직 고르지 않음 → 사용자 선택 대기
- 현재 커밋 범위 변경이 있고 품질 확인이 병목이면 → `$omc-review`
- 계획 확정 후 구현 대기이고 추가 선택이 없으면 → `$omc-task`
- 리뷰 통과 + 배포 의도 명시 + ship 차단 없음 → `$omc-ship`
- 클린하고 남은 작업 없음 → `$omc-retro` 또는 남은 스킬 후보 제안

## 다음 추천

- 주추천 1개만 제시: 판단 기준에 따라 1개만 선택합니다 (`$omc-plan` / `$omc-task` / `$omc-review` / `$omc-ship` / `$omc-retro` / 사용자 선택 대기)
- 자동으로 진행하지는 않습니다.
