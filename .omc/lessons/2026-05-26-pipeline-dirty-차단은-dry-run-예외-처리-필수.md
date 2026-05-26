# pipeline dirty 차단은 dry-run 예외 처리 필수
날짜: 2026-05-26
태그: python3 -m pytest scripts/test_omc_autopilot_preflight.py -k dirty

## 증상
(생략)

## 원인
(생략)

## 적용된 규칙
dirty 워크트리 차단 로직을 dry_run 여부 무관하게 적용하면 기존 test_uncommitted_change_shows_warning 깨짐

## 검증 커맨드
not allow_dirty and not dry_run 조건으로 dry-run은 항상 경고만 하도록 예외 처리
