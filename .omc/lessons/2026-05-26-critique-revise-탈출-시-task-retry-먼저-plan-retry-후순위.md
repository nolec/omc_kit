# critique REVISE 탈출 시 task_retry 먼저, plan_retry 후순위
날짜: 2026-05-26
태그: autopilot,critique,task_retry

## 증상
critique REVISE×3 탈출 후 plan만 재실행해도 동일 코드 품질 문제가 반복됨

## 원인
plan은 설계를 바꾸지만 코드 수정은 task 몫 — task 재실행 없이는 코드가 그대로 남음

## 적용된 규칙
failed_critique_loop 시: 1순위 task_retry(critique_issues 주입) → 2순위 plan_retry → hold

## 검증 커맨드
python3 -m pytest scripts/test_omc_critique_recovery.py -q
