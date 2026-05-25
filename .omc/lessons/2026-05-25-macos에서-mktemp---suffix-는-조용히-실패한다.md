# macOS에서 mktemp --suffix 는 조용히 실패한다
날짜: 2026-05-25
태그: macos,mktemp,install,hooks,compatibility

## 증상
GNU mktemp --suffix 옵션이 macOS BSD mktemp에서 지원 안 됨 → 훅 비정상 종료 → Cursor/Claude가 모든 파일 수정을 차단하는 악순환. install.py _check_force_regression()의 EOFError→'n' 처리가 non-interactive 환경에서 설치를 통째로 중단시킴.

## 원인
SSOT(templates/)와 live 파일 간 동기화 후 doctor에 감지 항목이 없어 회귀를 알 수 없었음. GNU/macOS 차이를 테스트로 커버하지 않았음.

## 적용된 규칙
새 .sh 훅은 mktemp를 항상 POSIX 호환 방식(-t prefix.XXXXXX)으로 작성한다. install.py input()은 sys.stdin.isatty() 분기로 non-interactive fallback을 제공한다.

## 검증 커맨드
python3 -m pytest scripts/test_omc_macos_compat.py -v
