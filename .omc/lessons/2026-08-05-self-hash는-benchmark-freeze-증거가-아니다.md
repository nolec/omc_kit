# self-hash는 benchmark freeze 증거가 아니다
날짜: 2026-08-05
태그: benchmark,provenance,hash,plan

## 증상
corpus 내용을 바꾸고 내부 digest를 재계산하면 preregistration validator가 승인했다

## 원인
검증 기준이 입력 파일 내부의 self-reported hash뿐이었다

## 적용된 규칙
benchmark corpus는 내부 무결성 hash와 별도로 코드 상수 또는 서명 manifest의 외부 anchor와 일치해야 한다

## 검증 커맨드
pytest scripts/test_omc_plan_context_selection.py -q -k retrieval_development
