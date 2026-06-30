# top-expensive-flows에 next-action gap 노출
날짜: 2026-06-30
태그: benchmark,next-action,report

## 증상
비싼 흐름은 보여도 다음 스킬 오판 여부가 바로 안 보였다

## 원인
top-expensive-flows가 비용/순위 중심이고 next-action 정오판 정보를 충분히 함께 싣지 않았다

## 적용된 규칙
비싼 흐름 리포트에는 expected_next_action와 baseline/candidate 정오판을 함께 노출한다

## 검증 커맨드
pytest scripts/test_omc_skill_benchmark.py -q
