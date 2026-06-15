# OMC Next Project Pack

## 가장 단순한 답

다음 프로젝트에 가져갈 것은 `omc_kit/` 하나입니다.

```bash
python omc_kit/scripts/omc.py setup --target /path/to/new-project
```

이 명령 하나로 스크립트 18개, 에이전트 규칙, 훅 설정, `.omc/` 초기 상태가 모두 설치됩니다.

## 설치 순서

```bash
# 1. 설치
python omc_kit/scripts/omc.py setup --target /path/to/new-project

# 2. 이동
cd /path/to/new-project

# 3. 헬스체크
python scripts/omc_doctor.py --target .

# 4. 도메인 오버레이 생성 (선택)
python scripts/omc.py domain init <your-domain>
```

## 설치 직후 권장 검증

```bash
python scripts/omc.py state status
python scripts/omc_doctor.py --target .
python scripts/omc.py autopilot --task-file .omc/tasks/example.json --dry-run
```

재사용성까지 한 번에 보려면:

```bash
python scripts/test_omc_setup_smoke.py
python scripts/test_omc_setup_smoke.py --executor codex
python scripts/test_omc_setup_smoke.py --executor gemini
```

## AGENTS/CLAUDE/GEMINI 포함 기준

| 파일 | 포함 기준 |
|---|---|
| `AGENTS.md` | 사실상 필수 — OMC 규칙의 기준점 |
| `.claude/CLAUDE.md` | Claude Code / Codex 사용 시 권장 |
| `.gemini/GEMINI.md` | Gemini CLI 사용 시 권장 |

권장 조합:
- 실행기 단일(Claude/Codex): `AGENTS.md` + `.claude/CLAUDE.md`
- 실행기 단일(Gemini): `AGENTS.md` + `.gemini/GEMINI.md`
- 실행기 혼용(모든 LLM): `AGENTS.md` + `.claude/CLAUDE.md` + `.gemini/GEMINI.md`

## 버리지 말아야 할 것

- `omc_kit/scripts/install.py`
- `omc_kit/scripts/omc.py`
- `omc_kit/scripts/omc_state.py`
- `omc_kit/scripts/omc_hooks.py`
- `omc_kit/prompts/team.json`

이 파일들이 있어야 setup, state, hook, mode 라우팅이 끊기지 않습니다.

## 프로젝트에만 남겨도 되는 것

- 프로젝트 도메인 전용 role 오버레이 (`project_prompts/`)
- `.omc/lessons/` — 프로젝트별 교훈 (BM25로 다음 세션 자동 주입)
- `.omc/tasks/` — autopilot 태스크 파일

## 한 줄 기준

```
공통 기능     → omc_kit/
현재 프로젝트 → project_prompts/ 또는 .omc/
도메인 차이   → python scripts/omc.py domain init <domain>
```
