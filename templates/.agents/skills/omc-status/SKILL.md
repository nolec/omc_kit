---
skill_name: omc-status
description: "현재 OMC 세션 상태·작업 컨텍스트 출력. 트리거: 지금 상태, 뭐하고 있었어, 현재 작업, 어디까지 했어, 상태 보여줘. confirmed 태스크·pending 항목·다음 액션 요약."
---

# OMC 현재 상태 확인

```bash
python3 scripts/omc.py state status --target .
cat .omc/notepad.md
```

출력 후 요약:

1. **현재 확정된 작업**
2. **미처리 pending**
3. **최근 5개 세션 흐름**
4. **다음 1액션 제안**
