# 명시적 preserve는 역할별 기본 pending 처리보다 우선한다
날짜: 2026-10-06
태그: state,preserve,completion,regression

## 증상
directive local-commit preserve 세션이 기존 pending completion을 삭제했다

## 원인
생성 단계는 preserve를 검증했지만 동기화 단계는 역할과 세션 이름만으로 보존 여부를 판단했다

## 적용된 규칙
검증된 명시적 preserve와 preserve-if-present는 비계보 세션에서 기존 pending bytes를 유지하며 완료 receipt는 원래 구현 세션에 결속한다

## 검증 커맨드
python3 -m pytest scripts/test_omc_guard.py -q
