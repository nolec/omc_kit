# B+C: session-start CONTRACT 초기화 + GREEN 먼저 경고
날짜: 2026-05-18
태그: session,contract,green-first,tdd,p-improvement

## 증상
세션이 바뀌어도 contract_confirmed가 초기화되지 않아 이전 세션 CONTRACT가 유효하게 남음. RED 없이 대량 구현 시 경고 없음

## 원인
omc_context.py session_start 훅에 pipeline_guard 연동 없음. cmd_check에 줄 수 기반 경고 없음

## 적용된 규칙
omc_context.py 실행 시 cmd_session_start 호출 → contract_confirmed=False 초기화. 50줄 이상 구현 파일에 RED 없으면 GREEN 먼저? 경고 출력

## 검증 커맨드
python3 scripts/omc_context.py --target . | grep PIPELINE && python3 scripts/omc_pipeline_guard.py status | grep CONTRACT
