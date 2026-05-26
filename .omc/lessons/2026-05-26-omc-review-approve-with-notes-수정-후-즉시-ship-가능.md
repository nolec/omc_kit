# omc-review APPROVE WITH NOTES → 수정 후 즉시 ship 가능
날짜: 2026-05-26
태그: review,ship,technical-debt,process

## 증상
review 경미 이슈를 수정하지 않고 넘어가려는 유혹

## 원인
경미 이슈도 누적되면 기술 부채가 됨

## 적용된 규칙
APPROVE WITH NOTES 판정 시 경미 이슈는 ship 전에 바로 수정한다. 별도 태스크로 미루지 않는다.

## 검증 커맨드
git log --oneline -3
