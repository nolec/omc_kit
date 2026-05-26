# critique None(미감지) streak은 rc=0 한정으로만 카운트해야 인프라 오류를 오분류하지 않는다
날짜: 2026-05-26
태그: autopilot,critique,none_verdict,streak

## 증상
executor 네트워크 오류(rc≠0) 도 None streak에 포함되면 task_retry가 코드 수정 목적으로 잘못 투입됨

## 원인
기존 None streak 조건이 rc를 체크하지 않아 인프라 오류와 AMBIGUOUS 응답을 구분 못 함

## 적용된 규칙
None streak 조건: rc == 0 and verdict is None and prev_verdict is None

## 검증 커맨드
python3 -m pytest scripts/test_omc_critique_recovery.py::test_critique_none_verdict_streak_triggers_task_retry -q
