# full-pipeline 자동화: VERDICT 규약 + --autopilot 플래그 패턴
날짜: 2026-05-25
태그: pipeline,automation,llm-parsing,tdd-guard

## 증상
full-pipeline 자동화에서 LLM 출력 판정 파싱과 pipeline_guard 수동 개입 문제

## 원인
LLM 자연어 출력에서 pass/fail을 판별할 규약이 없었고, pipeline_guard에 autopilot bypass 플래그도 없었음

## 적용된 규칙
1) 스킬 출력 포맷에 VERDICT: 키워드 강제 + grep 판별. 2) pipeline_guard에 --autopilot opt-in 플래그. 3) retry cap + 결과 파일 필수.

## 검증 커맨드
pytest test_omc_pipeline_guard_autopilot.py test_omc_autopilot_pipeline.py -v
