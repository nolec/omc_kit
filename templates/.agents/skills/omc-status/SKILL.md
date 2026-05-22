---
skill_name: omc-status
description: "현재 OMC 세션 상태·작업 컨텍스트 출력. 트리거: 지금 상태, 뭐하고 있었어, 현재 작업, 어디까지 했어, 상태 보여줘. confirmed 태스크·pending 항목·다음 액션 요약."
---

# OMC 현재 상태 확인

> **이 스킬을 쓰면 안 되는 상황**:
> - 파이프라인 차단 여부 확인 → `python3 scripts/omc_pipeline_guard.py status`
> - 교훈 목록 확인 → `$omc-lesson`

---

## 데이터 수집

> **AI는 아래 커맨드를 직접 실행하고 결과를 확인한 후 출력 포맷을 채운다. 건너뛰지 않는다.**

```bash
python3 scripts/omc.py state status --target . 2>/dev/null
cat .omc/notepad.md 2>/dev/null
git log --oneline -5 2>/dev/null
git branch --show-current 2>/dev/null
```

수집 결과 연결:
- `omc.py state status` → **항목 1** (확정 작업) + **항목 2** (pending) + **항목 3** (세션 흐름)
- `.omc/notepad.md` → **항목 2** (pending 보완)
- `git log --oneline -5` → **항목 3** (최근 흐름 보완)
- `git branch --show-current` → **항목 1** (작업 브랜치 컨텍스트)

데이터가 없거나 커맨드 결과가 비어있으면 해당 항목에 "없음"으로 명시한다. 빈칸으로 두지 않는다.

---

## 출력 포맷

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OMC STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 현재 확정된 작업
   (confirmed 세션의 목표·DoD 요약 + 브랜치)
   → _______________

2. 미처리 pending
   (아직 완료되지 않은 세션 또는 notepad 항목)
   없으면 "없음" 으로 명시
   → _______________

3. 최근 5개 세션 흐름
   (완료/실패/진행 중 상태와 간단한 설명)
   없으면 "없음" 으로 명시
   → _______________

4. 다음 1액션 제안
   판단 기준:
   - pending 항목 있음 → 해당 작업 재개 제안
   - 미확정 세션 있음 → confirm 먼저 제안
   - 모두 완료·클린 상태 → $omc-retro 또는 다음 우선순위 제안
   → _______________
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
