# 병렬 telemetry는 실행별 receipt로 귀속한다
날짜: 2026-08-24
태그: concurrency,telemetry,token-budget

## 증상
동일 executor의 병렬 child가 다른 child의 token usage를 읽음

## 원인
공용 cost log에서 executor 이름과 마지막 행만으로 실행 결과를 선택함

## 적용된 규칙
병렬 실행의 telemetry는 호출 전에 생성한 고유 receipt ID를 provider 기록과 결과 조회에 함께 사용한다

## 검증 커맨드
pytest -q scripts/test_omc_exec_codex_headless.py::test_concurrent_headless_runners_keep_token_receipts_isolated
