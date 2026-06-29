# task_retry BLOCK도 failed로 기록해 resume에서 completed로 오인하지 않기
날짜: 2026-06-30
태그: task,retry,resume,orchestration_failure

## 증상
task_retry가 VERDICT: BLOCK를 받아도 completed 상태로 남아 resume가 완료로 잘못 판단할 수 있었다.

## 원인
task_retry BLOCK 경로가 _retry_step_payload 결과를 그대로 재사용하고 status를 failed로 덮어쓰지 않았다.

## 적용된 규칙
task_retry와 task 모두 무사유 BLOCK이면 공통 실패 payload를 사용하고 status/decision/reason_codes를 같이 기록한다.

## 검증 커맨드
pytest scripts/test_omc_autopilot_pipeline.py -q -k 'task_retry_block_without_reason_code or task_block_without_reason_code'
