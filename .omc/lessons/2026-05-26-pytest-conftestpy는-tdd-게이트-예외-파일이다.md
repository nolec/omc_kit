# pytest conftest.py는 TDD 게이트 예외 파일이다
날짜: 2026-05-26
태그: tdd,conftest,pytest,infra

## 증상
conftest.py를 추가했을 때 TDD GATE BLOCK이 발생해 커밋 차단

## 원인
omc_tdd_check.py의 _EXCLUDE_PATTERNS에 conftest.py 패턴이 없어 신규 구현 파일로 오인

## 적용된 규칙
omc_tdd_check.py _EXCLUDE_PATTERNS에 r'/conftest\.py$' 패턴 추가. pytest 인프라 파일(conftest.py)은 TDD 대상이 아님

## 검증 커맨드
python3 scripts/omc_tdd_check.py --staged 실행 시 conftest.py가 excluded로 처리되는지 확인
