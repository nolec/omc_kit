# task-stage plan_retry와 critique budget 분리
날짜: 2026-06-30
태그: omc,autopilot,retry,review

## 증상
task-stage orchestration failure 복구가 critique plan_retry 예산을 잠식했다

## 원인
plan_retry 복구 헬퍼가 critique 카운터를 공용으로 증가시켜 이후 critique 재진입 여지를 줄였다

## 적용된 규칙
task-stage와 critique-stage의 plan_retry budget은 분리하고, orchestration reason code는 REASON_CODE 같은 구조화 마커만 읽는다

## 검증 커맨드
pytest scripts/test_omc_autopilot_pipeline.py -q && python3 scripts/omc_tdd_check.py --staged
