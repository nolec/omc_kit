# autopilot-5-reliability-fixes-pattern
날짜: 2026-05-26
태그: autopilot,reliability,tdd,critique-loop,ambiguous-response

## 증상
critique 루프가 retry_exhausted로 끝남, task verdict 미감지로 흐름 중단, 브랜치 충돌 시 crash, 상태 파일 오염, 이력 없음

## 원인
동일 프롬프트 재전송(같은 입력=같은 출력), verdict 파싱이 None 케이스 미처리, 브랜치 충돌 회복 경로 없음, RESULT_PATH 하드코딩, _save_pipeline_result가 latest만 저장

## 적용된 규칙
재시도 시 _build_retry_prompt로 직전 verdict 컨텍스트 주입 / _grep_verdict None → ambiguous 재시도 1회 / _checkout_new_branch suffix 재시도 / _get_result_path 환경변수 오버라이드 / _save_pipeline_result runs/{run_id} 원자적 저장

## 검증 커맨드
pytest scripts/test_omc_autopilot_pipeline.py -v
