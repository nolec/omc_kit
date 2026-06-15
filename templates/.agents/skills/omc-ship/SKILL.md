---
skill_name: omc-ship
description: "배포·릴리즈 준비 체크. 트리거: 배포해줘, 릴리즈, 푸시 준비, 배포 준비, 출시하자. TDD 게이트·린트·타입 체크 실행. 테스트 누락·실패 시 배포 차단."
---

# OMC Ship

배포 게이트입니다. 목적은 푸시·배포 전 차단 조건을 확인하는 것이며, `$omc-review` 미완료 또는 치명/중대 이슈가 있으면 배포 차단합니다.

## 필수 체크
- 게이트 통과: OMC 가드/TDD 및 프로젝트 품질 게이트 PASS
- 테스트/타입/린트 PASS, 비밀값 검사 통과, 사용자 승인 확인

사용자에게 보여줄 것: OMC 가드 / TDD 게이트 / 테스트 / 타입 / 린트 / 비밀값 / 승인 상태 / 결론
시스템이 암묵적으로 처리: 프로젝트 명령 탐색(package.json, README, ETHOS.md), Nx 여부 분기, 배포 전 차단 유지

## Phase 0. 게이트

```bash
python3 scripts/omc_guard.py require --target . --for "ship"
python3 scripts/omc_tdd_check.py --run-tests
git status -sb
git diff HEAD
git ls-files --others --exclude-standard
```

프로젝트별 명령은 `package.json`, `README`, `ETHOS.md`에서 확인합니다. 명령은 예시입니다.
- 테스트: Nx면 `npx nx affected --target=test`, Nx 미사용이면 프로젝트 테스트 명령
- 타입/린트: 예 `npx tsc --noEmit`, Nx면 `npx nx affected --target=lint`, Nx 미사용이면 프로젝트 린트 명령
- 비밀값: `SECRET`, `KEY`, `TOKEN`, `PASSWORD`, `.env`가 diff/untracked에 없는지 확인

실패 시: 기존 테스트 회귀/테스트 실패 → `$omc-investigate`, 신규 테스트 누락/TDD 위반 → `$omc-task`

## 실행 차단

모든 게이트 통과 전 `git push`, `deploy`, 배포 스크립트 실행 금지. 사용자 승인 없이 금지하며, 사용자 명시 승인 전에도 금지합니다.

```text
OMC 가드:
TDD 게이트:
테스트:
타입:
린트:
git status -sb:
git diff HEAD:
untracked:
비밀값:
사용자 명시 승인:
결론: SHIP READY / BLOCKED
```

## 배포 예시
- PR 기반: `git push origin HEAD` 후 `$pr-create` / Nx: `npx nx run <app>:deploy` / 직접 배포: 프로젝트 문서의 deploy 명령
실제 배포 후에만 헬스체크, 교훈 기록, `$omc-retro`를 진행합니다.

## 다음 추천
- SHIP READY → 사용자 선택 대기
- 실제 배포 후 → `$omc-retro`
- BLOCKED + 테스트/회귀 실패 → `$omc-investigate`
- BLOCKED + 신규 테스트 누락/TDD 위반 → `$omc-task`
- 자동으로 진행하지는 않습니다.
