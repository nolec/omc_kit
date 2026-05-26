# watch 루프에서 KeyboardInterrupt로 SIGINT 처리
날짜: 2026-05-26
태그: python,cli,watch,sigint,keyboard-interrupt

## 증상
signal.signal() 없이 Ctrl+C 후 터미널 상태 복원 필요

## 원인
watch 루프 구현 시 SIGINT 핸들러를 별도로 등록하려 했으나 try/except KeyboardInterrupt가 더 단순하고 동일한 효과

## 적용된 규칙
while True 루프에서 Ctrl+C 처리는 try/except KeyboardInterrupt + 복원 메시지로 충분. signal.signal()은 daemon thread 등 복잡한 경우에만 사용

## 검증 커맨드
pytest scripts/test_omc_autopilot_pipeline.py -k watch
