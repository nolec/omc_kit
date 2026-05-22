---
skill_name: omc-ship
description: "배포·릴리즈 준비 체크. 트리거: 배포해줘, 릴리즈, 푸시 준비, 배포 준비, 출시하자. TDD 게이트·린트·타입 체크 실행. 테스트 누락·실패 시 배포 차단."
---

# OMC 배포 준비

> **이 스킬을 쓰면 안 되는 상황**:
> - `$omc-review` 미완료 상태 → review 먼저
> - 치명/중대 이슈 열려 있음 → 수정 후 재실행

---

## 순서

```bash
# 1. OMC 가드 확인
python3 scripts/omc_guard.py require --target . --for "ship"

# 2. TDD 게이트
python3 scripts/omc_tdd_check.py --run-tests

# 3. 영향받는 테스트 전체
npx nx affected --target=test

# 4. 타입 체크
npx tsc --noEmit

# 5. 린트
npx nx affected --target=lint

# 6. 현재 상태 확인
git status -sb && git log --oneline -5
```

---

## 배포 전 체크리스트 (각 항목 결과 명시)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SHIP CHECKLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] OMC 가드 통과
    실패 시: omc.py state confirm 후 재실행

[ ] TDD 체크 통과 (반환값 0)
    실패 시: → $omc-task 로 돌아가 테스트 수정

[ ] 타입/린트 에러 0개
    실패 시: 에러 파일 수정 후 재실행

[ ] 테스트 전부 통과
    실패 시: → $omc-investigate 로 원인 파악

[ ] .env / 비밀값 커밋 없음
    확인: git diff HEAD | grep -E "SECRET|KEY|TOKEN|PASSWORD"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 배포 실행

프로젝트에 따라 아래 중 해당하는 방법 사용:

```bash
# Git push (PR 기반)
git push origin HEAD
# → GitHub PR 생성: $pr-create 스킬 사용

# Nx 배포 (CI 연동)
npx nx run <app>:deploy

# 직접 배포
# (프로젝트별 배포 명령 — ETHOS.md 참조)
```

## 배포 후 — Compound Engineering

```bash
python3 scripts/omc_lesson.py add -i
```
