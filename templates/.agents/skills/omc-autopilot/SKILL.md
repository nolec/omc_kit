---
skill_name: omc-autopilot
description: "지시문 하나로 plan→task→review→PR 전체 파이프라인을 자동 실행. 트리거: 자동으로 해줘, 자동화, autopilot, 잘 때 돌려줘, pipeline 실행. 반드시 지시문과 브랜치명을 먼저 확정한다."
---

# OMC Autopilot

지시문과 브랜치를 확정한 뒤 전체 파이프라인 명령만 출력하는 준비 단계 스킬입니다. 실제 pipeline 실행 금지.

## Phase 0. 읽기 전용 확인

```bash
git branch --show-current
git status --porcelain
git log --oneline -3
python3 scripts/omc.py state status --target .
```

## 필수 체크
- 지시문·브랜치 확정: 빈 값이면 중단
- 명시 승인: `미승인`이면 명령만 제시하고 종료
- 명령만 출력: 실제 실행은 사용자 승인 후 별도 수행

사용자에게 보여줄 것: 실행 전 확정 / 명령 출력 / 결과 확인 / 다음 액션
시스템이 암묵적으로 처리: dirty 판단 / 모드 추정 / 읽기 전용 상태 확인

## Phase 1. 실행 전 확정

```text
AUTOPILOT 실행 전 확정:
- 지시문 / 브랜치 / 모드 / dirty: clean / dirty N개 / N/A — 이유 / 사용자 승인: 미승인 / 승인
```
- 지시문이 모호하면 `$omc-office-hours` 또는 `$omc-brainstorm`
- 짧은 fix/chore/docs는 LITE, 긴 feat는 FULL: plan→critique→task→review | dirty면 실행 차단, 승인 시에만 `--allow-dirty` 안내 | PR 생성 가능성이 있으므로 사용자 승인 없이 시작하지 않음

## Phase 2. 명령 출력

```bash
nohup python3 scripts/omc_autopilot.py pipeline --instruction "[지시문]" --branch "[브랜치]" --mode [auto|lite|full] --auto > .omc/pipeline.log 2>&1 &
python3 scripts/omc_autopilot.py pipeline --instruction "[지시문]" --branch "[브랜치]" --dry-run
python3 scripts/omc_autopilot.py pipeline --instruction "[지시문]" --branch "[브랜치]" --force --allow-dirty
python3 scripts/omc_autopilot.py pipeline --instruction "[지시문]" --branch "[브랜치]" --resume
```

## Phase 3. 결과 확인

```bash
python3 scripts/omc_autopilot.py pipeline-status
python3 scripts/omc_autopilot.py benchmark-report --format json
cat .omc/pipeline.log
cat .omc/pipeline_run_result.json
```

```text
결과:
- status: completed / failed / N/A — 이유 / mode / benchmark-report / PR / 다음 액션
```

## 다음 추천

- 주추천 1개: 승인 전이거나 결과만 확인 중이면 사용자 선택 대기
- 실패/재확인 단계에서만 `pipeline-status` 또는 benchmark-report 확인
