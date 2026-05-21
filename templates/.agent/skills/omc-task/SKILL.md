---
skill_name: omc-task
description: "7단계 TDD 파이프라인으로 구현 실행. 트리거: 구현해줘, 만들어줘, 코딩해줘, 개발해줘, 이거 짜줘. CONTRACT→RED→GREEN→REFACTOR→GATE→REVIEW 순서. 단계 건너뜀 금지. omc-plan 이후 사용."
---

# OMC TDD 파이프라인

구현 작업을 7단계 체크포인트 양식으로 실행합니다. **건너뜀 금지.**

## 전제 조건

```bash
python3 scripts/omc_guard.py require --for "task" --target .
```

## 파이프라인 양식

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 1 ▸ CONTRACT
목표   : _______________
범위   : _______________
DoD    : _______________
☐ 사용자 컨펌 완료
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 3 ▸ RED  🔴
테스트 파일   : _______________
테스트 케이스 : _______________
☐ 실제 FAIL 출력 첨부 완료
  python3 scripts/omc_pipeline_guard.py red-done <테스트파일>
☐ RED 등록 완료
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 4 ▸ GREEN  🟢
구현 파일  : _______________
☐ 테스트 PASS 확인
☐ 기존 테스트 회귀 없음
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 6 ▸ TDD GATE
  python3 scripts/omc_tdd_check.py --staged
☐ 반환값 0 확인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 7 ▸ COMPOUND ENGINEERING
  python3 scripts/omc_lesson.py add -i
☐ 교훈 없음 / ☐ 교훈 기록 완료
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 규칙
- 체크박스를 채우지 않으면 다음 단계로 진행하지 않습니다
- RED 등록 없이 구현 파일 생성 금지
