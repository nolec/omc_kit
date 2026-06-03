## Engineering Ethos (필독 — 모든 판단에 적용)

**모든 추천·구현·리뷰는 `ETHOS.md`의 원칙을 따릅니다.**
핵심 4원칙: **완성하라 / 찾아라 / 사람이 결정한다 / 지루한 것을 선택해라**
세부 내용: `ETHOS.md` 참조.

---

## OMC — Orchestrated Multi-agent Craft

이 레포에는 공통 멀티비서 킷이 `omc_kit/`에 포함되어 있습니다.

## 세션 시작 시 자동 실행 (MANDATORY)

**모든 AI 에이전트는 대화를 시작하면 가장 먼저 아래 명령을 실행해야 합니다. 사용자 요청을 처리하기 전에 반드시 완료해야 합니다.**

```bash
OMC_EXECUTOR=codex python3 scripts/omc.py state init --target . && \
OMC_EXECUTOR=codex python3 scripts/omc.py hook session_start --target .
```

> Cursor는 `.cursor/hooks.json`, Claude Code는 `.claude/settings.json`, Gemini CLI는 `.gemini/settings.json`, Codex는 `.codex/hooks.json`의 `SessionStart` 훅이 이를 자동 처리합니다.
> 훅이 실행되지 않는 환경에서는 AI가 직접 위 명령을 실행합니다.

## AI 도구 우회 절대 금지 (CRITICAL)

**모든 AI는 `replace_file_content`나 `write_to_file` 등 파일 제어 도구를 사용해 코드를 직접 수정하기 전에, 반드시 `python3 scripts/omc.py state status`를 통해 현재 OMC 세션이 사용자의 요청 문맥과 동기화되어 있는지 확인해야 합니다.**
동기화되어 있지 않다면 반드시 `omc.py state init` 및 `session_start`를 실행하세요. OMC 파이프라인(TDD 게이트 등)을 거치지 않고 LLM 도구로 파일만 조용히 수정하는 행위는 시스템 파괴로 간주됩니다.


## 코딩 작업 자동 파이프라인 (MANDATORY — 모든 LLM 공통)

> **Tier 1 구현 요청은 자동으로 파이프라인에 진입**합니다. Tier 0는 생략 가능.

### Tier 분류 (먼저 판단)

| Tier | 정의 | CONTRACT·RED |
|---|---|---|
| **Tier 0** | 동작·API 변경 없음. `*.styled.ts`, `*.constants.ts`, `index.ts`, `*.config.ts`, 오타·정렬 수정 | **생략 가능** — 훅이 차단하지 않음 |
| **Tier 1 (신규 파일)** | 새 파일 생성, 신규 컴포넌트·훅·함수 | **CONTRACT + RED 필수** — 훅이 물리적으로 차단 |
| **Tier 1 (기존 파일 수정)** | 동작·API 변경, 로직 수정 | **CONTRACT만 필수** — RED는 대량 수정 시 경고 |

> **판단 기준 한 줄**: "동작·API·새 파일 없으면 Tier 0"

### Tier 1 트리거 — 해당 시 즉시 CONTRACT 양식 출력

| 상황 | 예시 |
|---|---|
| 새 파일 생성 | 신규 컴포넌트, 훅, 유틸, API 함수 |
| 동작/API 변경 | 기존 함수 시그니처 변경, 새 API 연동 |
| 기능 추가 | "이 기능 추가해줘", "feature", "구현해줘" |
| 버그 수정 (동작 변경 수반) | 재현 테스트 필요한 버그 |

> Tier 1은 CONTRACT를 건너뛰면 훅이 막음. Tier 0은 훅이 차단하지 않음.

**Tier 1 요청일 때만 아래 체크포인트를 출력합니다. Tier 0는 이 절을 생략합니다.**

**☐ 체크박스를 채우지 않으면 다음 단계로 진행하지 않습니다.**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 1 ▸ CONTRACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
목표   : _______________________________________________
범위   : 포함 — ________________  /  제외 — ___________
DoD    : _______________________________________________
제약   : _______________________________________________
적용 스킬 : ☐ omc-plan  ☐ omc-task  ☐ omc-investigate
            ☐ omc-review  ☐ omc-brainstorm  ☐ 해당 없음

☐ 사용자 컨펌 완료
  python3 scripts/omc_pipeline_guard.py contract-done
☐ CONTRACT 등록 완료
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    ↓ PHASE 1 완료 후 작성
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 3 ▸ RED  🔴  (구현 파일 생성 전 반드시)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
테스트 파일   : ________________________________________
테스트 케이스 : ________________________________________
실행 커맨드   : ________________________________________

실패 출력 (실제 터미널 출력을 아래 블록에 붙여넣으세요 — 임의 작성 금지):
\`\`\`
[FAIL 출력 첨부]
\`\`\`

☐ 실제 FAIL 출력 첨부 완료

  python3 scripts/omc_pipeline_guard.py red-done <테스트파일>

☐ RED 등록 완료
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    ↓ PHASE 3 완료 후 작성
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 4 ▸ GREEN  🟢
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
구현 파일  : ________________________________________
핵심 변경  : ________________________________________

☐ 테스트 PASS 확인  ☐ 기존 테스트 회귀 없음
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    ↓ PHASE 4 완료 후 작성
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 5 ▸ REFACTOR + TDD GATE  🔵
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
정리 항목 : ________________________________________

  python3 scripts/omc_tdd_check.py --staged

왜 처음부터 제대로 못 했나? (선택, 교훈 있으면 기록):
  python3 scripts/omc_lesson.py add -i

☐ 리팩토링 후 PASS 유지  ☐ TDD 게이트 통과
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### LLM별 차단 지점

| LLM | 파일 생성 차단 | 커밋 차단 | 배포 차단 |
|---|---|---|---|
| Cursor | `beforeToolCall` → `omc_pipeline_guard.py` | pre-commit hook | `omc_tdd_check.py` |
| Claude Code | `PreToolUse` → `omc-pipeline-check.sh` | pre-commit hook | `omc_tdd_check.py` |
| Codex | `PreToolUse` (matcher: Bash\|apply_patch\|Write) → exit 2 / 파일편집 **1차 미발화 2026-06-04, 재검증 필요** | pre-commit hook | `omc_tdd_check.py` |
| Gemini | 프롬프트 양식 (물리 차단: pre-commit) | pre-commit hook | `omc_tdd_check.py` |

자연어 요청 대신 아래 커맨드를 직접 사용할 수 있습니다.

> **LLM마다 호출 방식이 다릅니다. 자신의 IDE에 맞는 방식만 사용하세요.**

### Claude Code / Gemini CLI — 슬래시 커맨드

| 커맨드 | 역할 | 설명 |
|--------|------|------|
| `/plan [작업]` | analysis + senior_coding | 구현 전 TDD 태스크 분해 |
| `/task [설명]` | senior_coding | 7단계 TDD 파이프라인 |
| `/review [파일/설명]` | code_review + senior_coding | `git diff` 기준 코드 리뷰 |
| `/investigate [이슈]` | analysis | 4단계 방법론으로 버그 근본 원인 추적 |
| `/lesson [키워드]` | — | `.omc/lessons/` BM25 관련 교훈 검색·출력 |
| `/status` | — | 현재 OMC 세션 상태 요약 |
| `/ship [대상]` | — | OMC 가드 + 타입/린트/테스트/빌드 확인 후 배포 |
| `/brainstorm [주제]` | — | 요구사항이 아직 모호할 때 — 소크라테스식 탐색 |
| `/benchmark [기능]` | — | 세계 1등 제품과 비교해 갭·차별화·다음 액션 도출 |
| `/office-hours [요청]` | — | 기능 방향이 맞는지 의심될 때 — 6개 강제 질문 |
| `/ceo-review [모드]` | — | 기능 범위 재검토 — CEO 관점 10가지 체크 |
| `/critique [계획/코드]` | — | 계획·코드에 대한 냉정한 Pre-mortem 비판 리뷰 |
| `/retro [기간]` | — | 주기적 회고 — 세션 히스토리 분석 |

### Cursor — 자연어 (슬래시 커맨드 없음)

Cursor는 `.cursor/rules/`에 규칙이 항상 적용됩니다. **슬래시 커맨드 없이** 자연어로 요청하면 됩니다.

```
"이 기능 계획 잡아줘"   →  plan 흐름 자동 진입
"버튼 컴포넌트 만들어줘" →  task 흐름 자동 진입
"코드 리뷰해줘"          →  review 흐름 자동 진입
```

### Codex — Agent Skills (`$` 접두사 또는 자연어)

```
$omc-plan 로그인 기능 구현
$omc-task 버튼 컴포넌트 추가
$omc-review
```

또는 "계획해줘", "태스크 나눠줘" 등 자연어 키워드로도 자동 트리거됩니다.

권장 순서: `plan` → `task` → `review` → `ship`

- **Claude Code**: `.claude/commands/*.md` — `/plan` 형식
- **Cursor**: `.cursor/rules/` — 자연어 자동 적용
- **Codex**: `.agents/skills/omc-*/` — `$omc-plan` 또는 자연어
- **Gemini CLI**: `.gemini/commands/` — `/plan` 형식

---

## 스프린트 파이프라인 (Plan → Build → Review → Ship → Reflect)

OMC 작업의 표준 흐름입니다. **단계를 건너뛰지 않습니다.**

```
[PLAN]          [BUILD]         [REVIEW]        [SHIP]          [REFLECT]
/plan      →    /task      →    /review    →    /ship      →    /retro
태스크 분해      7단계 TDD       diff 리뷰        가드+배포        교훈 축적
DoD 확정         RED→GREEN       버그·리스크      TDD 게이트       BM25 자동주입
```

필요할 때만 앞에 붙이는 선택 단계:
- 요구사항이 모호하면 → `/brainstorm` 먼저
- 기능 방향이 맞는지 의심되면 → `/office-hours`
- 버그 원인이 불명확하면 → `/investigate`

각 단계별 핵심 원칙:
- **PLAN**: 범위와 DoD 확정 전 구현 시작 금지
- **BUILD**: 테스트 없는 구현 파일 생성 금지 (pipeline_guard 물리적 차단)
- **REVIEW**: 치명/중대 이슈 없음 확인 전 ship 금지
- **SHIP**: 미확정 세션이면 차단
- **REFLECT**: 30초라도 교훈 기록 — 복리 효과

역할 완료 시점에 **다음 역할로 명시적 전환**합니다.

```
─── 핸드오프 ───────────────────────────────────────────────
[현재 역할: analysis] 완료
  발견 사항: _______________________________________________
  결정 사항: _______________________________________________

→ 다음 역할: senior_coding
  이유: 분석 완료, 구현 단계 진입
  인계 사항: _______________________________________________
────────────────────────────────────────────────────────────
```

표준 전환 경로:

| 작업 유형 | 시작 | → | → | 종료 |
|---|---|---|---|---|
| 버그 수정 | `analysis` | `senior_coding` | `code_review` | 완료 |
| 신규 기능 | `analysis` | `senior_coding`+`tdd` | `code_review` | 완료 |
| 리팩터링 | `code_review` | `senior_coding` | `code_review` | 완료 |
| 배포 | `code_review` | `directive` | — | 완료 |

**전환 없이 다음 단계로 넘어가지 않습니다.**

## 역할 자동 추천 프로토콜 (MANDATORY — Cursor·Claude·Gemini·Codex 공통)

**모든 AI는 사용자의 자연어 요청을 받으면 아래 플로우를 반드시 따릅니다.**

### 플로우

```
1. 요청 수신
   ↓
2. 역할 자동 추천 (스크립트 또는 인라인 분석)
   python3 scripts/omc_role_suggest.py "[요청 텍스트]"
   ↓
3. 추천 역할 출력 + 컨펌 요청
   예:
   📌 요청 분석: "버그 수정하고 테스트 추가"
   🤖 추천 역할:
     1. [analysis] 분석 비서 → 버그 근본 원인 분석
     2. [tdd] TDD 비서 → RED→GREEN→REFACTOR 사이클 안내
   ─────────────────────────
   확인하려면: 확인
   역할 조정:  +senior_coding  또는  analysis,tdd
   ↓
4. 사용자 응답 처리
   - "확인" → 추천 역할 그대로 사용
   - "+role_id" → 역할 추가
   - "-role_id" → 역할 제거
   - "a,b,c" 형식 → 해당 역할로 교체
   ↓
5. 확정된 역할로 OMC 세션 시작 → 작업 실행
```

### 스크립트가 없는 환경에서의 인라인 분석 규칙

`scripts/omc_role_suggest.py`가 없으면 아래 규칙으로 직접 분류합니다:

| 요청 키워드 | 추천 역할 |
|------------|----------|
| 버그, 에러, error, debug | `analysis` |
| 테스트, test, tdd, spec | `tdd` |
| 리뷰, review, PR, diff | `code_review` |
| 구현, 개발, feature, 추가 | `senior_coding` |
| 배포, deploy, ship, 설치 | `directive` |
| 문서, docs, 검색, search | `search` |

복합 패턴:
- 버그 수정 + 테스트 → `analysis` + `tdd`
- 새 기능 → `senior_coding` + `tdd` (TDD 항상 권장)
- 리팩터링 → `senior_coding` + `code_review`

### 주의사항
- **컨펌 없이 코드 수정·명령 실행·파일 생성을 시작하지 않습니다.**
- 사용자가 단순 조회(상태, 도움말)를 요청하면 역할 추천 생략 가능.
- 슬래시 커맨드(`/review`, `/plan` 등) 사용 시 역할이 고정되므로 추천 단계 생략.

---

## TDD 규칙 (MANDATORY — 모든 LLM 공통)

새 파일·함수 추가 시 **반드시** 테스트를 먼저 작성합니다:
1. **RED**: 실패하는 테스트 먼저 작성
2. **GREEN**: 테스트를 통과하는 최소 구현
3. **REFACTOR**: GREEN 상태에서 코드 정리

- 테스트 없는 신규 파일(`.ts`, `.tsx`, `.py`)은 "미완성"으로 간주
- 버그 수정 시: 재현 테스트 먼저 → 수정 → GREEN 확인
- `/ship` 실행 시: 신규 파일에 테스트 없으면 자동 차단
- 예외(사용자 명시 승인 필요): 타입 정의(`*.d.ts`), 설정 파일(`*.config.ts`), 빈 index 파일
  - 예외 허용: `python3 scripts/omc_pipeline_guard.py allow <파일> --reason "이유"` (이유 기록 권장, `.omc/allow_log.jsonl` 에 감사 로그 저장)

---

- 사용자에게 보이는 안내/컨펌/보고는 기본 한글로 작성합니다(사용자가 영어를 명시하면 예외).
- 프로젝트 전용 오버레이는 `project_prompts/team.local.json`이 있으면 함께 반영합니다.
- 기본 진입점은 `python scripts/omc.py "요청"`입니다.
- `.omc/` 상태는 `scripts/omc.py state init|status|compact`로 관리합니다.
- 기본 정책은 `.omc/policy.json`의 `enforce_confirm=true`이며, `scripts/omc_guard.py`가 최신 미확정 세션을 차단합니다.
- **세션 자동 confirm**: `session_start` 훅에서 `omc_context.py`가 fresh 세션을 자동으로 confirm 처리합니다. 매번 수동 confirm이 불필요합니다. ship/git commit 전 TDD 게이트는 그대로 유지됩니다.
- 라이프사이클 훅은 `scripts/omc.py hook session_start|session_end|pre_compact|post_compact`를 사용합니다.
- 모드는 `autopilot`, `team`, `ulw`, `ralph`, `deep-interview`를 요청 의도에 맞게 선택합니다.

---

## Compound Engineering (반복 실수 방지)

작업 완료 후 반드시 자문합니다:
- 왜 처음부터 제대로 못 했나?
- 어떤 요구사항을 놓쳤나?
- 다음엔 어떤 규칙이 필요한가?

**교훈은 전역 파일(AGENTS.md)에 추가하지 않습니다. `.omc/lessons/` 에 별도 파일로 저장합니다.**

```bash
python3 scripts/omc_lesson.py add -i              # 교훈 추가 (대화형)
python3 scripts/omc_lesson.py list                # 목록
python3 scripts/omc_lesson.py search "키워드"     # BM25 유사도 검색
python3 scripts/omc_lesson.py search "키워드" --top 3
```

세션 시작 시 `omc_context.py`가 현재 브랜치/커밋을 BM25 쿼리로 사용해 **관련 교훈**을 자동 주입합니다.
`/retro` 커맨드에 교훈 캡처 단계가 포함됩니다.

## Autopilot — 멀티 LLM 자율 루프 (옵트인)

구조화된 태스크 파일로 여러 스텝을 자동 실행합니다. 각 스텝에 `expect` 검증을 설정하면 실패 시 컨텍스트를 다음 프롬프트에 자동 주입합니다.

```bash
# 태스크 파일 생성 (예시 포함)
python3 scripts/omc_autopilot.py new --id my-feature --title "기능 구현"

# 실행 (실제 LLM 호출)
python3 scripts/omc.py autopilot --task-file .omc/tasks/my-feature.json

# 계획만 확인 (LLM 호출 없음)
python3 scripts/omc.py autopilot --task-file .omc/tasks/my-feature.json --dry-run

# 실행 기록 조회
python3 scripts/omc_autopilot.py status
```

태스크 파일 포맷 (`.omc/tasks/<name>.json`):
```json
{
  "id": "feat-login",
  "title": "로그인 기능 구현",
  "executor": "auto",
  "max_retries": 1,
  "steps": [
    {
      "id": "s1",
      "prompt": "LLM에 전달할 프롬프트",
      "depends_on": [],
      "timeout_sec": 120,
      "expect": {
        "files": ["src/Login.tsx"],
        "checks": [
          {"cmd": "npx jest Login --passWithNoTests", "label": "테스트", "timeout_sec": 60},
          {"cmd": "npx tsc --noEmit", "label": "타입 체크"}
        ]
      }
    }
  ]
}
```

- `expect.files`: 스텝 완료 후 존재해야 할 파일 목록
- `expect.checks`: 통과해야 할 셸 커맨드 목록
- 실패 시 오류 출력이 다음 retry 프롬프트 앞에 자동 주입됨

> **Compound Engineering 인라인 프롬프트**: 코딩 파이프라인 완료 후 아래 3가지를 자문하세요.
> 1. 왜 처음부터 제대로 못 했나?
> 2. 어떤 요구사항을 놓쳤나?
> 3. 다음에 추가할 규칙은?
> 교훈이 있으면 `python3 scripts/omc_lesson.py add -i` 로 기록합니다 (30초).

---

## ⛔ 스킬 완료 후 자동 진행 금지 (MANDATORY — 모든 LLM 공통)

**AI는 아래 상황에서 반드시 멈추고 사용자의 다음 명령을 기다린다.**

스킬이 완료되면 판정·결과를 출력한 뒤 다음 스킬로 자동 진입하지 않는다.
사용자가 "진행하자", "계속해", "응" 같은 짧은 승인을 해도
**명시적으로 다음 스킬 이름이 언급되지 않으면 자동 진입 금지.**

| 완료된 스킬 | 금지 동작 |
|---|---|
| omc-office-hours | PROCEED 판정 후 자동으로 omc-plan 실행 금지 |
| omc-plan | Phase 완료 후 자동으로 omc-task 실행 금지 |
| omc-critique | 판정 후 자동으로 omc-plan/omc-task 실행 금지 |
| omc-benchmark | 분석 후 자동으로 omc-office-hours/omc-plan 실행 금지 |
| omc-brainstorm | 결론 후 자동으로 omc-plan 실행 금지 |
| omc-task | 완료 후 자동으로 omc-review/omc-ship 실행 금지 |

**올바른 동작:**
> "PROCEED 판정입니다. 다음으로 `/omc-plan` 진행할까요?"
> → 여기서 멈추고 사용자 응답을 기다린다.

**금지 동작:**
> "PROCEED 판정입니다. 바로 플랜을 작성하겠습니다. [plan 내용 시작]..."
