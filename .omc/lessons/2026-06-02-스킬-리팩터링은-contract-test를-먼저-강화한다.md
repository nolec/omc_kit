# 스킬 리팩터링은 contract test를 먼저 강화한다
날짜: 2026-06-02
태그: skills,tdd,contract,workflow

## 증상
스킬 본문을 먼저 줄이면 어떤 마커를 살려야 하는지 뒤늦게 드러나 재수정과 REVISE 루프가 늘어났다.

## 원인
리팩터링 전에 출력 계약과 필수 마커를 테스트로 잠그지 않아, 축약 과정에서 의미 보존 기준이 흔들렸다.

## 적용된 규칙
스킬 리팩터링을 시작할 때는 본문 수정 전에 contract test에 길이 상한, focus marker, 핵심 순서를 먼저 추가하고 RED를 확인한 뒤 본문을 줄인다.

## 검증 커맨드
python3 -m pytest -q scripts/test_omc_*_skill_contract.py
