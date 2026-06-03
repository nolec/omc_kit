# Codex PreToolUse 수동 검증 결과

| 필드 | 값 |
|------|-----|
| 날짜 | 2026-06-04 |
| Codex 버전 | GPT-5.4 |
| 프로젝트 경로 | omc_kit |
| 검증자 | 사용자 |

## tool별 훅 발화 매트릭스

| tool_name | 훅 발화 | exit 2 차단 | 비고 |
|-----------|---------|-------------|------|
| apply_patch / 파일 편집 (1차) | **아니오** | **FAIL** | matcher `Bash`만, `# codex-hook-test` 추가됨 |
| apply_patch / 파일 편집 (2차) | **아니오** | **FAIL** | matcher `Bash\|apply_patch\|Write` 후에도 `# codex-hook-test-2` 추가됨 (+2 -1) |
| Bash | 미실행 | — | — |

## 최종 판정

- [ ] 검증됨 (Bash+파일 편집)
- [x] **확정: Codex 파일 편집 — PreToolUse 훅 미발화 (플랫폼/도구 경로)**
- [ ] 미검증

설정·스크립트·테스트는 정상. **Codex CLI가 파일 수정 시 PreToolUse를 호출하지 않거나, 호출해도 차단에 연결되지 않음.**

## Codex 파일 수정 방어 (운영 정책)

| 층 | 역할 |
|---|---|
| PreToolUse | Bash만 matcher 대상 (파일 편집 **비적용 확인**) |
| pre-commit | `omc_tdd_check.py` — **주 방어선** |
| AGENTS.md / SessionStart | 프롬프트 보조 |

## stderr 샘플

```
(1차·2차 모두 차단 없음)
```

## 스크립트 단독 검증 (참고)

로컬 stdin 시뮬레이션에서는 `apply_patch` + `scripts/` → exit 2 정상.
→ **OMC 설정 문제 아님, Codex 런타임 훅 미연동.**
