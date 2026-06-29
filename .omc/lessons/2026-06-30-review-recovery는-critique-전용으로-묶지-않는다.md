# review recovery는 critique 전용으로 묶지 않는다
날짜: 2026-06-30
태그: omc,decision-engine,recovery,review,critique

## 증상
critique와 review 둘 다 같은 recovery 엔진을 써야 하는데, critique 전용 guard가 review 재진입을 막을 수 있었다.

## 원인
복구 타겟 계산 함수가 loop_step == critique일 때만 동작하도록 고정돼 있어서 review 단계의 reroute/plan_retry가 소비되지 않았다.

## 적용된 규칙
recovery target은 step 이름보다 decision/reason_codes를 우선으로 보고, step별 차이는 task_retry vs plan_retry 우선순위만 조절한다.

## 검증 커맨드
pytest scripts/test_omc_autopilot_pipeline.py -q -k 'recovery_target_from_decision or failed_review_loop_quality_failure_uses_plan_retry_before_task_retry'
