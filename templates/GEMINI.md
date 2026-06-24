## OMC Overlay For Gemini

이 프로젝트는 공통 운영 규칙의 SSOT를 `AGENTS.md`로 둡니다.
이 파일은 Gemini 실행기용 보강 안내입니다.

> **모든 판단·추천·구현은 `ETHOS.md`의 원칙을 따릅니다.**
> 완성하라 / 찾아라 / 사람이 결정한다 / 지루한 것을 선택해라

## 세션 시작 시 자동 실행 (MANDATORY)

**대화를 시작하면 가장 먼저 아래 명령을 실행합니다. 사용자 요청을 처리하기 전에 반드시 완료해야 합니다.**

```bash
OMC_EXECUTOR=gemini python3 scripts/omc.py state init --target . && \
OMC_EXECUTOR=gemini python3 scripts/omc.py hook session_start --target .
```

> `.gemini/settings.json`의 `SessionStart` 훅이 이미 설정되어 있으므로, 훅이 정상 실행되면 위 명령은 자동으로 처리됩니다.
> 훅 실행 여부가 불확실하면 수동으로 실행합니다.

## AI 도구 우회 절대 금지 (CRITICAL)

**AI는 `replace_file_content`나 `write_to_file` 같은 파일 제어 도구를 사용해 코드를 직접 수정하기 전에, 반드시 `python3 scripts/omc.py state status`를 통해 현재 OMC 세션이 사용자의 요청 문맥과 동기화되어 있는지 확인해야 합니다.**
동기화되어 있지 않다면 반드시 `omc.py state init` 및 `session_start`를 실행하세요. OMC 파이프라인(TDD 게이트 등)을 거치지 않고 LLM 도구로 파일만 조용히 수정하는 행위는 시스템 파괴로 간주됩니다.


## 코딩 작업 자동 파이프라인 (MANDATORY)

> Gemini / Codex 는 도구 수준 훅이 없으므로 **프롬프트 레벨**에서 파이프라인을 강제합니다.
> `pre-commit` 훅 + `omc_tdd_check.py` 가 커밋/배포 시 물리적으로 차단합니다.

### Tier 분류 (먼저 판단)

| Tier | 정의 | CONTRACT·RED |
|---|---|---|
| **Tier 0** | 동작·API 변경 없음. `*.styled.ts`, `*.constants.ts`, `index.ts`, `*.config.ts`, 오타·정렬 수정 | **생략 가능** — pre-commit도 Tier 0 통과 |
| **Tier 1 (신규 파일)** | 새 파일 생성, 신규 컴포넌트·훅·함수 | **CONTRACT + RED 필수** — pre-commit이 물리적으로 차단 |
| **Tier 1 (기존 파일 수정)** | 동작·API 변경, 로직 수정 | **CONTRACT만 필수** — RED는 대량 수정 시 경고 |

> **판단 기준 한 줄**: "동작·API·새 파일 없으면 Tier 0"

Tier 1 요청(새 파일 생성·동작 변경·기능 추가)에만 아래 체크포인트 양식을 출력하고 사용자 컨펌을 받습니다.

**☐ 체크박스를 채우지 않으면 다음 단계로 진행하지 않습니다.**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 1 ▸ CONTRACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
목표   : _______________________________________________
범위   : 포함 — ________________  /  제외 — ___________
DoD    : _______________________________________________
제약   : _______________________________________________

☐ 사용자 컨펌 완료
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

### 물리적 차단 지점 (Gemini/Codex)

| 단계 | 차단 메커니즘 |
|---|---|
| 커밋 전 | `pre-commit` → `omc_tdd_check.py --staged` → exit 1 |
| ship 전 | `omc_tdd_check.py --run-tests` → exit 1 |
| 컨펌 전 | `omc.py state confirm` → staged TDD 체크 |

> **세션 자동 confirm**: `session_start` 훅에서 `omc_context.py`가 fresh 세션을 자동으로 confirm 처리합니다. 매번 수동 confirm이 불필요합니다. ship/git commit 전 TDD 게이트는 그대로 유지됩니다.

## Compound Engineering (반복 실수 방지)

작업 완료 후 반드시 자문합니다:
- 왜 처음부터 제대로 못 했나?
- 어떤 요구사항을 놓쳤나?
- 다음엔 어떤 규칙이 필요한가?

**교훈은 전역 파일(GEMINI.md)에 추가하지 않습니다. `.omc/lessons/` 에 별도 파일로 저장합니다.**

```bash
python3 scripts/omc_lesson.py add -i              # 교훈 추가 (대화형)
python3 scripts/omc_lesson.py list                # 목록
python3 scripts/omc_lesson.py search "키워드"     # BM25 유사도 검색
```

세션 시작 시 현재 브랜치/커밋을 BM25 쿼리로 사용해 **관련 교훈**을 `.omc/context.md`에 자동 포함합니다.

## Autopilot — 멀티 LLM 자율 루프 (옵트인)

구조화된 태스크 파일로 여러 스텝을 순차 실행합니다. `expect` 검증 실패 시 오류 출력이 다음 retry 프롬프트에 자동 주입됩니다.

```bash
# 태스크 파일 생성
python3 scripts/omc_autopilot.py new --id my-feature --title "기능 구현"

# 실행 (실제 LLM)
python3 scripts/omc.py autopilot --task-file .omc/tasks/my-feature.json

# 계획 확인 (LLM 호출 없음)
python3 scripts/omc.py autopilot --task-file .omc/tasks/my-feature.json --dry-run

# 실행 기록 조회
python3 scripts/omc_autopilot.py status
```

태스크 스텝에 `expect` 검증을 추가하면 하네스 패턴으로 동작합니다:
```json
"expect": {
  "files": ["src/Login.tsx"],
  "checks": [
    {"cmd": "npx jest Login --passWithNoTests", "label": "테스트"},
    {"cmd": "npx tsc --noEmit", "label": "타입 체크"}
  ]
}
```

> **Compound Engineering 인라인 프롬프트**: 코딩 파이프라인 완료 후 아래 3가지를 자문하세요.
> 1. 왜 처음부터 제대로 못 했나?
> 2. 어떤 요구사항을 놓쳤나?
> 3. 다음에 추가할 규칙은?
> 교훈이 있으면 `python3 scripts/omc_lesson.py add -i` 로 기록합니다 (30초).

## 슬래시 커맨드

자연어 요청 대신 아래 커맨드를 직접 사용할 수 있습니다.

### 코어 커맨드 (매일 쓰는 것)

| 커맨드 | 설명 |
|--------|------|
| `/plan [작업]` | 구현 전 TDD 태스크 분해 |
| `/task [설명]` | 7단계 TDD 파이프라인 진입 |
| `/review [파일/설명]` | `git diff` 기준 코드 리뷰 |
| `/investigate [이슈]` | 4단계 방법론으로 버그 근본 원인 추적 |
| `/lesson [키워드]` | `.omc/lessons/` 에서 BM25 유사도 검색 |
| `/status` | OMC 상태 확인 |
| `/qa [변경]` | 구현 후 수동 QA 체크리스트 생성 |
| `/ship [대상]` | TDD 게이트 → 배포 |

### 선택 커맨드 (필요할 때만)

| 커맨드 | 설명 |
|--------|------|
| `/brainstorm [주제]` | 요구사항이 아직 모호할 때 — 소크라테스식 탐색 |
| `/office-hours [요청]` | 기능 방향이 맞는지 의심될 때 — 6개 강제 질문 |
| `/ceo-review [모드]` | 기능 범위 재검토 — CEO 관점 10가지 체크 |
| `/autopilot [태스크파일]` | 구조화된 태스크 파일 기반 자율 루프 |
| `/retro` | 회고 + Compound Engineering 교훈 캡처 |

권장 순서 (코어만): `/plan` → `/task` → `/review` → `/ship`

커맨드 파일 위치: `.gemini/commands/omc-commands.md` (자연어로 동일 동작)

## 역할 핸드오프 프로토콜

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

- 역할 전환 없이 다음 단계로 넘어가지 않습니다.
- 여러 역할을 동시에 활성화하면 결과물이 모호해집니다.

## 역할 자동 추천 (MANDATORY)

사용자의 자연어 요청을 받으면 **작업 전에 반드시** 역할을 추천하고 컨펌을 받습니다.

```bash
python3 scripts/omc_role_suggest.py "[요청 텍스트]"
```

스크립트가 없으면 `AGENTS.md`의 "인라인 분석 규칙"에 따라 직접 분류합니다.

컨펌 포맷:
```
📌 요청 분석: "[요청]"
🤖 추천 역할:
  1. [role_id] 역할 이름 → 설명
  2. [role_id] 역할 이름 → 설명
────────────────────────────────
확인하려면: 확인  |  역할 조정: +role_id / -role_id
```

- 사용자가 `확인`, `+추가,-삭제`, `a,b,c` 형태로 응답하기 전에는 파일 수정/코드 작성/명령 실행을 시작하지 않습니다.

## TDD 규칙 (Tier 1 대상)

> **Tier 0 파일** (`*.styled.ts`, `*.constants.ts`, `index.ts`, `*.config.ts`, `*.d.ts` 등)은 TDD 불필요.
> **Tier 1 파일** (새 컴포넌트·훅·API·동작 변경)에만 아래 규칙이 적용됩니다.

새 Tier 1 파일 추가 시 **반드시** 테스트를 먼저 작성합니다:
1. **RED**: 실패하는 테스트 먼저 작성 → `omc_pipeline_guard.py red-done <파일>` 등록
2. **GREEN**: 테스트를 통과하는 최소 구현
3. **REFACTOR**: GREEN 상태에서 코드 정리

- 테스트 없는 신규 Tier 1 파일(`.ts`, `.tsx`, `.py`)은 "미완성"으로 간주
- 버그 수정 시: 재현 테스트 먼저 → 수정 → GREEN 확인
- 예외(사용자 명시 승인 필요): 타입 정의, 설정 파일, 빈 index 파일
- 예외 허용: `python3 scripts/omc_pipeline_guard.py allow <파일> --reason "이유"` (이유 기록 권장)

## 핵심 규칙

- 기본 실행 경로는 `python scripts/omc.py "작업 요청"`이며, 상태 변경 작업은 OMC 경로를 우선 사용합니다.
- 사용자에게 보이는 안내와 진행 보고는 기본 한글로 작성합니다.

## 빠른 사용

```bash
python scripts/omc.py setup --target .
python scripts/omc.py "작업 요청"
OMC_EXECUTOR=gemini python scripts/omc.py state status
python scripts/omc_pipeline_guard.py status    # 파이프라인 상태 확인

# autopilot — 구조화된 자율 루프
python3 scripts/omc_autopilot.py new --id feat-x --title "X 기능"   # 태스크 파일 생성
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json  # 실행
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json --dry-run  # 계획 확인
python3 scripts/omc_autopilot.py status                               # 기록 조회

# 배포 후 토큰 비용 기록 (Gemini)
OMC_EXECUTOR=gemini python3 scripts/omc_cost.py record --model gemini-2.5-pro --task "작업명"
# JSON 실측 포함: gemini --json ... > /tmp/llm_out.json 후
OMC_EXECUTOR=gemini python3 scripts/omc_cost.py record --model gemini-2.5-pro --task "작업명" --llm-json /tmp/llm_out.json
python3 scripts/omc_cost.py report   # 전체 비용 현황
```

---

## ⛔ 스킬 완료 후 자동 진행 금지 (MANDATORY)

스킬이 완료되면 판정·결과를 출력한 뒤 다음 스킬로 자동 진입하지 않는다.
사용자가 "진행하자", "계속해", "응" 같은 짧은 승인을 해도
**명시적으로 다음 스킬 이름이 언급되지 않으면 자동 진입 금지.**

상세 규칙 및 금지 동작 목록: **AGENTS.md** `## ⛔ 스킬 완료 후 자동 진행 금지` 참조
