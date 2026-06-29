# next-action 라벨 변형 흡수
날짜: 2026-06-30
태그: omc,benchmark,next-action,review

## 증상
benchmark가 다음 액션: 만만 잡아서 추천 다음 스킬, 다음 단계 같은 실제 출력 변형을 놓쳤다.

## 원인
파서 정규식이 한 가지 라벨만 가정했고, 리뷰에서 지적한 누락을 실제 테스트로 바로 고정하지 않았다.

## 적용된 규칙
next-action 파서는 라벨 동의어와 구분자 변형을 함께 허용하고, 새 라벨은 fixture와 테스트에 함께 추가한다.

## 검증 커맨드
pytest scripts/test_omc_skill_benchmark.py -q && python3 scripts/omc_tdd_check.py --staged
