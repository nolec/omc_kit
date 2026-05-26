# omc_autopilot이 omc_exec CLI와 불일치하면 실행 시 crash
날짜: 2026-05-26
태그: omc_exec,interface,cli,autopilot,sync

## 증상
omc_autopilot.py가 --prompt/--headless/--timeout 플래그를 쓰지만 omc_exec.py는 --prompt-file/--execution-mode/--timeout-sec를 요구

## 원인
sixshop에서 omc_exec.py 인터페이스가 업그레이드됐으나 omc_kit의 omc_autopilot.py에 역반영되지 않음. install --force 동기화가 omc_autopilot만 덮었고 omc_exec도 같이 확인하지 않음

## 적용된 규칙
omc_exec.py CLI 변경 시 omc_autopilot.py의 cmd 빌드도 함께 수정. test_omc_exec_interface.py로 인터페이스 정합성 자동 검증

## 검증 커맨드
python3 -m pytest scripts/test_omc_exec_interface.py — 2 passed
