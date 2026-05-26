# critique BLOCK은 streak 없이 즉시 에스컬레이션해야 복구 경로에 도달한다
날짜: 2026-05-26
태그: autopilot,critique,block_verdict,immediate

## 증상
BLOCK 후 REVISE 등 다른 verdict가 나오면 streak이 리셋돼 task_retry 복구 경로에 영원히 도달 못 함

## 원인
BLOCK은 코드 불가 선언이므로 재시도가 무의미. streak 기반이면 탈출 불가 케이스가 존재

## 적용된 규칙
verdict == BLOCK → 즉시 failed_critique_loop 저장 + same_verdict_streak = _PIPELINE_MAX_SAME_VERDICT

## 검증 커맨드
python3 -m pytest scripts/test_omc_critique_recovery.py::test_critique_block_verdict_immediate_task_retry -q
