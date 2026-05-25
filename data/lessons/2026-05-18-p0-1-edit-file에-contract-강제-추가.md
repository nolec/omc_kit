# P0-1: edit_file에 CONTRACT 강제 추가
날짜: 2026-05-18
태그: omc,tdd,contract,pipeline-guard,p0,edit_file

## 증상
기존 파일 수정(edit_file) 시 CONTRACT 확인 없이 바로 구현이 진행됨

## 원인
beforeToolCall 훅이 edit_file은 민감 경로일 때만 검사하고, 일반 소스 파일 수정은 통과시킴

## 적용된 규칙
edit_file 호출 시 omc_pipeline_guard.py check-edit 실행. contract_confirmed=True여야만 허용. CONTRACT 완료 후 contract-done 명령 실행 필수.

## 검증 커맨드
python3 -m pytest scripts/test_omc_pipeline_guard_contract.py -v
