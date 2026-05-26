# plan_retry HOLD verdict는 critique 루프 재진입 전에 탈출해야 한다
날짜: 2026-05-26
태그: autopilot,critique,plan_retry,hold

## 증상
plan_retry 후 HOLD verdict 무시 → critique HOLD×3 streak → hold exit 2 (우회 경로)

## 원인
plan_retry 실행 후 _grep_verdict(plan_out)==HOLD 체크 없이 critique 루프 재진입

## 적용된 규칙
_run_pipeline_step 후 HOLD verdict 즉시 감지 → save(hold) + return 2

## 검증 커맨드
pytest test_omc_critique_recovery.py::test_plan_retry_hold_verdict_exits_hold — step_calls[-1]==plan_retry
