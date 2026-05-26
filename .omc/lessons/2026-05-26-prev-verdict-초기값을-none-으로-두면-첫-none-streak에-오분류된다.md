# prev_verdict 초기값을 None 으로 두면 첫 None streak에 오분류된다 — sentinel 객체 사용
날짜: 2026-05-26
태그: autopilot,critique,sentinel,prev_verdict,none_streak

## 증상
critique 루프 진입 직후 첫 verdict=None 이 나오면 prev_verdict(초기 None)와 같아 streak=1 발동

## 원인
prev_verdict=None 초기화가 실제 None verdict 와 구분 불가

## 적용된 규칙
_UNSET_VERDICT = object() 로 초기화. 루프 재진입 시에도 _UNSET_VERDICT 로 재설정.

## 검증 커맨드
python3 -m pytest scripts/test_omc_critique_recovery.py::test_critique_first_none_does_not_trigger_streak -q
