# pipeline-status: rich 선택 의존성 + plain-text fallback 패턴
날짜: 2026-05-26
태그: python,cli,rich,fallback

## 증상
rich 미설치 환경에서 ImportError 터짐

## 원인
rich를 하드 의존성으로 사용했을 때 설치 안 된 환경에서 실패

## 적용된 규칙
try/except ImportError로 선택적 사용, 공통 로직은 헬퍼 함수로 추출해 두 경로 중복 제거

## 검증 커맨드
pytest scripts/test_omc_autopilot_pipeline.py -k pipeline_status
