---
skill_name: pr-create
description: "GitHub PR 생성 — 프로젝트 표준 템플릿·라벨·Assignee 자동 적용. 트리거: PR 올려줘, PR 생성, pull request 만들어줘, PR 열어줘."
---

# PR Create

공용 보조 git 스킬입니다. non-omc 스킬이며 `omc_skill_check` 대상이 아님.

## Phase 0. Ship Gate
- `$omc-ship` 완료 전 중단 | `python3 scripts/omc_guard.py require --target . --for "ship"` | `python3 scripts/omc_tdd_check.py --staged`

## 필수 체크

- ship gate: OMC 가드/TDD gate 통과
- 승인 상태: `미승인`이면 생성 중단
- 쓰기 명령 차단: 승인 전 `git push`, `gh pr create`, `gh label create` 실행 금지

## Phase 1. 읽기 전용 확인
```bash
git status -sb
git log --oneline
git diff --stat
gh auth status
gh pr list --head "$(git branch --show-current)"
cat .github/PULL_REQUEST_TEMPLATE.md
```

- 읽기 전용 확인 완료 기준 | 쓰기/부작용 명령 사전조건 확인 | protected branch 점검: `main/master/trunk` | 중복 PR 확인 | push 필요 확인 | assignee 후보 확인

## Phase 2. 승인/차단 규칙
- 사용자 승인 전 아래 명령 실행 금지
- `git push`: 사용자 승인 전 실행 금지 | `gh pr create`: 사용자 승인 전 실행 금지 | `gh label create`: 사용자 승인 전 실행 금지
- `brew install gh`는 예시로만 안내 | 승인 상태는 `미승인` 또는 `승인`으로 명시

## Phase 3. 본문 초안
- 템플릿이 있으면 `.github/PULL_REQUEST_TEMPLATE.md` 구조 사용, 없으면 fallback 사용

fallback:
- 작업 사항
- 검증
- 리스크/롤백
- 스크린샷 또는 N/A
- 값이 비면 N/A와 이유를 함께 기록

## Phase 4. 출력 계약

```text
PR 준비 상태:
- ship gate:
- protected branch:
- 중복 PR:
- push 필요:
- 승인 상태: 미승인 / 승인

읽기 전용 확인:
- gh auth status:
- gh pr list:

쓰기/부작용 명령:
- git push: 사용자 승인 전 실행 금지
- gh pr create: 사용자 승인 전 실행 금지
- gh label create: 사용자 승인 전 실행 금지

라벨 후보:
assignee 후보:
```

## 다음 추천
- 주추천 1개: PR 준비 완료지만 사용자 승인 전이거나 결과 확인 단계면 사용자 선택 대기
- ship gate 미통과 또는 상태 정합성 재확인 필요면 `$omc-ship` 또는 `$omc-status`
