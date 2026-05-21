# OMC 커맨드 참조 (Gemini CLI 전용)

> **이 파일은 Gemini CLI 전용입니다.**
> Codex는 `.codex/commands/omc-commands.md`를, Claude Code는 `.claude/commands/*.md`를, Cursor는 자연어를 사용하세요.

이 파일은 **Gemini CLI**에서 OMC 슬래시 커맨드를 사용하는 방법을 안내합니다.
슬래시 커맨드 대신 **아래 지시를 자연어로 말해도** 동일하게 동작합니다.

---

## 코딩 작업 자동 파이프라인 (MANDATORY — 최우선 규칙)

> 슬래시 커맨드를 쓰지 않아도 **모든 구현 요청은 자동으로 파이프라인에 진입**합니다.

아래 중 **하나라도 해당**하면 즉시 CONTRACT 양식을 출력하고 사용자 컨펌을 받습니다.
코드 분석·파일 열기·구현 시작은 컨펌 이후에만 허용됩니다.

| 상황 | 예시 |
|---|---|
| 구현/수정/추가 키워드 | "구현해줘", "추가해줘", "수정해줘", "만들어줘", "feature", "개발" |
| 스킬/방법 질문 | "스킬 뭐 써야해", "어떻게 해야 해", "어떤 방법이 좋아" |
| 기존 파일 수정 요청 | "이 파일에 ~를 넣어줘", "~쪽 바꿔줘", 특정 파일명 + 변경 요청 |
| 버그 수정 요청 | "이거 왜 안 돼", "버그 고쳐줘", "~가 이상해" |

> **"작업이 명확해 보여도 CONTRACT를 건너뛰지 않는다."**
> 명확한 작업일수록 범위·제외·DoD가 중요하다.

**아래 체크포인트 양식을 즉시 출력하고 순서대로 채웁니다.**

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

**불명확하면 질문 1~3개로 좁히고 멈춥니다. 추측으로 진행 금지.**
커밋 시 `pre-commit` 훅, 배포 시 `omc_tdd_check.py` 가 물리적으로 차단합니다.

---

## 요청 수신 시 역할 자동 추천 (MANDATORY)

사용자의 자연어 요청을 받으면 **작업 전에 반드시** 아래 플로우를 따릅니다.

```bash
python3 scripts/omc_role_suggest.py "[요청 텍스트]"
```

스크립트가 없으면 직접 분류:

| 키워드 | 역할 |
|-------|------|
| 버그, error, debug | `analysis` |
| 테스트, test, tdd, spec | `tdd` |
| 리뷰, review, PR, diff | `code_review` |
| 구현, 개발, feature, 추가 | `senior_coding` |
| 배포, deploy, ship | `directive` |
| 문서, docs, 검색 | `search` |

복합: 버그+수정 → `analysis`+`tdd` / 새 기능 → `senior_coding`+`tdd`

출력 포맷:
```
📌 요청 분석: "[요청]"
🤖 추천 역할:
  1. [role_id] 역할 이름 → 설명
────────────────────────
확인하려면: 확인  |  역할 조정: +role_id / -role_id
```

**사용자 확인 전에는 파일 수정·코드 작성·명령 실행을 시작하지 않습니다.**

---

## TDD 규칙 (MANDATORY)

새 파일·함수 추가 시 반드시 테스트를 먼저 작성합니다:

1. **RED**: 실패하는 테스트 먼저 작성 → `omc_pipeline_guard.py red-done` 등록
2. **GREEN**: 테스트를 통과하는 최소 구현
3. **REFACTOR**: GREEN 상태에서 코드 정리

테스트 없는 신규 파일은 "미완성"으로 간주합니다.
예외 허용: `python3 scripts/omc_pipeline_guard.py allow <파일> --reason "이유"` (이유 기록 권장)

---

## /task — TDD 파이프라인 명시 실행

**말하는 방법**: "이 기능 TDD로 만들어줘" / "/task [기능 설명]"

자동 파이프라인과 동일하나 CONTRACT 단계부터 명시적으로 시작:
CONTRACT → RED → GREEN → REFACTOR → TDD GATE → REVIEW

REVIEW 단계에서 반드시 자문하세요:
- 왜 처음부터 제대로 못 했나?
- 어떤 요구사항을 놓쳤나?
- 다음에 추가할 규칙은?

교훈이 있으면: `python3 scripts/omc_lesson.py add -i` (30초)

---

## /review — 코드 리뷰

**말하는 방법**: "현재 변경사항 리뷰해줘" / "git diff 리뷰"

**실행**:
```bash
git status -sb && git diff
```
이후 `code_review` 역할 기준으로:
- 치명 / 중대 / 경미 / 제안 4단계 분류
- 각 이슈에 파일/라인/근거 첨부
- 수정 제안 + 검증 커맨드

---

## /investigate — 원인 추적 (4단계)

**말하는 방법**: "이 버그 원인 추적해줘" / "왜 이렇게 되는지 분석해줘"

**실행**: `analysis` 역할, 4단계 파이프라인
1. **PHASE 1 ROOT CAUSE**: 현상 정량화 (로그/코드 근거만, 추측 금지)
2. **PHASE 2 PATTERN ANALYSIS**: 가설 2~4개 분류 (데이터/로직/상태/환경/인터페이스)
3. **PHASE 3 HYPOTHESIS TESTING**: 한 번에 하나씩 검증, 3번 실패 시 아키텍처 재검토
4. **PHASE 4 IMPLEMENTATION**: 최소 변경 수정 + 교훈 캡처

**철칙**: 근본 원인 확인 전 수정 시작 금지

---

## /brainstorm — 요구사항 탐색

**말하는 방법**: "이거 만들어야 하는데 아직 모호해" / "요구사항 정리 도와줘"

**실행**: 소크라테스식 질문 루프
1. **Phase 1 (What)**: 현상 파악 — 문제/고통/빈도
2. **Phase 2 (Why)**: 원인 탐색 — 왜 지금 해결 안 됐나, 우회 방법은
3. **Phase 3 (How)**: 해결 방향 — 이상적 해결책, 최소 버전, 제약 없다면
4. **Phase 4 (Decide)**: 구현 옵션 A/B/C 제시 → 권장 선택

**규칙**: Phase 4 전에 코드 작성 금지, 최소 3번의 질문-답변 사이클

---

## /office-hours — 제품 사고 먼저

**말하는 방법**: "기능 구현 전에 한 번 정리하자" / "이 기능 정말 맞는 방향인지 체크해줘"

**실행**: 6개 강제 질문 양식 작성
1. 이 기능을 쓸 사람이 정확히 누구인가? (구체적인 한 명)
2. 지금 그 사람이 겪는 실제 고통은? (원하는 기능이 아니라 불편/손실)
3. 성공 기준은? (측정 가능한 지표)
4. 가장 단순한 MVP는?
5. 명시적 비범위는?
6. 10점짜리 버전은?

**완료 후**: 프레이밍 검토 → 대안 3가지 제시 → `.omc/notepad.md`에 결과 기록

---

## /ceo-review — 기능 범위 재검토

**말하는 방법**: "이 기능 범위 CEO 관점에서 체크해줘" / "범위가 맞는지 검토해줘"

**모드**: `EXPAND` / `SELECTIVE` / `HOLD` (기본) / `REDUCE`

**실행**: 10가지 체크리스트
- 이 기능이 없으면 사용자가 이탈하는가?
- 단순한 버전으로 80% 가치를 낼 수 있는가?
- 기술 부채를 만드는가?
- 성공 지표 1개는?
- (+ 6개 추가)

**결론**: `APPROVED` / `EXPAND` / `REDUCE` / `REJECT` + 이유

---

## /plan — 설계·계획

**말하는 방법**: "구현 전에 계획 세워줘" / "요구사항 정리해줘"

**실행**: `analysis` + `senior_coding` 역할
1. 목표 / 범위 / DoD / 제약 확정
2. 태스크마다 `RED(테스트) → GREEN(구현) → VERIFY(커맨드)` 명시
3. 계획 확정 후 `python3 scripts/omc.py state confirm --target .` 으로 세션 기록

---

## /status — 현재 상태

**말하는 방법**: "현재 OMC 상태 알려줘" / "지금 뭐 하고 있었는지 요약해줘"

**실행**:
```bash
python3 scripts/omc.py state status --target .
cat .omc/notepad.md
python3 scripts/omc_pipeline_guard.py status
```

---

## /retro — 주간 회고

**말하는 방법**: "이번 주 회고해줘" / "지난 세션들 분석해줘"

**실행**: `.omc/state/sessions/` 분석 후 회고 포맷 출력
**완료 후**: Compound Engineering 교훈 캡처 단계 포함

---

## /lesson — 교훈 캡처 (Compound Engineering)

**말하는 방법**: "이번 작업 교훈 저장해줘" / "반복 실수 기록해줘"

**목적**: 전역 지침 파일이 아닌 `.omc/lessons/` 에 작고 집중된 파일로 분리 저장

**실행**:
```bash
python3 scripts/omc_lesson.py add -i     # 대화형 추가
python3 scripts/omc_lesson.py list       # 목록
python3 scripts/omc_lesson.py search "키워드"
```

> 세션 시작 시 **BM25 유사도** 기반으로 현재 작업과 관련된 교훈이 AI 컨텍스트에 자동 포함됩니다.

---

## /ship — 배포 준비

**말하는 방법**: "배포 전 체크해줘" / "ship 준비해줘"

**실행**:
1. `python3 scripts/omc_guard.py require --target . --for "ship"` → 미확정 세션 차단
2. `python3 scripts/omc_tdd_check.py --run-tests` → 테스트 없거나 실패 시 차단
3. 타입 체크 → 린트 → 테스트 → 빌드 확인
4. 전부 통과 시에만 배포 진행

**배포 후 — 작업 규모 + 토큰 비용 기록 (MANDATORY)**:

Gemini CLI:
```bash
# 추정 기록 (항상 가능)
OMC_EXECUTOR=gemini python3 scripts/omc_cost.py record --task "[작업 설명]"

# 실측 기록 (gemini --json ... > /tmp/llm_out.json 으로 실행한 경우)
OMC_EXECUTOR=gemini python3 scripts/omc_cost.py record \
  --model gemini-2.5-pro --task "[작업 설명]" --llm-json /tmp/llm_out.json
```

Codex / OpenAI:
```bash
OMC_EXECUTOR=codex python3 scripts/omc_cost.py record \
  --model gpt-4o --task "[작업 설명]" --llm-json /tmp/llm_out.json
```

비용 현황 확인:
```bash
python3 scripts/omc_cost.py report
```

**배포 후 Compound Engineering (30초)**:
- 이번 배포에서 배운 점이 있으면: `python3 scripts/omc_lesson.py add -i`
- 없으면 그냥 넘어갑니다.

---

## /autopilot — 멀티 LLM 자율 루프

구조화된 태스크 파일(.omc/tasks/*.json)로 여러 스텝을 자동 실행합니다.
각 스텝에 `expect` 검증을 설정하면 실패 출력이 다음 retry 프롬프트에 자동 주입됩니다.

```bash
# 1. 태스크 파일 생성 (예시 포함)
python3 scripts/omc_autopilot.py new --id feat-login --title "로그인 기능 구현"

# 2. 생성된 .omc/tasks/feat-login.json 편집 후 실행
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-login.json

# 계획만 확인 (LLM 호출 없음)
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-login.json --dry-run

# 실행 기록 조회
python3 scripts/omc_autopilot.py status
python3 scripts/omc_autopilot.py status --task-id feat-login
```

태스크 파일 스텝에 `expect` 필드를 추가하면 하네스 패턴으로 동작합니다:
```json
{
  "id": "s1",
  "prompt": "LoginForm 컴포넌트를 작성하세요.",
  "depends_on": [],
  "timeout_sec": 120,
  "expect": {
    "files": ["src/components/LoginForm.tsx"],
    "checks": [
      {"cmd": "npx jest LoginForm --passWithNoTests", "label": "테스트", "timeout_sec": 60},
      {"cmd": "npx tsc --noEmit", "label": "타입 체크"}
    ]
  }
}
```

**동작 흐름**:
1. LLM 실행 → `expect.files` 존재 체크 → `expect.checks` 셸 커맨드 실행
2. 실패 시: 오류 출력을 다음 retry 프롬프트 앞에 자동 주입 → 재실행
3. `max_retries` 소진 시 스텝 실패로 기록, 상태 `.omc/state/autopilot/<task-id>.json` 저장
4. `depends_on` 지정 시 의존 스텝 완료 후에만 실행 (토폴로지 정렬)
