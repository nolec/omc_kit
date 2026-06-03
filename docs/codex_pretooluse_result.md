# Codex PreToolUse 수동 검증 결과

| 필드 | 값 |
|------|-----|
| 날짜 | 2026-06-04 |
| Codex 버전 | GPT-5.4 (스크린샷 기준, 5.5→5.4 전환 알림) |
| 프로젝트 경로 | omc_kit |
| 검증자 | 사용자 |

## tool별 훅 발화 매트릭스

| tool_name | 훅 발화 | exit 2 차단 | 비고 |
|-----------|---------|-------------|------|
| apply_patch / 파일 편집 | **아니오** | **FAIL** | `omc_context.py`에 `# codex-hook-test` 추가됨 (+1 -0), 차단 메시지 없음 |
| Bash | 미실행 | — | 2차 테스트 생략 |

## 최종 판정

- [ ] 검증됨 (Bash+파일 편집)
- [x] **부분 검증: PreToolUse는 matcher Bash만 — 파일 편집(apply_patch) 훅 미발화**
- [ ] 미검증 (실패 또는 미실행)

## stderr 샘플 (차단 시)

```
(차단 없음 — 훅 미발화 또는 미매칭으로 추정)
```

## 원인 추정

1. `.codex/hooks.json` PreToolUse `matcher: "Bash"` → 파일 편집 tool은 훅 대상 아님
2. Codex 플랫폼: `apply_patch` PreToolUse 커버리지 불안정 (공식/커뮤니티 이슈)
3. 파일 수정 방어: **pre-commit** + SessionStart 프롬프트에 의존

## 재검증 (2026-06-04 apply_patch matcher 추가 후)

설정: `matcher: "Bash|apply_patch|Write"`, 스크립트 `apply_patch` Write 분기 추가됨.

Codex에서 **동일 프롬프트**로 다시 시도:

```text
scripts/omc_context.py 파일 맨 아래에 주석 # codex-hook-test-2 한 줄만 추가해줘.
CONTRACT나 pipeline guard 등록은 하지 마.
```

| 재시도 | 훅 발화 | 차단 | 비고 |
|--------|---------|------|------|
| | | | |

## 후속

- 재검증 PASS → AGENTS 「검증됨」 갱신
- 재검증 FAIL → Codex 플랫폼이 apply_patch에 훅 미호출 (pre-commit만 방어)
