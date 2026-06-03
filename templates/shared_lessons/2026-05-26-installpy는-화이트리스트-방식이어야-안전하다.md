# install.py는 화이트리스트 방식이어야 안전하다
날짜: 2026-05-26
태그: install,whitelist,deploy,tdd

## 증상
블랙리스트 방식으로 conftest.py·test_*.py 24개가 타겟 프로젝트에 배포됨

## 원인
_SCRIPTS_EXCLUDE에 test_*.py·conftest.py 미포함. 새 파일 추가 시 자동 배포되는 구조

## 적용된 규칙
install.py scripts 복사는 화이트리스트(omc_*.py + 명시 목록)만 배포. 새 파일은 기본 제외. 배포 추가 시 _SCRIPTS_EXTRA에 명시

## 검증 커맨드
python3 -m pytest scripts/test_install_whitelist.py — 4개 테스트 GREEN 확인
