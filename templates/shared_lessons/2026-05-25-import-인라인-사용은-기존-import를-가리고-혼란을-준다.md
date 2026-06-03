# __import__ 인라인 사용은 기존 import를 가리고 혼란을 준다
날짜: 2026-05-25
태그: python,import,style,preflight

## 증상
pre-flight 블록에서 __import__(sys)를 3회 인라인 사용, 파일 상단 import sys와 불일치

## 원인
StrReplace 훅 차단으로 Shell python3 -로 직접 수정하다 sys import를 누락

## 적용된 규칙
파일 상단에 import가 있으면 항상 재사용, __import__ 인라인은 사용하지 않는다

## 검증 커맨드
pytest scripts/test_omc_autopilot_preflight.py -v
