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

> **AI는 아래 커맨드를 순서대로 직접 실행하고 결과를 확인한다. 건너뛰지 않는다.**
> Nx 미사용 프로젝트는 `npx nx affected` 대신 `npx jest`, `npx eslint .` 로 대체한다.

```bash
# 1. OMC 가드 확인
python3 scripts/omc_guard.py require --target . --for "ship" 2>/dev/null

# 2. TDD 게이트
python3 scripts/omc_tdd_check.py --run-tests

# 3. 영향받는 테스트 전체 (Nx 미사용 시: npx jest)
npx nx affected --target=test

# 4. 타입 체크
npx tsc --noEmit

# 5. 린트 (Nx 미사용 시: npx eslint .)
npx nx affected --target=lint

# 6. 현재 상태 확인
git status -sb && git log --oneline -5
```

---

수집 결과 연결:
- TDD 체크 결과 → 체크리스트 항목 판단

## 배포 전 체크리스트

> **모든 항목이 통과된 후에만 배포 실행 섹션으로 진입한다.**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SHIP CHECKLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] OMC 가드 통과
    실패 시: omc.py state confirm 후 재실행

[ ] TDD 체크 통과 (반환값 0)
    실패 시 — 기존 테스트 회귀: → $omc-investigate 로 원인 파악
    실패 시 — 신규 테스트 누락: → $omc-task 로 돌아가 테스트 추가

[ ] 타입/린트 에러 0개
    실패 시: 에러 파일 수정 후 재실행

[ ] 테스트 전부 통과
    실패 시: → $omc-investigate 로 원인 파악

[ ] .env / 비밀값 커밋 없음
    확인: git diff HEAD | grep -E "SECRET|KEY|TOKEN|PASSWORD"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

☐ 모든 항목 통과 — 배포 실행 진입 가능

---

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
# ETHOS.md가 없으면 프로젝트 README 또는 package.json scripts 확인
```

배포 완료 확인:
```bash
# 헬스체크 또는 배포 로그 확인 (프로젝트별 상이)
# curl https://<배포 URL>/health
# 또는 CI/CD 파이프라인 결과 확인
```

---

## 배포 후 — Compound Engineering (MANDATORY)

배포 후 반드시 교훈을 기록한다. 교훈이 없어도 "없음"을 명시하고 완료한다.

```bash
python3 scripts/omc_lesson.py add -i
```


> 답하기 어려운 항목은 `N/A — [이유]` 형식으로 기재한다. 빈칸으로 두지 않는다.

## 이후 액션

| 결과 | 다음 단계 |
|---|---|
| 배포 성공 | `$omc-retro` 교훈 캡처 |
| 배포 실패 | `$omc-investigate` 원인 추적 |
