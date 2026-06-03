## OMC Overlay For Claude/Codex

이 프로젝트는 공통 운영 규칙의 SSOT를 `AGENTS.md`로 둡니다.
이 파일은 Claude/Codex 계열 실행기용 **추가 안내**입니다 — AGENTS.md의 모든 규칙이 기본 적용됩니다.

> **모든 판단·추천·구현은 `ETHOS.md`의 원칙을 따릅니다.**
> 완성하라 / 찾아라 / 사람이 결정한다 / 지루한 것을 선택해라

## 세션 시작 시 자동 실행 (MANDATORY)

```bash
OMC_EXECUTOR=claude python3 scripts/omc.py state init --target . && \
OMC_EXECUTOR=claude python3 scripts/omc.py hook session_start --target .
```

> `.claude/settings.json`의 `SessionStart` 훅이 자동 처리합니다.
> `session_start` 훅에서 **세션 자동 confirm**과 **컨텍스트 수집**이 함께 실행됩니다.
> 훅 실행 여부가 불확실하면 수동으로 실행하세요.

## Claude Code 전용 물리적 차단 지점

| 단계 | 차단 메커니즘 |
|---|---|
| Write 전 | `PreToolUse` → `omc-pipeline-check.sh` → exit 2 |
| 커밋 전 | `pre-commit` → `omc_tdd_check.py --staged` → exit 1 |
| ship 전 | `omc_tdd_check.py --run-tests` → exit 1 |

## 슬래시 커맨드

### 코어 (매일 쓰는 것)

| 커맨드 | 용도 |
|--------|------|
| `/plan [작업]` | 구현 전 TDD 태스크 분해 |
| `/task [설명]` | 7단계 TDD 파이프라인 |
| `/review [대상]` | git diff 기반 코드 리뷰 |
| `/investigate [이슈]` | 4단계 방법론으로 버그 근본 원인 추적 |
| `/lesson [키워드]` | 교훈 추가/검색 (BM25 기반) |
| `/status` | OMC 상태 확인 |
| `/ship [대상]` | TDD 게이트 → 배포 |

### 선택 (필요할 때만)

| 커맨드 | 용도 |
|--------|------|
| `/brainstorm [주제]` | 요구사항이 모호할 때 소크라테스식 탐색 |
| `/office-hours [요청]` | 기능 방향이 맞는지 의심될 때 — 6개 강제 질문 |
| `/ceo-review [모드]` | 기능 범위 재검토 — CEO 관점 10가지 체크 |
| `/autopilot [태스크파일]` | 멀티 LLM 자율 루프 |
| `/retro [기간]` | 주기적 회고 |

권장 순서 (코어만): `/plan` → `/task` → `/review` → `/ship`

세부 규칙 전체: **AGENTS.md** 참조

---

## ⛔ 스킬 완료 후 자동 진행 금지 (MANDATORY)

스킬이 완료되면 판정·결과를 출력한 뒤 다음 스킬로 자동 진입하지 않는다.
사용자가 "진행하자", "계속해", "응" 같은 짧은 승인을 해도
**명시적으로 다음 스킬 이름이 언급되지 않으면 자동 진입 금지.**

상세 규칙 및 금지 동작 목록: **AGENTS.md** `## ⛔ 스킬 완료 후 자동 진행 금지` 참조
