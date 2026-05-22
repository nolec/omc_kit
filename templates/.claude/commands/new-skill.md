# /new-skill — 새 OMC 스킬 등록

새 OMC 스킬을 만들 때 **9개 등록 항목**을 빠짐없이 처리합니다.

---

## AI가 반드시 따르는 순서

1. `.agent/skills/SKILL_CHECKLIST.md` 읽기
2. 체크리스트 9개 항목 순서대로 처리
3. 각 항목 완료 즉시 체크 표시
4. 9번 SSOT 동기화까지 완료 후 "완료 선언" 출력

**항목 하나라도 빠지면 → "미완성" 알림 출력 후 중단**

---

## 등록 항목 (9개)

| # | 대상 | 파일 |
|---|---|---|
| 1 | Cursor 스킬 | `.agent/skills/omc-[NAME]/SKILL.md` |
| 2 | Codex 스킬 | `.agents/skills/omc-[NAME]/SKILL.md` (1번과 동일 복사) |
| 3 | Claude Code | `.claude/commands/[NAME].md` |
| 4 | Gemini CLI | `.gemini/commands/omc-commands.md` (섹션 추가) |
| 5 | Codex CLI | `.codex/commands/omc-commands.md` (`$omc-[NAME]` 추가) |
| 6 | AGENTS.md | 커맨드 테이블 행 추가 |
| 7 | Cursor Commands | `.cursor/rules/omc-commands.mdc` 섹션 추가 |
| 8 | Cursor Roles | `.cursor/rules/omc-roles.mdc` 트리거 키워드 추가 |
| 9 | omc_kit SSOT | 위 파일 전부 `$(cat .omc/hub.path)/templates/`에 복사 |

---

## 완료 선언 포맷

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
스킬 등록 완료: omc-[NAME]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[x] 1. Cursor 스킬
[x] 2. Codex 스킬
[x] 3. Claude Code
[x] 4. Gemini CLI
[x] 5. Codex CLI
[x] 6. AGENTS.md
[x] 7. omc-commands.mdc
[x] 8. omc-roles.mdc
[x] 9. omc_kit SSOT 동기화
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

스킬 이름: $ARGUMENTS
