# OMC 상세 운영 참조

일반 재설치는 OMC 관리 블록을 갱신하고 기존 스킬·참조 파일을 보존합니다. 이전 계획 스킬이 가리키는 Tier 1 요약은 공통 지침에 있습니다.

## 명시적으로 선택한 OMC 작업

이 절은 OMC 작업 흐름을 직접 선택했을 때 적용합니다. 신규 작업 준비와 기존 작업 이어가기는 `AGENTS.md`의 진입 기준을 따릅니다.

### OMC 구현 작업의 수정 전 동기화

**진입한 OMC 구현 작업에서 파일을 수정하기 전에는 `python3 scripts/omc.py state status`로 OMC 세션이 요청과 연결되어 있는지 확인합니다.**
Codex에서 동기화되지 않았다면 원본 초기화 대신 workspace-entry의 새 작업/이어가기 binding을 확인하세요.


### 명시적으로 시작한 OMC 구현의 파이프라인

> OMC 구현 흐름에 진입한 뒤 Tier를 분류합니다. Tier 0는 아래 체크포인트를 생략할 수 있습니다.

#### Tier 분류 (먼저 판단)

| Tier | 정의 | CONTRACT·RED |
|---|---|---|
| **Tier 0** | 동작·API 변경과 신규 파일 없음 | 기존 정책이 허용하는 경우 CONTRACT·RED를 생략할 수 있습니다. 변경에 필요한 검증을 수행합니다. |
| **Tier 1 (신규 파일)** | 새 파일 생성, 신규 컴포넌트·훅·함수 | **CONTRACT + RED 필수** — 명시적 검증에서 확인 |
| **Tier 1 (기존 파일 수정)** | 동작·API 변경, 로직 수정 | **CONTRACT 필수**. 일반 수정의 RED는 기존 프로젝트 정책을 따르며, 버그 재현과 신규 함수 등 기존 RED 의무를 적용합니다. |

> **판단 기준 한 줄**: "동작·API·새 파일 없으면 Tier 0"

#### OMC 진입 후 Tier 1 분류

| 상황 | 예시 |
|---|---|
| 새 파일 생성 | 신규 컴포넌트, 훅, 유틸, API 함수 |
| 동작/API 변경 | 기존 함수 시그니처 변경, 새 API 연동 |
| 기능 추가 | "이 기능 추가해줘", "feature", "구현해줘" |
| 버그 수정 (동작 변경 수반) | 재현 테스트 필요한 버그 |

> Tier 1은 승인과 CONTRACT를 확인합니다. RED가 필요한 경우 실제 실패와 등록을 확인합니다.

**Tier 1 작업에서 해당하는 체크포인트만 사용합니다. Tier 0는 이 양식을 생략합니다.**

**적용되는 체크포인트를 완료한 뒤 다음 단계로 진행합니다. RED는 기존 프로젝트·스킬 정책이 요구하는 경우에 적용합니다.**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 1 ▸ CONTRACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
목표   : _______________________________________________
범위   : 포함 — ________________  /  제외 — ___________
DoD    : _______________________________________________
제약   : _______________________________________________
적용 스킬 : ☐ omc-plan  ☐ omc-task  ☐ omc-investigate
            ☐ omc-review  ☐ 해당 없음

☐ 사용자 컨펌 완료
  python3 scripts/omc_pipeline_guard.py contract-done
☐ CONTRACT 등록 완료
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    ↓ PHASE 1 완료 후 작성
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 3 ▸ RED  🔴  (기존 정책상 RED가 필요한 작업에만 적용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
테스트 파일   : ________________________________________
테스트 케이스 : ________________________________________
실행 커맨드   : ________________________________________

실패 출력 (실제 터미널 출력을 아래 블록에 붙여넣으세요 — 임의 작성 금지):
\`\`\`
[FAIL 출력 첨부]
\`\`\`

☐ 실제 FAIL 출력 첨부 완료

  python3 scripts/omc_pipeline_guard.py red-done <테스트파일> --evidence-file <RED근거JSON>

☐ RED 등록 완료
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    ↓ RED가 필요한 경우 PHASE 3 완료 후 작성
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

#### LLM별 차단 지점

| LLM | 파일 생성 차단 | 커밋 차단 | 배포 차단 |
|---|---|---|---|
| Codex | 자동 훅 비활성 — 승인·CONTRACT·RED를 명시적으로 검증 | 명시적 TDD 검증 | `omc_tdd_check.py` |

자연어 요청 대신 아래 커맨드를 직접 사용할 수 있습니다.

> **LLM마다 호출 방식이 다릅니다. 자신의 IDE에 맞는 방식만 사용하세요.**

#### Codex에서 OMC 스킬 직접 호출

일반적인 계획·구현은 Superpowers 흐름을 따릅니다. OMC 단독 운영에서는 `$omc-plan`, `$omc-task`, `$omc-review`, `$omc-ship`을 필요한 단계에서 직접 호출합니다. 작업공간과 세션 연결을 확인한 뒤 선택한 스킬의 승인·CONTRACT·실제 RED 절차를 적용합니다.

### 오케스트레이션 사용 원칙

OMC 오케스트레이션을 직접 요청할 때는 [Quickstart](omc_quickstart.md)의 조회·미리보기 명령과 선택한 스킬의 실행 범위를 확인합니다.

#### 구현 작업의 속도 기준

DoD가 고정되고 변경 범위를 한 문장으로 설명할 수 있는 작업은 fast로 진행합니다. 새 구현 파일, API 변경, 여러 파일의 연쇄 수정은 normal을 적용합니다. fast는 보고를 줄이는 방식이며 해당 작업에 필요한 검증은 모두 수행합니다.

---

### 스프린트 파이프라인 (Plan → Build → Review → Ship → Reflect)

OMC 작업의 표준 흐름입니다. **단계를 건너뛰지 않습니다.**

```
$omc-plan → $omc-task → $omc-review → $omc-ship → $omc-retro
```

필요할 때만 앞에 붙이는 선택 단계:
- 요구사항이 모호하면 Superpowers의 브레인스토밍으로 범위를 정리합니다. OMC 단독 운영에서는 `$omc-office-hours`로 필요한 질문부터 확인합니다.
- 기능 방향이 맞는지 의심되면 → `$omc-office-hours`
- 버그 원인이 불명확하면 → `$omc-investigate`

각 단계별 핵심 원칙:
- **PLAN**: 범위와 DoD 확정 전 구현 시작 금지
- **BUILD**: 구현 파일 생성 전에 실제 RED 및 해당 가드의 결과를 확인
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

### OMC 작업의 역할 추천

**사용자가 OMC 역할 추천을 포함한 작업을 명시적으로 선택하면 아래 흐름을 따릅니다.**

#### 플로우

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

#### 스크립트가 없는 환경에서의 인라인 분석 규칙

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

#### 주의사항
- **컨펌 없이 코드 수정·명령 실행·파일 생성을 시작하지 않습니다.**
- 사용자가 단순 조회(상태, 도움말)를 요청하면 역할 추천 생략 가능.
- Codex 스킬(`$omc-review`, `$omc-plan` 등) 사용 시 역할이 고정되므로 추천 단계 생략.

---

### OMC 구현의 TDD 규칙

새 파일·함수 추가 시 **반드시** 테스트를 먼저 작성합니다:
1. **RED**: 실패하는 테스트 먼저 작성
2. **GREEN**: 테스트를 통과하는 최소 구현
3. **REFACTOR**: GREEN 상태에서 코드 정리

- 테스트 없는 신규 파일(`.ts`, `.tsx`, `.py`)은 "미완성"으로 간주
- 버그 수정 시: 재현 테스트 먼저 → 수정 → GREEN 확인
- `$omc-ship` 실행 시: 신규 파일에 테스트 없으면 자동 차단
- 예외(사용자 명시 승인 필요): 타입 정의(`*.d.ts`), 설정 파일(`*.config.ts`), 빈 index 파일
  - 예외 허용: `python3 scripts/omc_pipeline_guard.py allow <파일> --reason "이유"` (이유 기록 권장, `.omc/allow_log.jsonl` 에 감사 로그 저장)

---

- 사용자에게 보이는 안내/컨펌/보고는 기본 한글로 작성합니다(사용자가 영어를 명시하면 예외).
- 프로젝트 전용 오버레이는 `project_prompts/team.local.json`이 있으면 함께 반영합니다.
- 공통 CLI에서 OMC에 작업을 요청할 때는 `python scripts/omc.py "요청"`을 사용합니다.
- `.omc/` 상태는 `scripts/omc.py state init|status|compact`로 관리합니다.
- 기본 정책은 `.omc/policy.json`의 `enforce_confirm=true`이며, `scripts/omc_guard.py`가 최신 미확정 세션을 차단합니다.
- **자동 실행 비활성**: 세션 시작·도구·Git 자동 훅을 설치하지 않습니다. 상태 조회와 작업 승인을 구분하고 명시적 작업 진입에서만 승인·CONTRACT를 확인합니다. ship/git commit 전 수동 TDD 검증은 유지합니다.
- 라이프사이클 훅은 `scripts/omc.py hook session_start|session_end|pre_compact|post_compact`를 사용합니다.
- 모드는 `autopilot`, `team`, `ulw`, `ralph`, `deep-interview`를 요청 의도에 맞게 선택합니다.

---

### Compound Engineering (반복 실수 방지)

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

`omc_context.py`를 명시적으로 실행하면 현재 브랜치와 최근 커밋을 BM25 쿼리로 사용해 관련 교훈을 추천합니다.
`$omc-retro` 커맨드에 교훈 캡처 단계가 포함됩니다.

### Autopilot — 멀티 LLM 자율 루프 (옵트인)

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

### ⛔ 스킬 완료 후 자동 진행 금지 (MANDATORY — Codex)

**아래 전환 규칙은 OMC 스킬에만 적용합니다.**

스킬이 완료되면 판정·결과를 출력한 뒤 다음 스킬로 자동 진입하지 않는다.
사용자가 "진행하자", "계속해", "응" 같은 짧은 승인을 해도
**명시적으로 다음 스킬 이름이 언급되지 않으면 자동 진입 금지.**

#### 예외: 승인된 단일 작업 V0 내부 단계

`omc-autopilot-mission/v2`와 동일한 승인 receipt·work contract를 검증한 실행기 내부에서만 구현→검증→리뷰→수정 전환을 허용합니다. 부모 실행기가 승인·CONTRACT·RED·예산을 연결하며 각 스킬이 승인을 추정하거나 가드를 생략하지 않습니다. 실행기가 없는 일반 대화, 범위 변경, 만료, BLOCK에는 적용하지 않습니다. 원본 반영·게시·배포 승인은 포함하지 않습니다.

#### 예외: 이미 제시한 local commit 선택의 단일 승인 상속

새 스킬 진입이 아니라, 현재 세션에서 이미 범위와 선택지를 제시한 **로컬 커밋**을 확정하는 짧은 응답은 다시 묻지 않습니다.

- 선택지를 보여주기 직전에 `python3 scripts/omc.py state decision-open --target . --decision-id <id> --action local_commit --options-json '<options>'`으로 pending decision을 기록합니다.
- 여러 커밋 그룹을 제시할 때는 각 option에 `paths` 배열을 넣어 선택 그룹의 파일 범위를 명시합니다. 단일 option은 생략 시 현재 로컬 커밋 범위 전체를 사용합니다.
- 같은 세션·같은 변경 내용·TTL 이내의 정확한 별칭(`1`, `1번`, `확인` 등)만 상속합니다. staging 위치만 바뀐 것은 같은 범위로 봅니다.
- 범위나 세션이 바뀌거나, 만료·중복 별칭·불명확한 응답이면 상속하지 않고 다시 확인합니다.
- 범위와 의도가 확정됐으면 커밋 메시지는 저장소 규칙에 맞게 자동 작성하며 별도로 재확인하지 않습니다.
- 로컬 커밋 성공 후 `python3 scripts/omc.py state decision-consume --target . --decision-id <id>`로 receipt를 1회 소비합니다.
- 이 상속은 UX acknowledgment일 뿐 권한 승인이 아닙니다. push, PR, deploy, delete, reset 등은 항상 별도 요청이 필요합니다.

| 완료된 스킬 | 금지 동작 |
|---|---|
| omc-office-hours | PROCEED 판정 후 자동으로 omc-plan 실행 금지 |
| omc-plan | Phase 완료 후 자동으로 omc-task 실행 금지 |
| omc-critique | 판정 후 자동으로 omc-plan/omc-task 실행 금지 |
| omc-benchmark | 분석 후 자동으로 omc-office-hours/omc-plan 실행 금지 |
| omc-task | 완료 후 자동으로 omc-review/omc-ship 실행 금지 |

**올바른 동작:**
> "PROCEED 판정입니다. 다음으로 `$omc-plan` 진행할까요?"
> → 여기서 멈추고 사용자 응답을 기다린다.

**금지 동작:**
> "PROCEED 판정입니다. 바로 플랜을 작성하겠습니다. [plan 내용 시작]..."
