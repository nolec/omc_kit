# OMC 스킬 등록 체크리스트

> **새 스킬을 만들 때 반드시 이 체크리스트를 완료해야 합니다.**
> 체크박스를 채우지 않으면 스킬이 "미완성"으로 간주됩니다.

---

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

# 1-2: Cursor + Codex 스킬 디렉토리 생성
mkdir -p .agent/skills/omc-$SKILL_NAME
mkdir -p .agents/skills/omc-$SKILL_NAME

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

```markdown
---
skill_name: omc-[NAME]
description: "[한 줄 설명]. 트리거: [트리거 키워드]. [핵심 제약]."
---

# [스킬 제목]

## 실행 순서
...

## 출력 포맷
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
