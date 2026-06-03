# deployed vs templates 두 경로를 항상 함께 동기화해야 함
날짜: 2026-05-26
태그: docs,sync,templates,deployed,llm-commands

## 증상
.claude/commands/autopilot.md가 deployed에 없어 LLM이 새 기능을 모름

## 원인
templates만 수정하고 deployed 동기화를 누락하는 패턴 반복

## 적용된 규칙
templates 파일 수정 시 shutil.copy2로 deployed 동기화를 같은 커밋에 포함. 테스트에서 deployed 경로도 검증.

## 검증 커맨드
pytest scripts/test_llm_autopilot_commands.py -k deployed
