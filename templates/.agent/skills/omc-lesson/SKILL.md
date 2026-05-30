---
skill_name: omc-lesson
description: "교훈을 .omc/lessons/에 기록해 BM25 자동 주입에 활용. 트리거: 교훈 기록, 배운 거 저장, 다음에 주의할 것, 교훈 남기자, 실수 기록. 파일 하나 = 교훈 하나."
---

# OMC Lesson

Compound Engineering 교훈 캡처 스킬입니다. 단순 메모는 `.omc/notepad.md`에 남깁니다.

## Phase 0. 중복 검색

```bash
python3 scripts/omc_lesson.py search "키워드" --top 3
```

- 유사 교훈 없음 → 신규 기록 후보
- 유사 교훈 있음 → 기존 교훈 확인 후 수동 편집 또는 신규 기록 여부를 사용자에게 확인
- `omc_lesson.py`에는 update 명령 없음

## Phase 1. 기록 가능 여부

- 이번 task 중에는 실제 `.omc/lessons/` 파일 생성·수정 금지
- 실제 기록은 사용자가 구체적 교훈 기록을 요청했을 때만 수행
- 제목/증상/원인/규칙을 먼저 확인합니다
- 태그가 없으면 `general` 또는 `N/A — 이유`
- verify는 재발 방지를 확인한 명령이나 방법
- 규칙은 다음에 할 행동 지침 형태로 작성
- 제목/증상/원인/규칙 중 하나라도 불명확하면 기록 금지

## Phase 2. 기록 방식

사용자 직접 입력:

```bash
python3 scripts/omc_lesson.py add -i
```

비인터랙티브 입력:

```bash
python3 scripts/omc_lesson.py add \
  --title "제목" \
  --symptom "증상" \
  --cause "원인" \
  --rule "행동 지침" \
  --verify "검증" \
  --tags "general"
```

## Phase 3. 확인

```bash
python3 scripts/omc_lesson.py list
python3 scripts/omc_lesson.py search "키워드" --top 3
python3 scripts/omc_lesson.py show <lesson-id>
```

방금 제목이 안 보이면 search 또는 show로 재확인하고 실패를 보고합니다.

## 출력

```text
중복 검색:
판단: 교훈 없음 / 신규 기록 / 기존 교훈 후보 발견
필수 필드: 제목/증상/원인/규칙 명확 여부
기록 방식: add -i / add --title ...
확인:
다음 액션: $omc-retro / 세션 계속
```

## 규칙

- 한 파일 = 한 교훈
- BM25 자동 주입 효과를 위해 규칙은 행동 지침으로 작성
- 기존 교훈 후보 발견 시 바로 새 파일을 만들지 않음
- 완료 후 다음 액션은 `$omc-retro` 또는 세션 계속
