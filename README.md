# OMC - Orchestrated Multi-agent Craft

TDD 게이트와 telemetry를 갖춘 멀티 LLM 오케스트레이션 킷입니다. Codex, Claude Code, Gemini CLI, Cursor에서 같은 프로젝트 규칙과 작업 흐름을 사용할 수 있습니다.

## 현재 상태

2026-07-13 기준 OMC는 **안전한 자동 오케스트레이션**에 초점을 둔 단계입니다.

- V1 스킬 기반 라우팅: 완료
- V2 단계별 모델 라우팅: 완료
- V3 실패 감지·재시도·승격: 완료
- V4 telemetry·비용·KPI·observed 검증: 완료
- Operator Experience: 진행중
- Executor candidate 관측과 child handoff/dependency validation: 추천-only 구현 완료
- 실제 executor 호출·승인 lifecycle·자동 위임·자동 모델 전환: 아직 미구현

현재 executor 관련 결과는 모두 `execution_allowed=false`입니다. OMC는 먼저 작업을 분해하고, 후보·근거·scope·dependency를 설명한 뒤 사람이 다음 실행을 결정하도록 합니다.

상세 상태와 남은 작업은 [자동 모델 라우팅 로드맵](docs/automatic_model_routing_roadmap.md)을 참고하세요.

## 주요 기능

- **공통 작업 흐름**: plan, task, critique, review, ship, status, reentry 등 역할별 스킬 제공
- **TDD 게이트**: CONTRACT -> RED -> GREEN -> REFACTOR -> TDD GATE 흐름 관리
- **멀티 LLM 라우팅**: 요청 난이도·위험도·정책 profile에 따른 모델 강도와 executor 후보 추천
- **실패 대응**: retry, plan retry, timeout, critique/review 실패, reroute 경로를 decision engine으로 정리
- **운영 telemetry**: token, cost, retry, reroute, 성공률, multi-run KPI를 `.omc/runs`에 기록
- **observed evidence**: executor별 capability evidence를 fresh/stale·환경·품질 상태와 함께 집계
- **추천-only handoff**: parent-child scope, dependency, cycle, malformed metadata를 검증하고 다음 액션을 제시
- **설명 가능한 결과**: `decision`, `risk`, `next_action`, 추천 이유와 policy confidence를 유지
- **안전한 opt-in autopilot**: 단순·저위험·scope-fixed 작업만 별도 조건에서 제한적으로 실행 가능

## 설치

```bash
git clone https://github.com/nolec/omc_kit.git
cd omc_kit
python3 scripts/install.py --target /path/to/your-project
```

기존 파일을 OMC 최신 템플릿으로 갱신할 때만 `--force`를 사용합니다.

```bash
python3 scripts/install.py --target /path/to/your-project --force
```

여러 저장소에 설치할 때는 OMC 킷을 별도로 보관하고 각 target에 반복 설치합니다. 설치 후 target 저장소의 프로젝트 규칙과 기존 `AGENTS.md` 내용은 확인하고, 무조건 덮어쓰지 마세요.

## 설치 후 점검

1. `ETHOS.md`의 프로젝트 맥락과 `CONVENTIONS.md`의 팀 규칙을 프로젝트에 맞게 작성합니다.
2. 설치 상태를 확인합니다.

```bash
python3 scripts/omc_doctor.py
```

3. Git pre-commit hook을 설치하거나 doctor의 수정 옵션을 사용합니다.

```bash
cp scripts/pre-commit.sample .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
# 또는
python3 scripts/omc_doctor.py --fix
```

4. 세션 상태를 초기화합니다.

```bash
OMC_EXECUTOR=codex python3 scripts/omc.py state init --target .
OMC_EXECUTOR=codex python3 scripts/omc.py hook session_start --target .
```

## 사용 방법

### 일반 요청

Codex에서는 Agent Skill을 사용합니다.

```text
$omc-plan 로그인 기능 구현 계획
$omc-task 계획에 따라 구현
$omc-review 변경사항 리뷰
```

Claude Code와 Gemini CLI에서는 slash command를 사용합니다.

```text
/plan 로그인 기능 구현 계획
/task 계획에 따라 구현
/review 변경사항 리뷰
```

Cursor는 설치된 rules와 hooks가 자연어 요청을 분류합니다.

### 권장 흐름

```text
간단한 작업       -> task -> review
복잡한 작업       -> plan -> task -> review
고위험·모호 작업  -> brainstorm/office-hours/critique -> plan -> task -> review
배포·커밋 준비    -> review 승인 후 명시적으로 ship
```

스킬이 끝난 뒤 다음 스킬로 자동 진입하지 않습니다. 결과의 `next_action`을 확인하고 사용자가 다음 스킬을 명시적으로 실행합니다.

### 상태와 오케스트레이션 미리보기

```bash
# 현재 세션 상태
python3 scripts/omc.py state status --target .

# 자연어 요청을 단계 graph로만 분석
python3 scripts/omc.py orchestrate \
  --request "결제 API를 교체하고 프론트 테스트를 업데이트해줘" \
  --dry-run
```

`orchestrate --dry-run`은 실제 LLM이나 executor를 호출하지 않습니다.

### 구조화된 autopilot

```bash
# 태스크 파일 생성
python3 scripts/omc_autopilot.py new --id my-feature --title "기능 구현"

# 실행 계획만 확인
python3 scripts/omc.py autopilot \
  --task-file .omc/tasks/my-feature.json \
  --dry-run

# 실행 기록 조회
python3 scripts/omc_autopilot.py status
```

일반 autopilot은 실제 LLM 호출과 검증을 수행할 수 있습니다. 단, 복잡한 작업의 자동 위임·자동 ship은 기본 제공하지 않으며, 단순 작업 실행도 명시적인 opt-in gate가 필요합니다.

태스크 파일의 핵심 필드는 `steps`, `depends_on`, `timeout_sec`, `expect.files`, `expect.checks`, `max_retries`입니다. 실패 시 검증 출력이 다음 retry 문맥에 전달됩니다.

## LLM별 진입점

| 실행기 | 기본 진입점 | 설정 위치 |
|---|---|---|
| Codex | `$omc-plan`, `$omc-task`, `$omc-review` | `.agents/skills/`, `.codex/` |
| Claude Code | `/plan`, `/task`, `/review` | `.claude/commands/`, `.claude/` |
| Gemini CLI | `/plan`, `/task`, `/review` | `.gemini/commands/`, `.gemini/` |
| Cursor | 자연어 요청 | `.cursor/rules/`, `.cursor/hooks/` |

모든 실행기는 공통 규칙을 `AGENTS.md`에서 읽고, 실행기별 overlay는 각 개인 설정 영역에서 읽습니다.

## 안전 경계

- 새 파일·기능·동작 변경은 CONTRACT와 TDD 흐름을 거칩니다.
- 테스트 없는 신규 구현 파일은 완료로 보지 않습니다.
- `execution_allowed=false`인 추천 결과를 실행 권한으로 해석하지 않습니다.
- observed candidate는 executor eligibility가 아닙니다.
- 승인 scope가 바뀌면 fingerprint mismatch로 다시 검토합니다.
- dependency가 완료되지 않은 child는 다음 단계로 진행하지 않습니다.
- `eligible` threshold, 실제 비용 정책, 자동 executor 전환은 아직 확정하지 않았습니다.
- 커밋·ship은 사용자가 명시적으로 요청한 경우에만 수행합니다.

## 핵심 명령과 스크립트

| 경로 | 역할 |
|---|---|
| `scripts/omc.py` | prompt, state, orchestrate, autopilot 진입점 |
| `scripts/omc_orchestrator.py` | 요청 분류, 단계 graph, capability/handoff contract |
| `scripts/omc_autopilot.py` | 구조화된 multi-step 실행과 상태 조회 |
| `scripts/omc_pipeline_guard.py` | CONTRACT·RED 파이프라인 gate |
| `scripts/omc_tdd_check.py` | staged 변경의 테스트 커버리지 검사 |
| `scripts/omc_doctor.py` | 설치·hook 진단 및 수정 |
| `scripts/omc_sync_ssot.py` | 템플릿 SSOT 동기화 검사 |
| `scripts/omc_lesson.py` | 교훈 저장·검색 |
| `scripts/install.py` | target 저장소 설치·force 갱신 |

## 디렉토리 구조

```text
omc_kit/
├── scripts/                 실행기, guard, orchestrator, autopilot
├── templates/
│   ├── AGENTS.md             공통 규칙 SSOT
│   ├── CLAUDE.md             Claude overlay
│   ├── GEMINI.md             Gemini overlay
│   ├── CODEX.md              Codex overlay
│   ├── ETHOS.md              엔지니어링 원칙
│   ├── CONVENTIONS.md        코딩 컨벤션
│   ├── .agents/skills/       Codex skill 원본
│   ├── .claude/              Claude commands/hooks
│   ├── .codex/               Codex commands/hooks
│   ├── .cursor/              Cursor rules/hooks
│   ├── .gemini/              Gemini commands/hooks
│   ├── .agent-hooks/         공통 hook 구현
│   └── shared_tasks/         설치 대상 공통 task
├── docs/                     로드맵·운영 정책·설계 문서
└── .github/workflows/        CI
```

스킬 원본은 `templates/.agents/skills/`에서 관리하고, target 저장소에는 `scripts/install.py`로 배포합니다.

## 검증

관련 orchestrator·로드맵 테스트:

```bash
python3 -m pytest \
  scripts/test_omc_orchestrator.py \
  scripts/test_automatic_model_routing_roadmap.py -q
```

전체 스크립트 테스트:

```bash
python3 -m pytest scripts -q
```

커밋 전 staged TDD gate:

```bash
python3 scripts/omc_tdd_check.py --staged
```

## 로드맵

현재 우선순위는 다음 순서입니다.

1. V4 observed telemetry와 Operator Experience 유지 검증
2. 추천-only child handoff와 dependency contract 확장
3. 충분한 운영 데이터가 쌓인 뒤 비용·품질 기반 executor eligibility 정책 검토
4. 승인 기반 실행·budget/retry/timeout guard 검토
5. 마지막에 V5 Learned Orchestrator shadow mode 검토

실제 executor 자동 전환을 먼저 넣지 않는 이유는 잘못된 도메인 분류, 잘못된 모델 선택, child dependency 오류, 비용 폭증과 무한 retry를 운영 데이터 없이 안전하게 검증하기 어렵기 때문입니다.
