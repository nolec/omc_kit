# next-action gap은 누락과 오답을 분리
날짜: 2026-06-30
태그: benchmark,next-action,gap

## 증상
top-expensive-flows에서 next-action이 한쪽만 빠진 케이스가 gap으로 충분히 드러나지 않았다

## 원인
정오판만 보다가 누락 자체를 별도 상태로 모델링하지 않았다

## 적용된 규칙
expected_next_action이 있으면 baseline/candidate 누락 여부를 next_action_incomplete로 따로 기록하고, next_action_gap에는 누락도 포함한다

## 검증 커맨드
pytest scripts/test_omc_skill_benchmark.py -q
