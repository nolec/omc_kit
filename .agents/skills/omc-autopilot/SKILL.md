---
skill_name: omc-autopilot
description: "지시문 하나로 plan→task→review→PR 전체 파이프라인을 자동 실행. 트리거: 자동으로 해줘, 자동화, autopilot, 잘 때 돌려줘, pipeline 실행. 반드시 지시문과 브랜치명을 먼저 확정한다."
---

# OMC Autopilot

지시문과 브랜치를 확정한 뒤 전체 파이프라인을 안내합니다. 실제 pipeline 실행 금지: 이 스킬은 명령을 출력하고, 사용자가 승인한 뒤 별도 실행하게 합니다.

## Phase 0. 읽기 전용 확인

```bash
git branch --show-current
git status --porcelain
git log --oneline -3
python3 scripts/omc.py state status --target .
```

- 현재 브랜치: 새 브랜치 제안에 사용
- dirty 상태: 실제 실행 차단 여부 판단
- 세션 상태: 진행 중 작업이 있으면 알림

## 필수 체크

- 지시문·브랜치 확정: 빈 값이면 중단
- 명시 승인: `미승인`이면 명령만 제시하고 종료
- 명령만 출력: 실제 실행은 사용자 승인 후 별도 수행

## Phase 1. 실행 전 확정

```text
AUTOPILOT 실행 전 확정:
- 지시문:
- 브랜치:
- 모드: LITE / FULL / auto
- dirty: clean / dirty N개 / N/A — 이유
- 사용자 승인: 미승인 / 승인
```

- 지시문이 모호하면 `$omc-office-hours` 또는 `$omc-brainstorm`
- `fix/`, `hotfix/`, `chore/`, `docs/` 또는 짧은 지시문은 LITE
- `feat/`와 긴 지시문은 FULL: plan→critique→task→review
- dirty면 실제 실행은 차단하고, 승인 시에만 `--allow-dirty` 안내
- PR 생성 가능성이 있으므로 사용자 승인 없이 시작하지 않음

## Phase 2. 명령 출력

clean 상태:

```bash
nohup python3 scripts/omc_autopilot.py pipeline \
  --instruction "[지시문]" \
  --branch "[브랜치]" \
  --mode [auto|lite|full] \
  --auto \
  > .omc/pipeline.log 2>&1 &
```

dry-run:

```bash
python3 scripts/omc_autopilot.py pipeline \
  --instruction "[지시문]" \
  --branch "[브랜치]" \
  --dry-run
```

예외 옵션:

```bash
python3 scripts/omc_autopilot.py pipeline \
  --instruction "[지시문]" \
  --branch "[브랜치]" \
  --force \
  --allow-dirty

python3 scripts/omc_autopilot.py pipeline \
  --instruction "[지시문]" \
  --branch "[브랜치]" \
  --resume
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
- status: completed / failed / N/A — 이유
- mode: LITE / FULL / auto
- benchmark-report: json / N/A — 이유
- PR:
- 다음 액션:
```
