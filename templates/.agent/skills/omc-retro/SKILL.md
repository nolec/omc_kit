---
skill_name: omc-retro
description: "세션 히스토리 분석 후 주간 회고 실행. 트리거: 회고, 이번 주 리뷰, 돌아보기, 주간 정리, 회고해줘. 완료·실패 세션 분석 후 교훈 캡처."
---

# OMC 주간 회고

```bash
python3 scripts/omc.py state status --target .
cat .omc/notepad.md
python3 scripts/omc_lesson.py list
```

## 회고 포맷

1. 이번 주 완료된 작업 (confirmed 세션)
2. 반복되는 문제 패턴
3. 완료되지 못한 작업
4. 다음 주 우선순위

## Compound Engineering 교훈 캡처 (MANDATORY)

```bash
python3 scripts/omc_lesson.py add -i
```
