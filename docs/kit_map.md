# OMC Kit Map

## 목적

이 문서는 OMC/멀티비서 킷을 다른 프로젝트로 옮길 때 무엇을 가져가야 하는지 빠르게 판단하기 위한 공통 지도입니다.

## SSOT

공통 킷의 SSOT는 `omc_kit/` 입니다.

**모든 변경은 `omc_kit/scripts/` 또는 `omc_kit/templates/`에 먼저 적용 후 `install.py --force`로 라이브에 동기화합니다.**

## 다음 프로젝트로 가져갈 최소 묶음

필수:
- `omc_kit/`  (이 하나가 전부입니다)

선택:
- `AGENTS.md` (강권 — 세션 시작 훅이 자동 생성하지만 커스터마이즈 가능)
- `CLAUDE.md` (Claude Code 오버레이)
- `GEMINI.md` (Gemini CLI 오버레이)
- `project_prompts/` (도메인 role/profile 오버레이)

가져가지 않아도 되는 것:
- 현재 프로젝트 도메인 전용 docs
- 루트의 중복 설명 문서

## 레이어별 역할

```
omc_kit/
  scripts/          ← SSOT (18개 스크립트)
  templates/        ← SSOT 템플릿 (install.py가 배포)
    AGENTS.md         공통 에이전트 규칙 (모든 LLM)
    CLAUDE.md         Claude Code 오버레이
    GEMINI.md         Gemini CLI 오버레이
    .claude/commands/ Claude Code 슬래시 커맨드 (개별 md 파일)
    .gemini/commands/ Gemini CLI 커맨드 참조 (omc-commands.md)
    .codex/commands/  Codex CLI 커맨드 참조 (/project: 네임스페이스)
    .cursor/rules/    Cursor 규칙 (omc-always.md)
    .agent-hooks/     공용 훅 스크립트 (Claude/Codex/Gemini 공통)
      omc-pipeline-check.sh   파일 수정 전 세션/TDD 파이프라인 차단
      omc-prompt-inject.sh    UserPromptSubmit — BM25 교훈 주입 + 모호 메시지 감지
      omc-post-file-check.sh  PostToolUse — 세션 미확인 파일 수정 경고 (소프트 가드)
      omc-session-start.sh    세션 시작 훅
      omc-session-end.sh      세션 종료 훅
    .agents/skills/   Codex + Antigravity Agent Skills (SSOT — plural)
    .agent/           Antigravity IDE 전용
      workflows/        명시적 슬래시 커맨드 (/plan, /task 등)
      rules/            항상 적용 규칙 (omc-always.md)
      skills/           암묵적 트리거 스킬 (.agents/skills/ 의 미러)
  docs/             ← 사용 가이드
  prompts/          ← 역할/모드 프롬프트

AGENTS.md           ← 공통 에이전트 규칙 (모든 LLM)
CLAUDE.md           ← Claude Code 오버레이 (선택)
GEMINI.md           ← Gemini CLI 오버레이 (선택)
project_prompts/    ← 현재 프로젝트 도메인 role/profile (선택)
.omc/               ← 런타임 상태 (세션, 교훈, 비용, autopilot 기록)
```

## 핵심 스크립트 목록 (SSOT)

| 스크립트 | 역할 |
|---|---|
| `omc.py` | 단일 진입점 |
| `omc_state.py` | 세션 상태 R/W + compact |
| `omc_guard.py` | 미확정 세션 실행 차단 |
| `omc_hooks.py` | 세션 라이프사이클 훅 |
| `omc_context.py` | 세션 컨텍스트 수집 (git + BM25 교훈) |
| `omc_lesson.py` | 교훈 CRUD + BM25 검색 |
| `omc_cost.py` | LLM 비용 추적 (토큰 파서) |
| `omc_autopilot.py` | 멀티 LLM 자율 루프 (태스크 파일 기반) |
| `omc_role_suggest.py` | 역할 자동 추천 |
| `omc_pipeline_guard.py` | TDD 파이프라인 게이트 |
| `omc_tdd_check.py` | staged 파일 TDD 체크 |
| `omc_exec.py` | LLM CLI 실행 어댑터 |
| `omc_chat.py` | 자연어 → OMC 실행 라우팅 |
| `omc_peer_review.py` | 피어 리뷰 자동화 |
| `omc_doctor.py` | 설치 상태 진단 + 자동 수정 |
| `omc_utils.py` | 공통 유틸 (project_root) |
| `install.py` | 다른 프로젝트로 배포 |
| `auto_prompt.py` | 프롬프트 합성 |

## 실무 규칙

공통 로직을 고칠 때:
- `omc_kit/scripts/` 또는 `templates/`를 수정
- `install.py --target . --force`로 동기화
- `omc_doctor.py --target .`로 검증

현재 프로젝트만 다르게 하고 싶을 때:
- `project_prompts/` 또는 `.omc/policy.json`을 수정

프로젝트 추가할 때:
- `python omc_kit/scripts/omc.py setup --target /path/to/new-project`

## CI/CD

`.github/workflows/omc-ci.yml`이 설치된 레포에서는 아래 검사를 자동화할 수 있습니다.

- **ubuntu**: 셸 독립 단위 테스트 + `omc_tdd_check.py --run-tests` (TDD 게이트)
- **macOS**: 셸 의존 훅 테스트 (`omc-pipeline-check.sh`, `omc-post-file-check.sh` 등)
