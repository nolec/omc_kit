---
skill_name: omc-autopilot
description: "지시문 하나로 plan→task→review→PR 파이프라인을 준비. 트리거: 자동으로 해줘, 자동화, autopilot, 잘 때 돌려줘, pipeline 실행. 상태·계획 조회는 현재 문맥으로 답한다."
---

# OMC Autopilot

상태·계획 조회는 현재 문맥으로 답하고, 실행 준비 요청일 때만 준비 단계에서 지시문과 브랜치를 확정해 전체 파이프라인 명령을 출력합니다. 실제 pipeline 실행 금지.

## Phase 0. 읽기 전용 확인

```bash
git branch --show-current
git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null
git status --porcelain
git log --oneline -3
python3 scripts/omc.py state status --target .
```

## 의도 경계
- 자연어 트리거는 스킬 활성화 신호이며, `다음 계획`·`현재 상태` 같은 조회 표현만 있으면 조회를 우선하고 실행 요청으로 확정하지 않습니다.
- 상태·계획 조회: `다음 계획`·`뭐 해야 해`·`현재 상태`는 동기화된 저장소·브랜치·병목을 답하고 이미 아는 값을 재질문하지 않으며 실행 명령을 출력하지 않습니다.
- 실행 준비: 조회 표현만 있는 경우를 제외하고, 위 모든 자연어 트리거 또는 명시적 `$omc-autopilot` 호출에 실행 가능한 작업이 함께 있으면 아래 게이트로 진입합니다. 작업이 없으면 그 항목만 한 번 묻습니다.
- 실행 문맥 상속은 `active + confirmed + 현재 저장소 일치 + 미완료`일 때만 허용하며 stale·completed·다른 저장소 문맥과 `git symbolic-ref --short refs/remotes/origin/HEAD`로 확인한 저장소 기본 브랜치는 이름과 무관하게 실행값으로 자동 확정하지 않습니다. 기본 브랜치 확인 실패 시에도 브랜치를 상속하지 않고 명시 입력을 요구합니다.
- 조회와 실행 표현이 충돌하면 실행하지 않고 의도만 한 번 확인합니다. 부적합한 단계에서는 현재 병목의 다음 행동 1개만 제시합니다.

## 실행 준비에만 적용하는 필수 체크
- 지시문·브랜치 확정: 빈 값이면 중단
- 명시 승인: `미승인`이면 명령만 제시하고 종료
- 명령만 출력: 실제 실행은 사용자 승인 후 별도 수행

사용자에게 보여줄 것: 실행 전 확정 / 명령 출력 / 결과 확인 / 다음 액션 | 시스템이 암묵적으로 처리: dirty 판단 / 모드 추정 / 읽기 전용 상태 확인

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
cat .omc/pipeline.log .omc/pipeline_run_result.json
```
결과: status completed / failed / N/A — 이유 / mode / benchmark-report / PR / 다음 액션

## 다음 추천

- 주추천 1개: 승인 전이거나 결과만 확인 중이면 사용자 선택 대기
- 실패/재확인 단계에서만 `pipeline-status` 또는 benchmark-report 확인
