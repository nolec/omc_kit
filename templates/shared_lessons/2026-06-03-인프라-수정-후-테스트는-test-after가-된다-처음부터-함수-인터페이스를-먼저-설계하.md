# 인프라 수정 후 테스트는 test-after가 된다 — 처음부터 함수 인터페이스를 먼저 설계하라
날짜: 2026-06-03
태그: tdd,install,test-after,infra

## 증상
install.py _check_force_regression 구현 후 테스트를 추가하니 RED 단계가 성립하지 않아 red-done을 형식적으로 등록

## 원인
인프라 스크립트를 먼저 구현하고 나중에 테스트 커버리지를 채우는 순서로 진행함

## 적용된 규칙
분기가 많은 함수(인프라 포함)는 구현 전 입출력 인터페이스를 먼저 설계하면 TDD가 자연스럽게 적용된다

## 검증 커맨드
python3 scripts/test_install.py
