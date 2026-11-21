---
name: omc-status
description: "현재 세션 상태·작업 컨텍스트 출력. 트리거: 지금 상태, 뭐하고 있었어, 현재 작업, 어디까지 했어, 상태 보여줘. confirmed 태스크·pending 항목·다음 액션 요약."
---

# OMC Status

조회 전용 상태판입니다. 현재 상태를 바꾸지 않고 다음 1액션만 정리하는 것이 목적입니다.

## Phase 0. 수집

1. 같은 저장소에서 작업 트리와 세션 상태가 바뀌기 전에 얻은 `state status` 결과를 재사용합니다. 현재 결과가 없으면 OMC 설치본에서 `python3 scripts/omc.py state status --target .`를 실행합니다.
2. 종료 코드가 0이어도 필요한 세션·Git 항목이 실제로 출력됐는지 확인합니다. staged와 unstaged 양쪽에 같은 파일이 있으면 두 범주에 모두 표시합니다.
3. OMC 미설치·조회 실패·항목 누락 또는 브랜치·ahead/behind·전체 경로가 필요할 때만 기존 Git 결과를 재사용하거나 `GIT_OPTIONAL_LOCKS=0 git -c core.fsmonitor=false status --short --branch --untracked-files=all`로 빠진 Git 정보만 보완합니다. Git으로 확인한 결과와 확인 불가인 OMC 세션은 따로 표시합니다.
4. 판단에 필요한 내용만 선택해 보완합니다. 변경량은 `GIT_OPTIONAL_LOCKS=0 git -c core.fsmonitor=false diff --stat HEAD`, 최근 커밋은 `GIT_OPTIONAL_LOCKS=0 git -c core.fsmonitor=false log --oneline -5`, 메모 본문은 `.omc/notepad.md`에서 확인합니다.

## Phase 1. 세션 판정

사용자에게 보여줄 것: 세션 요약 / git 요약 / 변경 카테고리 / 차단·주의 / 다음 액션 / 남은 스킬 후보
시스템이 암묵적으로 처리: 현재 사용자 요청 vs latest request / confirmed_request / pending_request 비교, read-only 유지
조회 전용 안전 항목: read-only / 커밋 대상 아님 / 다음 액션 1개 추천 / 세션 불일치 / stale / 소스/스킬 변경 / .omc 실행 아티팩트 / staged / unstaged / untracked 사실과 사용자 요청 범위 별도 표시; 요청 범위 근거 없으면 미확인, Git 조회 실패는 확인 불가 / 실패한 명령은 `N/A — 이유`

## Phase 2. 변경 카테고리

- 소스/스킬 변경: Git 단계별 상태와 사용자 요청 범위를 별도 요약; staged를 승인 범위로 추정하지 않음 / `.omc 실행 아티팩트`: `.omc/pipeline_run_result.json`, `.omc/runs`, `.omc/lessons`, `.omc/allow_log.jsonl` / `untracked`: 새 파일 후보와 커밋 대상 아님 후보 분리

## Phase 3. 출력

```text
OMC 세션: latest/confirmed/pending, stale
Git 상태: branch/ahead/behind, staged/unstaged/untracked
변경 분류: Git staged/unstaged/untracked, .omc 실행 아티팩트, 요청 범위 (근거 없으면 미확인)
차단/주의: 미확정 세션, 기존 변경 보존, 커밋 대상 아님, 테스트/리뷰/ship 차단 (근거가 있을 때만)
이벤트가 있을 때만 reroute 이유 / delay 이유 / 재개 조건: ...
다음 액션: $omc-plan / $omc-task / $omc-review / $omc-ship / $omc-retro
남은 스킬 후보:
```

## 판단 기준

- 우선순위는 항상 `현재 병목 > 기본 파이프라인`
- 요청 stale 또는 문맥 엇갈림 → `$omc-plan`
- 사용자가 상태만 확인 중이거나 다음 단계를 아직 고르지 않음 → 사용자 선택 대기
- 요청 범위가 확인되고 품질 확인이 병목이면 → `$omc-review`
- 계획 확정 후 구현 대기이고 추가 선택이 없으면 → `$omc-task`
- 리뷰 통과 + 배포 의도 명시 + ship 차단 없음 → `$omc-ship`
- 클린하고 남은 작업 없음 → `$omc-retro` 또는 남은 스킬 후보 제안

## 다음 추천

- 주추천 1개만 제시: 현재 사용자 흐름과 판단 기준에 따라 1개만 선택 (`$omc-plan` / `$omc-task` / `$omc-review` / `$omc-ship` / `$omc-retro` / 사용자 선택 대기), 자동으로 진행하지는 않습니다.
