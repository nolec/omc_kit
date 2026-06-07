# Codex PreToolUse 차단은 exit 2 (exit 1은 비차단)
날짜: 2026-06-04
태그: codex, hooks

## 증상
`.codex/hooks.json`에 `OMC_BLOCK_EXIT=1`을 설정했으나 Codex에서 파일/셸 차단이 기대대로 동작하지 않을 수 있음

## 원인
OpenAI Codex 공식: **exit 2만 차단**, exit 1은 비차단(도구 호출 계속). Claude Code와 동일 exit 2 사용이 맞음.
PreToolUse는 **Bash는 비교적 안정**, `apply_patch`/파일 편집 훅은 플랫폼 갭 가능 — 별도 수동 검증 필요

## 적용된 규칙
- Codex PreToolUse: `omc-pipeline-check.sh` 직접 호출 (OMC_BLOCK_EXIT 제거), matcher `Bash` 1차
- AGENTS.md `(미검증)` 제거는 `docs/codex_pretooluse_result.md` 수동 검증 기록 후에만
- apply_patch 미발화 시 AGENTS에 **(부분 검증: Bash)** 로 표기

## 검증 커맨드
```bash
python3 scripts/test_codex_hooks_config.py
# 수동: docs/codex_pretooluse_verification.md 체크리스트 실행 후 result.md 기록
```
