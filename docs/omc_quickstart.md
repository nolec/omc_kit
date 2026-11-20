# OMC Kit Quickstart

## Codex에서 Superpowers와 함께 시작하기

이 가이드는 Superpowers로 계획·구현·검증을 진행하는 Codex 프로젝트를 기준으로 합니다. 작업 요청은 Superpowers 흐름에서 시작하고, 아래 상황에 해당할 때 OMC를 직접 호출합니다. 명령은 OMC가 설치된 프로젝트 루트에서 실행합니다.

| 필요한 때 | OMC 기능 |
|---|---|
| 작업을 시작하거나 다시 열었을 때 | `python3 scripts/omc.py state status --target .`로 작업 상태를 조회합니다. Superpowers 계획 원장이 있으면 `--superpowers-ledger`에 경로를 지정해 마지막 완료 범위를 현재 Git 이력과 대조할 수 있습니다. |
| 커밋 전에 다른 변경이 섞였는지 확인할 때 | `python3 scripts/omc.py scope-check --target . --expected-path scripts/example.py`로 요청 범위와 staged 경로를 비교합니다. |
| 기존 검증에서 빠진 UI 확인 항목을 찾을 때 | Codex에서 `$omc-qa`를 호출하고 기존 테스트 결과와 실행 환경을 함께 전달합니다. |
| 프로젝트 교훈을 찾을 때 | `python3 scripts/omc_lesson.py --target . search "키워드"`를 실행합니다. |

## 현재 지원 범위

OMC는 Codex Agent Skills(`.agents/skills`)와 공통 CLI를 지원합니다. Claude Code·Gemini CLI·Antigravity·Cursor 전용 설치 지원은 종료됐습니다. OMC 자동 세션·도구·Git 훅은 비활성 상태입니다.

## OMC 단독 운영 예시

OMC만으로 작업을 진행할 때는 Codex에서 필요한 스킬을 명시적으로 호출합니다. 요구사항이 모호하면 Superpowers의 브레인스토밍으로 범위를 정리하거나, OMC 단독 운영에서는 `$omc-office-hours`의 질문으로 시작합니다. 아래는 OMC 단독 운영 예시입니다.

```text
$omc-office-hours → $omc-plan → $omc-task → $omc-review → $omc-ship → $omc-retro
```

설치 상태와 자동화 기능의 운영 요약이 필요할 때 각각 다음 명령을 실행합니다.

```bash
python3 scripts/omc_doctor.py --target .
python3 scripts/omc_autopilot.py overview
```

## 자주 쓰는 CLI 명령

```bash
python3 scripts/omc.py state status       # 현재 상태
python3 scripts/omc.py state compact      # 메모리 압축
python3 scripts/omc_lesson.py search "키워드"  # 교훈 BM25 검색
python3 scripts/omc_autopilot.py new --id feat-x --title "기능 X"
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json --dry-run
```

`state status`는 파일을 초기화하거나 갱신하지 않습니다. Git staged·unstaged·untracked는 현재 Git 상태이며 사용자 요청이나 커밋 승인 범위를 뜻하지 않습니다. 상태 파일이나 Git 확인이 불가능하면 해당 결과를 확인 불가로 표시합니다. 오래된 작업에 pending이 없고 완료 영수증과 Git의 추가 판정이 필요한 경우 lifecycle을 확인 불가로 표시하며 해제·승인을 뜻하지 않습니다. 실제 쓰기 가드 판정은 그대로입니다. 초기화는 별도의 `state init` 명령으로 수행합니다.

명시한 작업 경로와 현재 staged 경로를 읽기 전용으로 비교하려면 `python3 scripts/omc.py scope-check --target . --expected-path scripts/example.py`를 실행합니다. 경로마다 `--expected-path`를 반복하고 저장소 루트 기준으로 지정합니다. JSON의 `MATCH`/`MISMATCH`는 **경로만** 비교하며 각각 종료 코드 0/1입니다. 경로 누락, 충돌 또는 Git 조회 실패는 `UNKNOWN`(종료 코드 2)입니다. 이름 변경은 이전·이후 경로를 모두 지정합니다. 출력의 unstaged와 untracked는 Git이 보고하는 범위이며 무시된 파일과 `assume-unchanged`·`skip-worktree` 등으로 Git이 숨긴 변경은 누락될 수 있습니다. `path_comparison`은 경로 일치만 뜻하며 `content_status: NOT_VERIFIED`는 내용 미검증을 뜻합니다. 같은 파일에 staged·unstaged 변경이 있으면 `staged_and_unstaged`와 `warnings`에 표시되므로 staged 내용에 새 작업이 포함됐다고 추정하지 마세요. 내용 검증, 사용자 승인, 리뷰, staging 또는 커밋을 수행하지 않습니다. 실행 시점의 스냅샷이므로 이후 변경은 별도로 확인해야 합니다.


특정 세션의 로컬 완료 기록은 `python3 scripts/omc.py state capture-status --target . --session-id SESSION --commit FULL_COMMIT_ID`로 조회합니다. `MATCH`(0)는 기록이 지정 커밋과 일치함을 뜻합니다. `MISMATCH` 또는 `NO_RECORD`(1)는 각각 다른 커밋의 기록 또는 지정 세션의 기록 없음이며, 판별할 수 없으면 `UNKNOWN`(2)입니다. 기록의 부모 커밋과 변경 경로를 Git과 대조합니다. 별도 `verification.tree_comparison`은 지정 세션의 서명된 검증 기록과 커밋 트리를 비교합니다. 기록이 없으면 `NO_EVIDENCE`, 서명 또는 출력 근거를 검증할 수 없으면 `UNKNOWN`입니다. `passed_for_commit`은 트리가 일치할 때의 검증 결과이며, 트리가 다르거나 근거가 없으면 `null`입니다. 종료 코드는 기존 완료 기록 판정을 따릅니다. 두 옵션을 생략하면 기존 리허설 상태를 표시합니다.

Superpowers 계획을 재개하기 전에 `python3 scripts/omc.py state status --target . --superpowers-ledger .superpowers/sdd/PLAN/progress.md`로 명시한 원장을 점검할 수 있습니다. 저장소 안의 원장에 적힌 마지막 완료 범위의 두 커밋을 해석하고, 범위가 유효하며 끝 커밋이 현재 HEAD에 포함되는지 확인합니다. 결과는 `INCLUDED`(0), `NOT_INCLUDED`(1), `UNKNOWN`(2)입니다. 이력 누락이나 모호한 커밋은 판별 불가이며, 이력 재작성 뒤 같은 변경이 다시 적용됐는지는 비교하지 않습니다. 옵션을 생략하면 기존 상태 요약을 표시합니다.

잘못된 교훈을 철회하려면 `python3 scripts/omc_lesson.py --target . retract OLD.md --reason "정책 변경" --replacement NEW.md`를 실행합니다. 대체 교훈은 이미 저장된 활성 교훈의 정확한 파일명이며, 아직 대체 교훈이 없다면 옵션을 생략할 수 있습니다. 철회 기록은 원문 옆에 별도로 남습니다. `show OLD.md`로 원문과 철회 이유를 볼 수 있습니다. 검색과 새 컨텍스트 추천에는 활성 교훈만 포함되며, 이전에 생성한 컨텍스트는 다시 생성해야 반영됩니다.

여러 교훈의 대체 관계는 `python3 scripts/omc_lesson.py --target . doctor`로 점검합니다. 각 철회 기록의 직접 연결을 확인하고, 대체 교훈의 상태 및 원본 교훈이 빠진 기록을 표시합니다.

`python3 scripts/omc_lesson.py --target . doctor --replacement current.md`는 `current.md`를 직접 가리키는 철회 기록을 조회합니다. 정확한 `.md` 파일명을 지정하면 대상 파일이 삭제된 뒤에도 참조를 찾을 수 있습니다. 읽을 수 없는 철회 기록이 있으면 확인된 일치 수와 함께 `조회 불완전`을 표시합니다.

종료 코드 `0`은 확인할 문제 없음, `1`은 확인이 필요한 기록이 있거나 조회가 불완전함, `2`는 잘못된 파일명 또는 교훈 디렉터리 점검 불가를 뜻합니다.

## 명시적 검증 & CI

커밋 전 staged 변경의 TDD 게이트를 확인합니다.

```bash
python3 scripts/omc_tdd_check.py --staged
```

## 파일 지도

- `docs/kit_map.md` — 스크립트 전체 목록
- `docs/quickstart_kr.md` — 시나리오별 사용 가이드
- `docs/next_project_pack.md` — 다음 프로젝트로 이식하는 방법
