# 상태 write 함수 테스트엔 stale 초기값 전제 조건 필수
날짜: 2026-06-08
태그: tdd,state,test-design,pipeline-guard

## 증상
cmd_contract_done에서 latest.json 없으면 이전 session_id가 지워지지 않고 남음. omc_kit 테스트에선 놓치고 okx 프로젝트에서 발견.

## 원인
_load_state가 기존 pipeline_session을 로드할 수 있는데, 테스트가 항상 빈 초기 상태만 가정했음. stale 값 존재 + 읽기 실패 케이스를 테스트하지 않음.

## 적용된 규칙
상태를 write하는 함수 테스트는 (1)초기 빈 상태, (2)stale 값+정상 입력, (3)stale 값+입력 실패 세 케이스를 모두 커버해야 한다. 특히 try/except pass 블록이 있으면 실패 경로 테스트 필수.

## 검증 커맨드
(생략)
