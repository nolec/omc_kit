# resume 테스트는 --mode full 명시로 LITE 자동 분기 방지
날짜: 2026-05-26
태그: --mode full 명시 또는 FULL 분기 조건(50자 초과 지시문 + feat/ 브랜치)으로 강제

## 증상
(생략)

## 원인
(생략)

## 적용된 규칙
resume 테스트에서 plan=completed 상태를 주고 --mode 없이 실행하면

## 검증 커맨드
branch+지시문 기준 자동으로 LITE 분기되어 FULL skip 로직이 실행 안 됨
