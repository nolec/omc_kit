# P1: allow --reason 필수 + CONTRACT 스킬 체크박스
날짜: 2026-05-18
태그: allow,reason,audit,contract,p1

## 증상
--reason 없이 allow 사용 시 경고만 출력하고 통과 — 이유 없는 예외가 감사 로그에 기록되어 의미 없음

## 원인
cmd_allow에서 빈 reason을 경고 + 이유 미기재 문자열로 대체하고 허용했음

## 적용된 규칙
reason.strip() 이 빈 문자열이면 exit 1 차단. CONTRACT 양식에 적용 스킬 체크박스 추가

## 검증 커맨드
python3 scripts/omc_pipeline_guard.py allow src/Foo.tsx → exit 1
