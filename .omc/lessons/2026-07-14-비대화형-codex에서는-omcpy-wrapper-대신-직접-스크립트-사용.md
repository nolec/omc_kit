# 비대화형 Codex에서는 omc.py wrapper 대신 직접 스크립트 사용
날짜: 2026-07-14
태그: codex,wrapper,noninteractive,workflow

## 증상
비대화형 Codex에서 python3 scripts/omc.py autopilot overview 또는 자연어 wrapper를 실행하면 역할 입력 대기 세션이 생성되고 원래 검증 흐름이 중단된다.

## 원인
omc.py wrapper가 자연어 요청의 역할 추천과 확인 입력을 거치도록 설계되어 stdin이 없는 환경에서도 waiting_input 세션을 만든다.

## 적용된 규칙
Codex 자동 검증에서는 python3 scripts/omc_autopilot.py overview 같은 직접 진입점을 사용하고, omc.py 자연어 wrapper는 대화형 입력이 가능한 경우에만 사용한다.

## 검증 커맨드
python3 scripts/omc_autopilot.py overview 실행 결과와 python3 scripts/omc.py state status --target .를 순차 확인해 새 waiting_input 세션이 생성되지 않는지 확인한다.
