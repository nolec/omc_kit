# OMC - Orchestrated Multi-agent Craft

TDD 게이트와 telemetry를 갖춘 멀티 LLM 오케스트레이션 킷입니다. Codex, Claude Code, Gemini CLI, Cursor에서 같은 프로젝트 규칙과 작업 흐름을 사용할 수 있습니다.

## 현재 상태

2026-08-28 기준 OMC는 **승인 범위 안에서 제한 병렬 실행을 수행하는 bounded orchestration** 단계입니다.

- V1 스킬 기반 라우팅: 완료
- V2 단계별 모델 라우팅: 완료
- V3 실패 감지·재시도·승격: 완료
- V4 telemetry·비용·KPI·observed 검증: 완료
- V5 bounded N-child scheduler·provider adapter: 부분 반영
- Operator Experience: 진행중
- 승인된 v2 grant의 single child·exact 2-child·bounded N-child 실행: 구현 완료
- 실제 3–5 child 운영 acceptance·실패 자동 재분배·자동 모델 전환·자동 ship: 미완료

실행 가능 여부는 승인 grant, scope, dependency, budget과 provider capability에 따라 fail-close 판정됩니다. 실행 코드가 있다는 사실만으로 제품 효과가 검증된 것은 아니며, 실제 3–5 child 작업의 single-agent baseline 비교는 아직 진행 중입니다.

현재 첫 제품 범위는 복잡한 코드 변경을 `task → review`로 안전하게 완료하는 흐름입니다. 실제 자연 발생 implementation 작업 3건을 최소 2개 저장소에서 최대 7일 동안 OMC/Baseline 동일 조건으로 비교하며, 완료율·wall-clock 시간·사용자 개입·재작업만 primary metric으로 사용합니다. 이 gate가 끝날 때까지 Plan·Review 대체 검증과 다른 연구 lane은 `PAUSED_NOT_CANCELLED`입니다.

Product Value 결과는 두 판정을 분리합니다.

- **운영 대체 판정**: no-key `subscription_bounded` 경로에서도 성공률·시간·token·개입·안전 위반을 비교해 `OPERATIONALLY_REPLACEABLE` 또는 `NOT_REPLACEABLE`로 종료할 수 있습니다.
- **strict hard-token 인증**: exact input count와 native output cap을 증명하는 `provider_enforced` transport만 `STRICTLY_CERTIFIED`가 될 수 있습니다. 운영 기준을 통과했지만 이 capability가 없으면 `HOLD_TRANSPORT`이며, 운영 대체 판정을 무효화하지 않습니다.

두 판정의 claim scope는 `bounded_n_child_execution`으로 제한됩니다. 과거 6건 corpus는 원문 evidence가 소실되어 유효한 development evidence로 승계하지 않으며, 이 결과만으로 Plan, Review 또는 전체 OMC가 기준 제품을 대체한다고 주장하지 않습니다. 외부 운영 대체 주장은 구현과 판정 기준을 고정한 뒤 별도로 선정한 disjoint holdout에서 재현됐을 때만 허용합니다.

상세 상태와 남은 작업은 [자동 모델 라우팅 로드맵](docs/automatic_model_routing_roadmap.md)을 참고하세요.

## Target Architecture — Not Implemented

OMC의 제품 방향은 여러 레포를 운영하는 SaaS 창업자가 새 제품 기능을 요구사항·검증·리뷰 기준까지 완료하도록 돕는 것이다. 제안된 **persona-guided Codex Pilot v3**는 `PROPOSAL — decision required`이며, OMC가 persona·DoD·검증 계약을 정하고 Codex가 실행한 뒤 OMC가 선언된 검증과 종료 판정을 제공하는 구조를 목표로 한다.

Codex adapter는 아직 구현되지 않았다. 따라서 이것은 현재 제공 기능이나 Pilot v2의 결과가 아니며, 기존 v2의 T0·roster·inventory·readiness·receipt를 바꾸지 않는다. v3는 별도 adapter contract, 승인된 범위, 실제 사용자 효용 측정이 확정된 뒤에만 시작할 수 있다.

## 일반 사용 경로

일반 개발 작업은 내부 benchmark 명령을 직접 조합하지 않고 아래 흐름으로 사용합니다.

1. `python3 scripts/omc.py setup --target /path/to/project`으로 설치합니다.
2. 구현은 Codex의 `$omc-task`, Claude Code·Gemini CLI의 `/task`, Cursor의 자연어 요청으로 시작합니다.
3. `python3 scripts/omc.py state status --target .`로 현재 세션과 실행 상태를 확인합니다.
4. 변경 검토는 `$omc-review`, 배포 준비는 `$omc-ship`을 사용합니다.

`product-value-*`, `execute-n-child`, acceptance·evidence 명령은 OMC 자체를 검증하는 내부 research 명령입니다. 기존 자동화와 호환되도록 직접 호출은 유지하지만 루트 `--help`의 일반 사용 목록에는 노출하지 않습니다.
이 research 실행은 일반 개발 작업의 완료율·사용자 마찰 관찰 표본에 포함하지 않습니다.

## 주요 기능

- **공통 작업 흐름**: plan, task, critique, review, ship, status, reentry 등 역할별 스킬 제공
- **TDD 게이트**: CONTRACT -> RED -> GREEN -> REFACTOR -> TDD GATE 흐름 관리
- **멀티 LLM 라우팅**: 요청 난이도·위험도·정책 profile에 따른 모델 강도와 executor 후보 추천
- **실패 대응**: retry, plan retry, timeout, critique/review 실패, reroute 경로를 decision engine으로 정리
- **운영 telemetry**: token, cost, retry, reroute, 성공률, multi-run KPI를 `.omc/runs`에 기록
- **observed evidence**: executor별 capability evidence를 fresh/stale·환경·품질 상태와 함께 집계
- **제한 병렬 handoff**: 승인된 v2 grant 안에서 parent-child scope, dependency, cycle, budget을 검증하고 bounded N-child scheduler로 실행
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

OMC 릴리스 버전은 source kit의 `VERSION`을 기준으로 하며 target 프로젝트의
루트에는 복사하지 않습니다. 설치된 버전과 원본 최신성은 다음 명령으로 확인합니다.

```bash
python3 scripts/omc.py version --target .
python3 scripts/omc.py version --target . --json
```

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

### Review 비교 결과 수집

Codex와 `omc-review` 실행 결과를 비교 샘플로 기록할 때는 `run_review_in_snapshot()`에
`envelope_context`를 명시적으로 전달합니다. context에는 `provider`, `case_id`, `diff_id`,
`prompt_id`, `status`, `execution_mode`가 필요하며, snapshot 사용 여부와 workspace mutation은
실행 metadata로 자동 기록됩니다. 이 경로는 결과 identity를 추론하지 않고, 실패 시에도
`status=failed` evidence를 보존합니다.

`envelope_context`를 생략한 legacy 호출은 `{result, execution_metadata}`를 반환하므로 기존
호출과의 하위 호환에는 사용할 수 있지만, 신규 comparison 수집에는 사용하지 않습니다.

`execution_metrics`는 실행 결과의 핵심 telemetry 요약입니다.

- `duration_ms`: 실행 전체 또는 최종 측정 구간의 소요 시간(ms)입니다. 완료된 실행은 실제 경과 시간을, 아직 끝나지 않은 실행은 `null`을 사용합니다.
- `mode`: 파이프라인 실행 모드입니다. 현재는 `lite`와 `full`을 사용하며, 어떤 경로가 수행됐는지 판별하는 운영 기본값입니다.
- `skill_path`: 해당 실행에서 실제로 밟은 스킬 경로입니다. 예를 들어 `["omc-task", "omc-review"]`처럼 단계별 스킬 순서를 기록합니다.

```python
from omc_review_orchestration import run_review_in_snapshot

envelope = run_review_in_snapshot(
    ".",
    review_callback,
    envelope_context={
        "provider": "codex",
        "case_id": "case-1",
        "diff_id": "diff-1",
        "prompt_id": "review-v1",
        "status": "completed",
        "execution_mode": "cli_completed",
    },
)
```

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
| `scripts/omc_version.py` | OMC 버전·원본 변경·설치 무결성 판정 |
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

빠른 회귀 테스트 (느린 health 실행 제외):

```bash
python3 -m pytest scripts -q -m "not slow"
```

느린 health 테스트만 별도 실행:

```bash
python3 -m pytest scripts/test_omc_health.py -q -m slow
```

커밋 전 staged TDD gate:

```bash
python3 scripts/omc_tdd_check.py --staged
```

## 로드맵

현재 우선순위는 다음 순서입니다.

1. 실제 자연 발생 implementation 작업 3건을 최소 2개 저장소에서 chronological first eligible 순서로 수집해 readiness 발행
2. readiness 뒤에만 동일 조건의 OMC/Baseline arm을 실행하고, 완료율·wall-clock 시간·사용자 개입·재작업을 비교해 최대 7일 안에 `CONTINUE`, `REDUCE`, `STOP` 판정
3. `CONTINUE`인 경우에만 사용자가 `PAUSED_NOT_CANCELLED` backlog 중 다음 lane 하나를 선택

위 acceptance가 끝날 때까지 Product Value corpus, bounded N-child, Plan Batch B, native Review, Work Packet, Decision Policy를 실행하거나 새 schema·transport·benchmark fixture를 추가하지 않습니다. strict hard-token 인증도 사용자가 해당 lane을 별도로 재개한 경우에만 검토합니다.
