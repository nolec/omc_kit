# OMC — Orchestrated Multi-agent Craft

AI 보조 개발 환경을 위한 TDD 파이프라인 + 멀티 LLM 운영 킷입니다.

> ⚠️ **SSOT 규칙**: 스킬 파일은 반드시 `templates/.agents/skills/` 에서만 수정합니다.
> 루트 `.agents/skills/` 를 직접 생성·수정하지 않습니다 — `install.py` 실행 시 덮어써집니다.

## 무엇인가

- **TDD 파이프라인 강제**: RED → GREEN → REFACTOR 순서를 물리적 훅으로 강제
- **멀티 LLM 지원**: Cursor / Claude Code / Gemini CLI / Codex 동시 운용
- **모델별 사고 패턴**: 각 LLM에 최적화된 추론 지침 자동 주입
- **변경 범위 경고**: 큰 변경 전 자동 scope 분석 (차단 아님, 신호만)
- **세션 관리**: 작업 선언 → 파일 수정 → 완료 흐름으로 문맥 유지
- **모호 메시지 감지**: "응", "진행하자" 등 모호한 입력 시 다음 스킬 확인 질문 자동 주입 (`UserPromptSubmit` 훅)
- **GitHub Actions CI**: 모든 push/PR에서 TDD 게이트 + 훅 테스트 자동 실행 (ubuntu + macOS 매트릭스)

## 설치

```bash
git clone https://github.com/nolec/omc_kit.git
cd omc_kit
python3 scripts/install.py --target /path/to/your-project
# 설치 완료 후 omc_kit 폴더는 삭제해도 됩니다
cd .. && rm -rf omc_kit
```

`--force` 옵션으로 기존 파일 덮어쓰기 가능.

> **팁**: 여러 프로젝트에 설치할 때는 omc_kit을 로컬에 두고 반복 사용할 수 있습니다.
> `git pull` 로 최신 버전 유지 → `python3 scripts/install.py --target /other-project`

## 설치 후 설정

### 1. ETHOS.md 섹션 5 — 프로젝트 맥락

ETHOS.md 파일을 열어 섹션 5를 프로젝트에 맞게 채우세요:
스택, 디렉토리 구조, 패턴 우선순위, 금지 사항

### 2. CONVENTIONS.md — 코딩 컨벤션

CONVENTIONS.md를 열어 네이밍, 파일 구조, 금지 사항을 채우세요.

### 3. pre-commit 훅 설치

```bash
cp scripts/pre-commit.sample .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
# 또는:
python3 scripts/omc_doctor.py --fix
```

### 4. 동작 확인

```bash
python3 scripts/omc_doctor.py
# "OMC 설치 상태 이상 없음" 출력 확인
```

## 기본 워크플로우

```
1. 작업 선언      python3 scripts/omc.py "작업 내용"
2. 구현           (파일 수정 — beforeToolCall 훅이 자동 감시)
3. 커밋           git commit (pre-commit TDD 게이트 통과)
```

## LLM별 진입점

| LLM | 방식 |
|---|---|
| Cursor | `.cursor/rules/` 자동 적용 — 자연어 요청 |
| Claude Code | `.claude/commands/` — `/plan`, `/task`, `/review` |
| Gemini CLI | `.gemini/commands/` — `/plan`, `/task` |
| Codex | `.agents/skills/` — `$omc-plan`, `$omc-task` |

## 핵심 스크립트

| 스크립트 | 용도 |
|---|---|
| `scripts/omc.py` | 세션 관리 진입점 |
| `scripts/omc_state.py` | 세션 상태 R/W + `latest_skill` 저장 |
| `scripts/omc_pipeline_guard.py` | TDD 파이프라인 게이트 |
| `scripts/omc_tdd_check.py` | 테스트 커버리지 검사 |
| `scripts/omc_doctor.py` | 설치 상태 진단 |
| `scripts/omc_sync_ssot.py` | SSOT 동기화 확인 |
| `scripts/omc_lesson.py` | 교훈 저장/검색 |

## 최근 변경 (2026-06-08)

- **모호 메시지 감지** — `UserPromptSubmit` 훅(`omc-prompt-inject.sh`)이 "응", "진행하자" 등의 모호한 입력을 감지해 확인 질문을 자동 주입합니다. 직전 스킬명도 함께 표시해 맥락을 제공합니다.
- **`latest_skill` 저장** — `sync-session` 시 스킬 타이틀을 `latest.json`에 보관해 훅이 꺼낼 수 있게 했습니다.
- **Codex PostToolUse 소프트 가드** — 세션 미확인 상태에서 파일 수정 시 경고를 주입합니다 (차단 아님).
- **GitHub Actions CI** — ubuntu(단위 테스트) + macOS(훅 통합 테스트) 매트릭스로 모든 push/PR을 검증합니다.

## 디렉토리 구조

```
omc_kit/
├── scripts/          OMC 스크립트 전체 (프로젝트에 배포됨)
├── templates/
│   ├── AGENTS.md     공통 에이전트 규칙 (SSOT)
│   ├── CLAUDE.md     Claude 전용 오버레이 템플릿
│   ├── GEMINI.md     Gemini 전용 오버레이 템플릿
│   ├── CODEX.md      Codex/o-series 전용 패턴
│   ├── ETHOS.md      프로젝트 엔지니어링 원칙 (섹션 5는 직접 채움)
│   ├── CONVENTIONS.md 코딩 컨벤션 (직접 채움)
│   ├── .cursor/      Cursor rules + hooks
│   ├── .claude/      Claude Code 설정 + 개인 CLAUDE 오버레이
│   ├── .gemini/      Gemini CLI 설정 + 개인 GEMINI 오버레이
│   └── .agent-hooks/ 공통 hooks
└── docs/             문서
```
