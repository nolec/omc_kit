---
skill_name: omc-ship
description: "배포·릴리즈 준비 체크. 트리거: 배포해줘, 릴리즈, 푸시 준비, 배포 준비, 출시하자. TDD 게이트·린트·타입 체크 실행. 테스트 누락·실패 시 배포 차단."
---

# OMC 배포 준비

## 순서

```bash
python3 scripts/omc_guard.py require --target . --for "ship"
python3 scripts/omc_tdd_check.py --run-tests
npx nx affected --target=test
git status -sb && git log --oneline -5
```

## 배포 전 체크리스트

- [ ] OMC 가드 통과
- [ ] TDD 체크 통과 (`omc_tdd_check.py --run-tests` 반환값 0)
- [ ] 타입/린트 에러 0개
- [ ] 테스트 전부 통과
- [ ] `.env` / 비밀값 커밋 없음

## 배포 후 — Compound Engineering

```bash
python3 scripts/omc_lesson.py add -i
```
