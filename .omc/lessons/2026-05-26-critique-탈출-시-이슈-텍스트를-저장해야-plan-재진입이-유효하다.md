# critique 탈출 시 이슈 텍스트를 저장해야 plan 재진입이 유효하다
날짜: 2026-05-26
태그: autopilot,critique,recovery

## 증상
failed_critique_loop 후 --resume 해도 동일 REVISE 반복

## 원인
last_output[:300] 슬라이싱으로 이슈 본문이 잘리거나 빈 컨텍스트로 plan 재실행

## 적용된 규칙
_extract_critique_issues 로 VERDICT 앞 30줄 추출 → critique_issues 저장 → plan_retry 프롬프트에 주입

## 검증 커맨드
pytest scripts/test_omc_critique_recovery.py — 7 passed
