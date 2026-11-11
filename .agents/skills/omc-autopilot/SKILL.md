---
name: omc-autopilot
description: "지시문 하나로 plan→task→review→PR 파이프라인을 실행. 트리거: 자동으로 해줘, 자동화, autopilot, 잘 때 돌려줘, pipeline 실행. 상태·계획 조회는 현재 문맥으로 답한다."
---

# OMC Autopilot

상태·계획 조회는 현재 문맥으로 답합니다. 실행 준비 요청일 때만 지시문과 브랜치를 확정하고, 명시 승인 전에는 실행하지 않습니다. 승인 후 실제 pipeline 실행까지 담당합니다.

## 단일 승인 V0 — 명시적으로 선택한 경우만

- 현재 지원은 Codex·Python/pytest·기존 구현 및 테스트 파일 수정이다. 일반 autopilot과 자동 전환하지 않는다.
- 준비 담당은 목표·DoD·base·허용 파일·검증 argv·재현 case ID·독립 Python 검수기 경로/해시·만료 시각·전체 시간·수정 최대 2회를 `omc-autopilot-mission/v2`의 `execution`에 고정한다. 내부 준비 과정은 사용자에게 대신 수행한다.
- 사용자에게는 범위·검수·시간·중단 조건을 한 번 보여주고 실제 승인을 받은 뒤에만 mission 승인 receipt를 생성한다. 모호한 응답·이전 승인을 재사용하지 않는다.
- 기존 `freeze-work-contract`에 승인된 mission·receipt를 전달한다. v2 mission이면 `isolated_candidate_only`를 사용하며 `pipeline --work-contract`로 실행한다. 내부 candidate branch 이름은 사용자에게 재질문하지 않는다.
- 실행기가 같은 구현 작업 폴더에서 CONTRACT→실제 RED→구현→검증→독립 검수→리뷰를 수행한다. 검증 실패·REVISE만 정해진 횟수 안에서 수정하고, BLOCK·등록 실패·만료·범위 위반·검수 격리 불가는 중단한다.
- 유효한 승인 실행 내부에서는 역할·스킬 이름을 다시 입력받지 않는다. 범위·검증·예산 확대에는 새 승인이 필요하다.
- task 완료 가드 통과 후에도 후보 커밋·내용 일치·전체 결과 저장까지 성공해야 후보 준비를 보고한다. 원본 반영·원본 브랜치 생성·PR·배포는 하지 않는다. 중단 후 자동 재개는 지원하지 않으며 기록을 그대로 보존한다.
- 자동 테스트와 실제 Codex 정상/검증수정/리뷰수정 관측은 구분한다. 미실행을 사용자 부담 감소로 보고하지 않는다.

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
- 승인 후 실행: 승인된 같은 지시문·브랜치·dirty 조건의 명령을 실행 도구로 즉시 시작하며, 명령을 답변으로만 출력하지 않음

사용자에게 보여줄 것: 실행 전 확정 / 명령 출력 / 결과 확인 / 다음 액션 | 시스템이 암묵적으로 처리: dirty 판단 / 모드 추정 / 읽기 전용 상태 확인

## Phase 1. 실행 전 확정

```text
AUTOPILOT 실행 전 확정:
- 지시문 / 브랜치 / 모드 / dirty: clean / dirty N개 / N/A — 이유 / 사용자 승인: 미승인 / 승인
```
- 지시문이 모호하면 `$omc-office-hours` 또는 `$omc-brainstorm`
- 짧은 fix/chore/docs는 LITE, 긴 feat는 FULL: plan→task→조건부 critique→review | 고위험 또는 명시적 Full override에서만 critique 실행 | dirty면 실행 차단, 승인 시에만 `--allow-dirty` 안내 | PR 생성 가능성이 있으므로 사용자 승인 없이 시작하지 않음

## Phase 2. 명령 출력 및 승인 후 실행

대안 옵션(실행 명령에 선택한 옵션만 추가): 검증만 `--dry-run` | dirty 승인 `--force --allow-dirty` | 실패 재개 `--resume`

```bash
nohup python3 scripts/omc_autopilot.py pipeline --instruction "[지시문]" --branch "[브랜치]" --mode [auto|lite|full] --auto > .omc/pipeline.log 2>&1 & pipeline_pid=$!
python3 scripts/omc_autopilot.py pipeline-status --expect-pid "$pipeline_pid" --expect-branch "[브랜치]" --expect-instruction "[지시문]" --wait-start 10
```
- 미승인: 실행 명령을 보여주고 사용자 선택 대기 | 승인: 선택한 옵션만 launch에 추가하고 위 두 줄을 같은 셸에서 한 번 실행. 호출 없이 완료로 보고하지 않음

## Phase 3. 결과 확인

```bash
python3 scripts/omc_autopilot.py pipeline-status
python3 scripts/omc_autopilot.py benchmark-report --format json
cat .omc/pipeline.log .omc/pipeline_run_result.json
```
결과: status completed / failed / N/A — 이유 / mode / benchmark-report / PR / 다음 액션. branch와 instruction이 현재 요청과 일치해야 하며 이전 결과는 stale로 판정합니다.

## 다음 추천

- 주추천 1개: 승인 전이거나 결과만 확인 중이면 사용자 선택 대기, 승인 후에는 실행 단계 결과를 보고
- 실패/재확인 단계에서만 `pipeline-status` 또는 benchmark-report 확인
