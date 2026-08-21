---
skill_name: omc-ship
description: "배포·릴리즈 준비 체크. 트리거: 배포해줘, 릴리즈, 푸시 준비, 배포 준비, 출시하자. TDD 게이트·린트·타입 체크 실행. 테스트 누락·실패 시 배포 차단."
---

# OMC Ship

배포 게이트입니다. `$omc-review` 미완료 또는 치명/중대 이슈가 있으면 배포 차단합니다.

## 필수 체크
- OMC 가드/TDD 및 프로젝트 품질 게이트 통과, 테스트/타입/린트 PASS, 비밀값 검사 통과, 사용자 승인 확인

사용자에게 보여줄 것: OMC 가드 / TDD 게이트 / 테스트 / 타입 / 린트 / 비밀값 / 승인 상태 / 결론
시스템이 암묵적으로 처리: 배포 전 차단 유지, 현재 ship 대상 범위와 범위 밖 dirty 변경 분리. 프로젝트 품질 명령은 추측하지 않고 `docs/omc_quality_gates.md` 계약을 따르며 승인 전 실행하지 않습니다.

## Phase 0. 게이트

```bash
python3 scripts/omc_guard.py sync-require --target . --mode autopilot --title "omc-ship" --request "<현재 작업 한 줄 요약>" --roles directive --for "ship"
python3 scripts/omc_tdd_check.py --staged
python3 scripts/omc_quality_gate.py --target . status
python3 scripts/omc_quality_gate.py --target . run
git status -sb
git diff HEAD
git ls-files --others --exclude-standard
```

`status`가 `ready`가 아니면 실행하지 않습니다. 설정이 없거나 근거가 바뀌면 proposal을 생성·검증하고 사용자의 적용 및 실행 승인을 각각 받은 뒤 재시도합니다.
- 비밀값: `SECRET`, `KEY`, `TOKEN`, `PASSWORD`, `.env`가 diff/untracked에 없는지 확인

실패 시: 기존 테스트 회귀/테스트 실패 → `$omc-investigate`, 신규 테스트 누락/TDD 위반 → `$omc-task`

## 실행 차단

모든 게이트 통과 전 사용자 승인 없이는 금지합니다. 사용자 명시 승인 전에는 `git push`, `deploy`, 배포 스크립트 실행 금지입니다.

```text
OMC 가드:
TDD 게이트:
테스트:
타입:
린트:
현재 ship 대상 범위:
범위 밖 dirty 변경:
git status -sb:
git diff HEAD:
untracked:
비밀값:
사용자 명시 승인:
결론: SHIP READY / BLOCKED
```

- 배포 예시: PR 기반 `git push origin HEAD` 후 `$pr-create` / 직접 배포는 프로젝트 문서의 deploy 명령
- 실제 배포 후에만 헬스체크, 교훈 기록, `$omc-retro`를 진행합니다.

## 다음 추천
- 주추천 1개만 제시, 우선순위: 현재 결론에 맞는 1개를 먼저 말합니다.
- SHIP READY → 사용자 선택 대기
- 실제 배포 후 → `$omc-retro`
- BLOCKED + 테스트/회귀 실패 → `$omc-investigate`
- BLOCKED + 신규 테스트 누락/TDD 위반 → `$omc-task`
- 자동으로 진행하지는 않습니다. Machine output contract는 `omc-output/v1`, `stage=ship`, JSON 필드 `outcome/risk/next_skill/user_selection_needed/reason_code`; `PROCEED+all_gates_passed→ready,null,true`, `BLOCK+test_or_regression_failure→blocked,omc-investigate,false`, `BLOCK+tdd_or_test_missing→blocked,omc-task,false`, `BLOCK+approval_missing→blocked,null,true`; 마지막 두 줄은 `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`이며 본문 `SHIP READY`는 machine `PROCEED`, `BLOCKED`는 `BLOCK`입니다.
