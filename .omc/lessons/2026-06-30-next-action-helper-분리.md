# next-action helper 분리
날짜: 2026-06-30
태그: omc,benchmark,next-action,refactor

## 증상
파서 정리를 하려면 라벨 추출과 페이로드 추출을 먼저 분리된 helper로 고정하는 편이 안전했다.

## 원인
정규식 하나만 직접 만지기보다 label/separator/payload를 나누는 helper를 먼저 두는 게 변경 안전성이 높았다.

## 적용된 규칙
새 next-action 변형은 helper 단위 테스트를 먼저 추가하고, 기존 eval 테스트는 회귀 확인용으로 둔다.

## 검증 커맨드
pytest scripts/test_omc_skill_benchmark.py -q && python3 scripts/omc_tdd_check.py --staged
