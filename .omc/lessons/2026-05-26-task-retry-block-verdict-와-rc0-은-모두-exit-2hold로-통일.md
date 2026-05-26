# task_retry BLOCK verdict 와 rc≠0 은 모두 exit 2(hold)로 통일해야 한다
날짜: 2026-05-26
태그: autopilot,task_retry,exit_code,hold

## 증상
task_retry rc≠0 시 return 1 → CI가 failed 로 오분류, BLOCK verdict 시 처리 없이 critique 재진입

## 원인
exit code 1(failed)과 exit code 2(hold)가 혼재. BLOCK verdict 검사 누락.

## 적용된 규칙
task_retry 이후 rc≠0 또는 VERDICT:BLOCK 이면 save('hold') + return 2 로 통일

## 검증 커맨드
python3 -m pytest scripts/test_omc_critique_recovery.py::test_task_retry_block_verdict_exits_hold scripts/test_omc_critique_recovery.py::test_task_retry_rc_nonzero_exits_hold_with_code_2 -q
