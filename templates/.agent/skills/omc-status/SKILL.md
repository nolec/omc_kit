---
skill_name: omc-status
description: "현재 OMC 세션 상태·작업 컨텍스트 출력. 트리거: 지금 상태, 뭐하고 있었어, 현재 작업, 어디까지 했어, 상태 보여줘. confirmed 태스크·pending 항목·다음 액션 요약."
---

# OMC Status

조회 전용 상태판입니다. 세션, git, 변경 분류를 보고 다음 1액션만 제안합니다.

## Phase 0. 수집

```bash
python3 scripts/omc.py state status --target .
cat .omc/notepad.md
git status -sb
git diff --stat HEAD
git ls-files --others --exclude-standard
git log --oneline -5
```

실패한 명령은 `N/A — 이유`로 표시합니다. 이 스킬은 세션이나 git 상태를 바꾸지 않습니다.

## Phase 1. 세션 판정

- 현재 사용자 요청과 `latest request`, `confirmed_request`, `pending_request`를 비교합니다.
- 다르면 `세션 불일치` 또는 stale 컨텍스트로 표시하고 `$omc-plan` 재진입을 제안합니다.
- pending이 있으면 완료/재개 대상을 분리하고, 없으면 `없음`이라고 씁니다.

## Phase 2. 변경 분류

- 소스/스킬 변경: `.agents`, `templates`, `scripts`, `src`, `lib` 등 작업 대상 파일
- .omc 실행 아티팩트: `.omc/pipeline_run_result.json`, `.omc/runs`, `.omc/lessons`, `.omc/allow_log.jsonl`
- untracked: 새 파일 후보와 커밋 대상 아님 후보를 분리

`.omc 실행 아티팩트`만 dirty면 커밋 대상 아님으로 표시합니다.

## Phase 3. 출력

```text
OMC 세션:
- latest/confirmed/pending:
- 세션 불일치 또는 stale:

Git 상태:
- branch/ahead/behind:
- staged/unstaged/untracked:

변경 분류:
- 소스/스킬 변경:
- .omc 실행 아티팩트:
- untracked:

차단/주의:
- 미확정 세션:
- 커밋 대상 아님:
- 테스트/리뷰/ship 차단:

다음 액션:
- $omc-plan / $omc-task / $omc-review / $omc-ship / $omc-retro
- 남은 스킬 후보:
```

## 판단 기준

- 세션 불일치 또는 stale → `$omc-plan`
- 계획 확정 후 구현 대기 → `$omc-task`
- 변경 완료 후 검토 대기 → `$omc-review`
- 리뷰 통과 후 출시 준비 → `$omc-ship`
- 클린하고 남은 작업 없음 → `$omc-retro` 또는 남은 스킬 후보 제안
