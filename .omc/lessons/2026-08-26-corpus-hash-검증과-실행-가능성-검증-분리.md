# corpus hash 검증과 실행 가능성 검증 분리
날짜: 2026-08-26
태그: benchmark,corpus,provenance,preflight

## 증상
Product Value corpus의 hash와 Git provenance는 통과했지만 frozen source-e에 packet 검증 경로가 없어 실행이 실패했다.

## 원인
corpus 생성기가 metadata binding만 확인하고 workload scope와 verification command의 시작 snapshot 실행 가능성을 점검하지 않았다.

## 적용된 규칙
corpus를 승인하기 전 frozen snapshot에서 packet 검증 argv의 경로 존재와 명령 실행 결과를 확인하고, 의도적 RED라면 expected preflight 상태를 별도 계약으로 고정한다.

## 검증 커맨드
frozen source별 packet verification argv 실행
