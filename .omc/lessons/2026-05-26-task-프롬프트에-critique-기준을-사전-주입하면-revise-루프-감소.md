# task 프롬프트에 critique 기준을 사전 주입하면 REVISE 루프 감소
날짜: 2026-05-26
태그: autopilot,critique,task_prompt,quality

## 증상
task가 critique 기준을 모른 채 구현 → critique REVISE 반복 → hold

## 원인
task 와 critique 기준이 분리돼 있어 task가 품질 기준을 인지하지 못함

## 적용된 규칙
_CRITIQUE_QUALITY_HINT 를 task_prompt / task_prompt_lite 에 항상 포함시킨다

## 검증 커맨드
python3 -m pytest scripts/test_omc_critique_recovery.py::test_task_prompt_contains_critique_quality_hint -q
