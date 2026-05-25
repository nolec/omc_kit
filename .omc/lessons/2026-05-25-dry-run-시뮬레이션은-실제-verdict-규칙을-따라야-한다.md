# dry-run 시뮬레이션은 실제 verdict 규칙을 따라야 한다
날짜: 2026-05-25
태그: dry-run,verdict,review,regression

## 증상
review verdict를 APPROVE로 엄격화하자 dry-run 회귀가 발생했다

## 원인
dry-run이 항상 VERDICT: PROCEED를 반환해 review 스텝 기대값과 불일치

## 적용된 규칙
dry-run은 step_name별로 실제 허용 verdict를 반환해야 한다 (review → APPROVE, 그 외 → PROCEED)

## 검증 커맨드
pytest scripts/test_omc_autopilot_pipeline.py -v
