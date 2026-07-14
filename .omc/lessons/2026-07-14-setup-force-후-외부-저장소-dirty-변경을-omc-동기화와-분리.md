# setup force 후 외부 저장소 dirty 변경을 OMC 동기화와 분리
날짜: 2026-07-14
태그: setup,install,git,scope,workflow

## 증상
여러 사용처에 setup --force를 실행하면 OMC 동기화 변경과 기존 애플리케이션 dirty 변경이 함께 보여 커밋 범위를 판단하기 어려워진다.

## 원인
setup --force는 대상 저장소의 설치 표면을 갱신하지만 실행 전후 저장소별 변경 스냅샷을 남기지 않으면 기존 변경과 설치 변경을 구분할 수 없다.

## 적용된 규칙
setup --force 전에 각 저장소의 git status --short를 저장하고, 사용처별로 순차 설치·audit·diff를 수행한 뒤 예상된 OMC 파일만 별도 커밋하며 blanket git add를 금지한다.

## 검증 커맨드
각 대상에 대해 setup 전후 git status --short와 python3 scripts/omc_install_audit.py <target> 결과를 비교한다.
