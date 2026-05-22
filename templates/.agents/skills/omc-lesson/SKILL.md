---
skill_name: omc-lesson
description: "교훈을 .omc/lessons/에 기록해 BM25 자동 주입에 활용. 트리거: 교훈 기록, 배운 거 저장, 다음에 주의할 것, 교훈 남기자, 실수 기록. 파일 하나 = 교훈 하나."
---

# OMC Compound Engineering 교훈 캡처

> **이 스킬을 쓰면 안 되는 상황**:
> - 교훈이 이미 기록되어 있는지 먼저 확인 → `search` 먼저
> - 단순 메모 → `.omc/notepad.md` 에 직접 기록

---

## 교훈 추가

```bash
python3 scripts/omc_lesson.py add -i
```

### 작성 양식

```
제목  : _______________________________________________
태그  : _______________________________________________
증상  : (언제, 어떤 상황에서 문제가 발생했는가)
원인  : (왜 그런 일이 생겼는가)
규칙  : (다음에 이렇게 하면 된다 — 행동 지침 형태로)
```

---

## 교훈 조회

```bash
# 목록 전체 보기
python3 scripts/omc_lesson.py list

# 키워드로 검색 (BM25 유사도)
python3 scripts/omc_lesson.py search "키워드"
python3 scripts/omc_lesson.py search "키워드" --top 3
```

---

## 규칙
- 한 파일 = 한 교훈 (여러 교훈을 한 파일에 묶지 않음)
- 다음 세션 시작 시 BM25 유사도 기반으로 관련 교훈이 자동 주입됩니다
- 교훈은 "규칙" 항목을 행동 지침 형태로 작성해야 자동 주입 효과가 높습니다
