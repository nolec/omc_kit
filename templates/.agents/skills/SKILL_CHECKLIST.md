# OMC 스킬 등록 체크리스트

> **새 스킬을 만들 때 반드시 이 체크리스트를 완료해야 합니다.**
> 체크박스를 채우지 않으면 스킬이 "미완성"으로 간주됩니다.

---

## Part 1 — SKILL.md 작성 품질 기준 (먼저 확인)

> **SKILL.md를 작성하기 전에 `SKILL_TEMPLATE.md`를 복사해서 시작한다.**
> 아래 항목이 모두 포함돼야 스킬이 "완성"으로 간주됩니다.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SKILL.md 품질 체크리스트
스킬: omc-[NAME]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[frontmatter]
[ Q1 ] skill_name 필드 있음
[ Q2 ] description 필드에 트리거 키워드 포함

[컨텍스트 수집]
[ Q3 ] 커맨드 블록이 있는 섹션마다 AI 실행 지시문 있음
       형식: > **AI는 아래 커맨드를 직접 실행하고 결과를 확인한다. 건너뛰지 않는다.**
[ Q4 ] 수집 커맨드에 2>/dev/null 붙어 있음 (스크립트 없는 환경 대비)
[ Q5 ] 수집 결과 → 출력 항목 연결 매핑 있음
       형식: 수집 결과 연결: / - [커맨드] → **항목 N** 에 반영

[출력 포맷]
[ Q6 ] 출력 포맷(양식)이 명시됨
[ Q7 ] 데이터 없을 때 처리 기준 있음
       형식: "없으면 '없음'으로 명시하고 빈칸으로 두지 않는다"

[체크박스]
[ Q8 ] 완료 체크박스(☐)가 코드블록 밖에 있음
       (코드블록 안 체크박스는 AI가 인터랙션 불가)

[이후 액션]
[ Q9 ] "이 스킬을 쓰면 안 되는 상황" 섹션 있음
[ Q10] "이후 액션" 섹션 있음 (완료/실패 시 다음 스킬 링크 포함)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
완료 기준: Q1~Q10 전부 체크
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**빠른 작성법**: `SKILL_TEMPLATE.md`를 복사하면 Q3~Q8이 자동으로 충족됩니다.

```bash
cp .agents/skills/SKILL_TEMPLATE.md .agents/skills/omc-[NAME]/SKILL.md
cp .agents/skills/SKILL_TEMPLATE.md .agent/skills/omc-[NAME]/SKILL.md
```

---

## Part 2 — 파일 등록 체크리스트

## 스킬 이름: `omc-[NAME]`

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEW SKILL REGISTRATION CHECKLIST
스킬: omc-[NAME]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[ 1 ] Cursor (Agent Skills)
      파일: .agent/skills/omc-[NAME]/SKILL.md
      확인: skill_name, description, 트리거 키워드 포함

[ 2 ] Codex (Agent Skills)
      파일: .agents/skills/omc-[NAME]/SKILL.md
      확인: .agent/skills와 동일 내용 복사

[ 3 ] Claude Code (Slash Command)
      파일: .claude/commands/[NAME].md
      확인: # /[NAME] 헤더, $ARGUMENTS 변수, 이후 액션 포함

[ 4 ] Gemini CLI (Commands 통합 파일)
      파일: .gemini/commands/omc-commands.md
      확인: ### `/[NAME] [인자]` 섹션 추가

[ 5 ] Codex CLI (Commands 통합 파일)
      파일: .codex/commands/omc-commands.md
      확인: $omc-[NAME] 호출 목록에 추가

[ 6 ] AGENTS.md (커맨드 테이블)
      위치: "선택 커맨드" 테이블 또는 "코어 커맨드" 테이블
      확인: | `/[NAME] [인자]` | — | 설명 |

[ 7 ] .cursor/rules/omc-commands.mdc
      확인: ## /[NAME] [인자] 섹션 추가

[ 8 ] .cursor/rules/omc-roles.mdc
      확인: 트리거 키워드 테이블에 키워드 추가

[ 9 ] omc_kit SSOT 동기화
      커맨드:
        OMC_KIT=$(cat .omc/hub.path)
        cp .agent/skills/omc-[NAME]/SKILL.md  $OMC_KIT/templates/.agent/skills/omc-[NAME]/SKILL.md
        cp .agents/skills/omc-[NAME]/SKILL.md $OMC_KIT/templates/.agents/skills/omc-[NAME]/SKILL.md
        cp .claude/commands/[NAME].md          $OMC_KIT/templates/.claude/commands/[NAME].md
        cp .gemini/commands/omc-commands.md    $OMC_KIT/templates/.gemini/commands/omc-commands.md
        cp .codex/commands/omc-commands.md     $OMC_KIT/templates/.codex/commands/omc-commands.md
        cp AGENTS.md                           $OMC_KIT/templates/AGENTS.md
        cp .cursor/rules/omc-commands.mdc      $OMC_KIT/templates/.cursor/rules/omc-commands.mdc
        cp .cursor/rules/omc-roles.mdc         $OMC_KIT/templates/.cursor/rules/omc-roles.mdc

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
완료 기준: 9개 항목 전부 체크
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 빠르게 실행하는 방법 (자동화 스크립트)

```bash
# 스킬 이름만 바꿔서 실행
SKILL_NAME="[NAME]"
OMC_KIT=$(cat .omc/hub.path 2>/dev/null)
if [ -z "$OMC_KIT" ] || [ ! -d "$OMC_KIT" ]; then
  echo "오류: .omc/hub.path 없음 또는 경로 유효하지 않음"
  echo "  python3 scripts/omc.py hub init 또는 echo /path/to/omc_kit > .omc/hub.path"
  exit 1
fi

# 1-2: Cursor + Codex 스킬 디렉토리 생성 (템플릿 복사)
mkdir -p .agent/skills/omc-$SKILL_NAME
mkdir -p .agents/skills/omc-$SKILL_NAME
cp .agents/skills/SKILL_TEMPLATE.md .agent/skills/omc-$SKILL_NAME/SKILL.md
cp .agents/skills/SKILL_TEMPLATE.md .agents/skills/omc-$SKILL_NAME/SKILL.md

# 9: SSOT 동기화 (파일 작성 후 실행)
mkdir -p $OMC_KIT/templates/.agent/skills/omc-$SKILL_NAME
mkdir -p $OMC_KIT/templates/.agents/skills/omc-$SKILL_NAME
cp .agent/skills/omc-$SKILL_NAME/SKILL.md  $OMC_KIT/templates/.agent/skills/omc-$SKILL_NAME/SKILL.md
cp .agents/skills/omc-$SKILL_NAME/SKILL.md $OMC_KIT/templates/.agents/skills/omc-$SKILL_NAME/SKILL.md
cp .claude/commands/$SKILL_NAME.md          $OMC_KIT/templates/.claude/commands/$SKILL_NAME.md
cp .gemini/commands/omc-commands.md         $OMC_KIT/templates/.gemini/commands/omc-commands.md
cp .codex/commands/omc-commands.md          $OMC_KIT/templates/.codex/commands/omc-commands.md
cp AGENTS.md                                $OMC_KIT/templates/AGENTS.md
cp .cursor/rules/omc-commands.mdc           $OMC_KIT/templates/.cursor/rules/omc-commands.mdc
cp .cursor/rules/omc-roles.mdc              $OMC_KIT/templates/.cursor/rules/omc-roles.mdc
```

---

## 새 스킬이 안 보일 때 체크리스트

설치 후 스킬이 목록에 뜨지 않는 3가지 원인:

| # | 원인 | 해결 |
|---|---|---|
| 1 | SSOT 경로 오류 | `templates/.agents/skills/`에 있는지 확인. `.agent/skills/`에만 넣으면 install 시 무시됨 |
| 2 | install --force 미실행 | `python3 omc_kit/scripts/install.py --target . --force` 재실행 |
| 3 | Cursor 세션 캐시 | 스킬 파일 저장 후 Cursor 세션 재시작 (Cmd+Shift+P → "Reload Window") |

**빠른 진단:**
```bash
# 1. 스킬이 실제로 설치됐는지 확인
python3 scripts/omc_skill_check.py --all

# 2. 누락된 스킬이 있으면 재설치
python3 path/to/omc_kit/scripts/install.py --target . --force

# 3. 그래도 안 보이면 → Cursor 재시작
```

---

## 각 LLM 파일 최소 요구사항

### `.agent/skills/omc-[NAME]/SKILL.md` (Cursor + Codex 공통)

`SKILL_TEMPLATE.md`를 복사해서 시작합니다. 최소 구조는 아래와 같습니다.

```markdown
---
skill_name: omc-[NAME]
description: "[한 줄 설명]. 트리거: [트리거 키워드]. [핵심 제약]."
---

# [스킬 제목]

> **이 스킬을 쓰면 안 되는 상황**: ...

## Step 0: 컨텍스트 수집

> **AI는 아래 커맨드를 직접 실행하고 결과를 확인한다. 건너뛰지 않는다.**

[커맨드 블록 + 2>/dev/null]

수집 결과 연결:
- [커맨드] → **항목 N** 에 반영

## 실행 순서 / 출력 포맷
...

## 이후 액션
...
```

### `.claude/commands/[NAME].md` (Claude Code)

```markdown
# /[NAME] — [제목]

[한 줄 설명]

## 언제 쓰나
...

## AI가 할 일
...

---

대상: $ARGUMENTS (미지정 시 [기본 동작])
```
