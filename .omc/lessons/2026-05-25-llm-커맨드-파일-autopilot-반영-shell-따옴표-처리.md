# LLM 커맨드 파일 autopilot 반영 — shell 따옴표 처리
날짜: 2026-05-25
태그: python3 -m pytest scripts/test_llm_autopilot_commands.py

## 증상
(생략)

## 원인
(생략)

## 적용된 규칙
--instruction $ARGUMENTS 따옴표 누락 시 공백 포함 지시문 split 오류

## 검증 커맨드
\"$ARGUMENTS\" 로 따옴표 감싸기. test_claude_autopilot_quoted_arguments 테스트 추가.
