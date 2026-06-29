# task_retry e2e 회귀는 status와 exit code를 함께 본다
날짜: 2026-06-30
태그: task,retry,resume,e2e

## 증상
task_retry BLOCK 경로는 payload가 failed여도 파이프라인 종료 코드는 retry_exhausted 규약을 따를 수 있었다.

## 원인
상태 기록은 실패 payload helper가 담당하고, 최종 종료 코드는 retry_exhausted save()가 담당하는 경로가 분리돼 있었다.

## 적용된 규칙
e2e 테스트에서 step status와 pipeline status를 둘 다 검증한다.

## 검증 커맨드
pytest scripts/test_omc_autopilot_pipeline.py -q -k 'task_retry_block_without_reason_code_is_failed_in_pipeline'
