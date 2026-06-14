# 세션 마무리 후 stale current_request는 status로 즉시 확인한다
날짜: 2026-06-14
태그: omc,state,notepad,stale,workflow

## 증상
git push와 최종 커밋까지 끝났는데도 .omc state/notepad의 current_request가 이전 작업을 active로 유지해 다음 세션 판단을 흐린다.

## 원인
작업 완료 후 git 기준 완료 여부만 보고 OMC state/notepad 재확인을 생략하면 latest session과 현재 작업 상태가 어긋난 채 남는다.

## 적용된 규칙
커밋 또는 push로 작업을 마무리한 직후에는 python3 scripts/omc.py state status --target . 와 cat .omc/notepad.md 로 latest/current_request를 함께 확인하고, 불일치가 보이면 다음 작업 전에 omc-status로 기준을 다시 맞춘다.

## 검증 커맨드
python3 scripts/omc.py state status --target . && cat .omc/notepad.md
