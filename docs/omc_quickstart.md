# OMC Kit Quickstart

## 30초 설치 후 바로 시작

```bash
# 1. 상태 확인
python3 scripts/omc_doctor.py --target .

# 2. 운영 요약 확인
python3 scripts/omc_autopilot.py overview

# 3. 첫 작업 요청
python3 scripts/omc.py "만들고 싶은 것"

# 4. 스프린트 순서 (권장)
# /brainstorm → /office-hours → /plan → /task → /review → /ship → /retro
```

## 슬래시 커맨드 / 스킬

### Claude Code · Antigravity IDE (슬래시 커맨드)

| 커맨드 | 설명 |
|--------|------|
| `/brainstorm [주제]` | 요구사항 소크라테스식 탐색 |
| `/office-hours [요청]` | 제품 사고 먼저 — 6개 강제 질문 |
| `/ceo-review [모드]` | CEO 관점 기능 범위 재검토 |
| `/plan [작업]` | TDD 태스크 분해 |
| `/task [설명]` | 7단계 TDD 파이프라인 |
| `/review` | git diff 코드 리뷰 |
| `/investigate [이슈]` | 4단계 디버깅 방법론 |
| `/lesson [키워드]` | BM25 교훈 검색 |
| `/ship` | TDD 게이트 → 배포 |
| `/retro` | 회고 + 교훈 캡처 |
| `/status` | OMC 상태 확인 |

### Codex IDE (Agent Skills)

명시적: `$omc-plan`, `$omc-task`, `$omc-review` 등  
암묵적: "계획해줘", "태스크 나눠줘" 등 자연어로도 자동 트리거됩니다.

### Cursor

`.cursor/rules/omc-always.md`의 항상 적용 규칙으로 동작합니다. 슬래시 커맨드 없이 자연어로 요청하면 OMC 흐름을 따릅니다.

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

## 명시적 검증 & CI

커밋 전 staged 변경의 TDD 게이트를 확인합니다.

```bash
python3 scripts/omc_tdd_check.py --staged
```

## 파일 지도

- `docs/kit_map.md` — 스크립트 전체 목록
- `docs/quickstart_kr.md` — 시나리오별 사용 가이드
- `docs/next_project_pack.md` — 다음 프로젝트로 이식하는 방법
