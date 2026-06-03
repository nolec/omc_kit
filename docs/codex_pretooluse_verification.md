# Codex PreToolUse 수동 검증 체크리스트

> DoD [B]: 이 문서를 Codex CLI에서 실행한 뒤 결과를 `codex_pretooluse_result.md`에 기록합니다.

## 사전 조건

- [ ] `omc_kit` (또는 OMC 설치 프로젝트) 루트에서 Codex 실행
- [ ] `python3 scripts/omc_pipeline_guard.py contract-done` **미실행** (pending 세션)
- [ ] `.codex/hooks.json`에 `OMC_BLOCK_EXIT` 없음, `matcher: "Bash"` 확인

```bash
grep -E "OMC_BLOCK_EXIT|matcher|PreToolUse" .codex/hooks.json
python3 scripts/test_codex_hooks_config.py
```

## 체크리스트

### 1. Bash + 차단 시도

| # | 단계 | 기대 | 결과 (PASS/FAIL) | 비고 |
|---|------|------|------------------|------|
| 1 | Codex에서 민감 경로 셸 수정 시도 (예: `echo x >> scripts/omc_context.py`) | PreToolUse 훅 발화 | | |
| 2 | stderr에 `[OMC BLOCK]` 또는 파이프라인 메시지 | exit 2 차단 | | |
| 3 | Codex가 명령을 실행하지 않음 | 차단 성공 | | |

### 2. apply_patch / 파일 편집 (플랫폼 갭 확인)

| # | 단계 | 기대 | 결과 (PASS/FAIL/SKIP) | 비고 |
|---|------|------|----------------------|------|
| 4 | CONTRACT 없이 새 파일 생성 요청 | 훅 발화 여부 관찰 | | 미발화면 SKIP |
| 5 | 훅 발화 시 exit 2 차단 | 차단 | | |
| 6 | 훅 미발화 | pre-commit만 방어 — AGENTS **(부분 검증: Bash)** | | |

### 3. stdin 샘플 (선택)

| # | 단계 | 결과 |
|---|------|------|
| 7 | 훅 로그/stdin JSON 캡처 | `codex_pretooluse_samples.json`에 저장 |

## 판정

- 1~3 PASS → Bash PreToolUse **검증됨**
- 4~5 FAIL(미발화) → 파일 편집은 **부분 검증**, `(미검증)` → `(부분 검증: Bash)` 로 AGENTS 갱신
- 전체 미실행 → `(미검증)` 유지

## AGENTS.md 갱신 (태스크 7)

수동 검증 완료 후에만 `AGENTS.md` Codex 행의 `(미검증)` 문구를 위 판정에 맞게 수정합니다.
