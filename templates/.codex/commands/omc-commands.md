# OMC Commands — Codex CLI

Codex CLI에서 OMC 기능을 사용하는 방법입니다.

> **Codex에서는 `/plan` 같은 슬래시 커맨드를 쓰지 않습니다.**
> `$omc-plan` 형식의 Agent Skills 또는 자연어를 사용하세요.

## 호출 방식

**명시적 호출 (`$` 접두사)**:
```
$omc-plan 로그인 기능 구현
$omc-task 버튼 컴포넌트 추가
$omc-review
$omc-investigate 빌드 오류
$omc-lesson 타입 오류
$omc-status
$omc-ship
$omc-critique
$omc-retro
$omc-benchmark
```

**암묵적 트리거 (자연어)**: 아래 중 하나라도 해당하면 CONTRACT 양식을 즉시 출력하고 사용자 컨펌을 받습니다.

| 상황 | 예시 |
|---|---|
| 구현/수정/추가 키워드 | "구현해줘", "추가해줘", "수정해줘", "만들어줘", "feature", "개발" |
| 스킬/방법 질문 | "스킬 뭐 써야해", "어떻게 해야 해", "$omc-* 뭐 써야해" |
| 기존 파일 수정 요청 | "이 파일에 ~를 넣어줘", "~쪽 바꿔줘", 특정 파일명 + 변경 요청 |
| 버그 수정 요청 | "이거 왜 안 돼", "버그 고쳐줘", "~가 이상해" |

> **"작업이 명확해 보여도 CONTRACT를 건너뛰지 않는다."**

> 스킬 파일 위치: `.agents/skills/omc-*/SKILL.md`

---

## 코어 스킬 (매일 쓰는 것)

### `$omc-plan [작업]`

구현 전 TDD 태스크 분해:
1. 목표 / 범위(포함·제외) / DoD / 제약 확정
2. 태스크마다 `RED(테스트) → GREEN(구현) → VERIFY(커맨드)` 명시
3. 확정 후 `python3 scripts/omc.py state confirm --target .` 실행

### `$omc-task [설명]`

7단계 TDD 파이프라인 진입:
CONTRACT → DESIGN → RED🔴 → GREEN🟢 → REFACTOR🔵 → TDD GATE → REVIEW

**PHASE 3 RED 등록 없이 구현 파일 생성 시도 금지.**

```bash
python3 scripts/omc_pipeline_guard.py red-done <테스트파일>
```

### `$omc-review`

`git diff` 또는 현재 변경사항 기준 코드 리뷰:
- 치명 / 중대 / 경미 / 제안 4단계 분류
- 각 이슈에 파일/라인/근거 첨부
- 수정 제안 + 검증 커맨드 포함

```bash
git status -sb && git diff
```

### `$omc-investigate [이슈]`

4단계 디버깅 방법론:
1. **ROOT CAUSE**: 현상 정량화 (로그/코드 근거만, 추측 금지)
2. **PATTERN ANALYSIS**: 가설 분류 (데이터/로직/상태/환경/인터페이스)
3. **HYPOTHESIS TESTING**: 한 번에 하나씩 검증, 3번 실패 시 아키텍처 재검토
4. **IMPLEMENTATION**: 최소 변경 수정 + 교훈 캡처

**철칙: 근본 원인 확인 전 수정 시작 금지.**

### `$omc-lesson [키워드]`

BM25 유사도 검색으로 관련 교훈 출력:

```bash
python3 scripts/omc_lesson.py search "[키워드]" --top 5
python3 scripts/omc_lesson.py list
python3 scripts/omc_lesson.py add -i    # 교훈 추가
```

### `$omc-status`

현재 OMC 상태 확인:

```bash
python3 scripts/omc.py state status --target .
python3 scripts/omc_pipeline_guard.py status
```

### `$omc-benchmark [기능]`

세계 1등 제품과 비교해 갭 분석 + 차별화 포인트를 도출합니다.
비교 대상은 사용자가 직접 확정해야 하며, 없으면 후보 3개 이하만 선택지로 제안합니다.

출력:
- 검증 상태
- 갭 분석 최소 3개
- 차별화 포인트 1개
- 우선순위 TOP 1
- 다음 액션: 실제 제품 검증 / `$omc-office-hours` / `$omc-plan`

### `$omc-ship`

배포 준비 체크:
1. `python3 scripts/omc_guard.py require --target . --for "ship"` → 미확정 세션 차단
2. `python3 scripts/omc_tdd_check.py --staged` → 테스트 없는 파일 차단
3. 타입/린트/테스트/빌드 통과 확인
4. 전부 통과 시에만 배포 진행

---

## 선택 스킬 (필요할 때만)

### `$omc-brainstorm [주제]`
소크라테스식 4단계 탐색: What(현상) → Why(원인) → How(해결 방향) → Decide(옵션 A/B/C)

### `$omc-office-hours [요청]`
6개 강제 질문: 대상 사용자 / 핵심 고통 / 성공 기준 / MVP / 비범위 / 10점 버전

### `$omc-ceo-review [모드]`
모드: `EXPAND` / `SELECTIVE` / `HOLD`(기본) / `REDUCE`

### `$omc-retro`
최근 세션 히스토리 분석 + 회고 포맷 출력 + 교훈 캡처

---

## 권장 순서

```
$omc-plan → $omc-task → $omc-review → $omc-ship → $omc-retro
```

---

## autopilot — 전체 파이프라인 자동 실행

지시문 하나로 plan → task → review → PR 전체를 자동 실행합니다.

**모드 자동 결정**: fix/hotfix/chore/docs 브랜치 또는 지시문 50자 이하 → LITE,
feat + 긴 지시문 → FULL (plan→critique→task→review)

```bash
# 흐름 먼저 확인 (dry-run)
python3 scripts/omc_autopilot.py pipeline \
  --instruction "구현할 내용" \
  --branch "feat/기능명" \
  --dry-run

# 백그라운드 실행 (수십 분 소요)
nohup python3 scripts/omc_autopilot.py pipeline \
  --instruction "구현할 내용" \
  --branch "feat/기능명" \
  --allow-dirty \
  > .omc/pipeline.log 2>&1 &

echo "PID: $!  |  로그: .omc/pipeline.log"
```

# 실패한 파이프라인 재개
python3 scripts/omc_autopilot.py pipeline \
  --instruction "[이전과 동일한 지시문]" \
  --branch "[이전 브랜치명]" \
  --resume


### 결과 확인 (pipeline-status)

```bash
# 1회 확인
python3 scripts/omc_autopilot.py pipeline-status

# 실시간 모니터링 (2초 간격)
python3 scripts/omc_autopilot.py pipeline-status --watch

# 간격 조정 (3초)
python3 scripts/omc_autopilot.py pipeline-status --watch --interval 3
```

### 고급 사용 — task 파일 기반 방식

```bash
python3 scripts/omc_autopilot.py new --id feat-x --title "기능 X"
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json --dry-run
```
