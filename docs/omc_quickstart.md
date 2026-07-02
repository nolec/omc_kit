# OMC Kit Quickstart

## 30초 설치 후 바로 시작

```bash
# 1. 상태 확인
python3 scripts/omc_doctor.py --target .

# 2. 첫 작업 요청
python3 scripts/omc.py "만들고 싶은 것"

# 3. 스프린트 순서 (권장)
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
python3 scripts/omc_autopilot.py overview # autopilot/overview 상태 확인
python3 scripts/omc_autopilot.py new --id feat-x --title "기능 X"
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json --dry-run
```

## 자동 가드 & CI

- 구현 전 세션/가드는 `python3 scripts/omc_guard.py sync-require --target . ...` 로 동기화합니다.
- 스테이징된 변경은 `python3 scripts/omc_tdd_check.py --staged` 로 TDD 게이트를 확인합니다.
- 전체 회귀는 CI와 `python3 -m pytest scripts/ -q` 기준으로 맞춥니다.

## 파일 지도

- `docs/kit_map.md` — 스크립트 전체 목록
- `docs/quickstart_kr.md` — 시나리오별 사용 가이드
- `docs/next_project_pack.md` — 다음 프로젝트로 이식하는 방법
