# install.py 수동 등록 누락 패턴

## 발생 상황
templates/에 새 파일(ETHOS.md)을 추가했지만 install.py의 복사 목록에 수동으로 추가하지 않아서
다른 프로젝트에 설치해도 해당 파일이 복사되지 않는 버그 발생.

## 근본 원인
install.py가 파일 목록을 하드코딩으로 관리 → 새 파일 추가 시 install.py도 동시에 수정해야 하는
2중 등록 의무가 있었으나 파일 생성에만 집중하고 install.py를 빠뜨림.

## 해결
templates/ 루트의 .md 파일을 자동으로 감지해서 복사하도록 변경.
마커 기반 병합이 필요한 파일(AGENTS.md 등)은 _MERGE_MARKERS dict로 별도 관리.
나머지 .md는 auto-glob으로 자동 복사 → 수동 등록 불필요.

## 규칙
templates/에 새 파일 추가 후 "install.py에도 등록했는가?" 체크 대신
→ 자동 감지 구조로 만들어서 체크 자체를 없애라.

## 적용 날짜
$(date +%Y-%m-%d)
