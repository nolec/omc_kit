# next-action 영어 라벨도 흡수
날짜: 2026-06-30
태그: omc,benchmark,next-action,review

## 증상
Korean next-action 라벨은 잡았지만 영어 출력의 Next action은 benchmark가 놓쳤다.

## 원인
파서가 라벨 언어와 구분자 다양성을 충분히 흡수하지 않았고, 그 누락을 먼저 테스트로 못 박지 않았다.

## 적용된 규칙
라벨 동의어는 Korean/English를 함께 허용하고, 새 라벨은 대표 테스트와 함께 추가한다.

## 검증 커맨드
pytest scripts/test_omc_skill_benchmark.py -q && python3 scripts/omc_tdd_check.py --staged
