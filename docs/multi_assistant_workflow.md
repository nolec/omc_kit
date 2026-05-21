# Multi-Assistant Workflow (역할 분리 + 자동 합성)

이 셋업은 “한 명의 만능 AI” 대신, **역할(비서) 단위로 프롬프트를 분리**해 필요할 때 조합합니다.

빠르게 구조를 파악하거나 다음 프로젝트로 옮길 때는 아래 두 문서를 먼저 보면 됩니다.

- `docs/kit_map.md`
- `docs/next_project_pack.md`

## 1) 역할(비서) 개념

- 역할 프롬프트는 `prompts/ROLE_*.md`에 저장합니다.
- 역할/프로필(조합)은 `prompts/team.json`이 SSOT입니다.

## 0) 중요한 한계(자동 인식)

LLM이 로컬 파일을 “자동으로” 읽는지는 사용하는 도구가 어떤 파일을 컨텍스트로 로드하느냐에 달려 있습니다.

- 기본 언어: 사용자에게 보이는 안내/컨펌/보고는 **한글**을 기본으로 합니다(사용자가 영어를 명시하면 전환).

- Codex CLI: `AGENTS.md`를 지침으로 사용할 수 있습니다.
- Claude Code: 보통 `CLAUDE.md`를 힌트로 활용합니다(도구 설정에 따라 다름).
- 기타 LLM/에디터: 프로젝트 지침 파일을 자동 로드하지 않을 수 있습니다.

그래서 이 킷의 `scripts/install.py`는 `AGENTS.md`/`CLAUDE.md`/`GEMINI.md`에 부트스트랩 블록을 추가해,
“역할 제안 → 컨펌 → 진행” 습관이 기본으로 잡히도록 돕습니다.

OMC 방향으로는 `scripts/omc.py`를 단일 진입점으로 쓰고, `autopilot`, `team`, `ulw`, `ralph`, `deep-interview` 같은 모드 키워드를 자동 해석하는 구성이 권장됩니다.
또한 `.omc/` 아래에 `notepad.md`, `project-memory.json`, `state/sessions/`를 두고, 각 `omc` 실행마다 최근 작업 상태를 기록해 다음 세션에 다시 주입합니다.
이제 `session_start`, `session_end`, `pre_compact`, `post_compact` 훅도 같이 실행되어, compact 전 스냅샷과 compact 후 notepad 재생성이 자동화됩니다.
훅 정의는 `.omc/hooks.json`에서 바꿀 수 있고, 기본값은 builtin 훅이지만 `{"type":"shell","command":"..."}` 형태의 shell hook도 지원합니다.

## 2) 기본 사용(수동 조합)

```bash
python scripts/compose_prompt.py --roles search,analysis,senior_coding
```

## 3) OMC-style 단일 진입점(추천)

```bash
# 단일 작업 (역할 추천 + 컨펌)
python scripts/omc.py "로그인 기능 구현하고 싶어"
python scripts/omc.py team "프론트엔드/백엔드/테스트를 같이 나눠서 해줘"

# Autopilot: 태스크 파일 기반 멀티 LLM 자율 루프
python scripts/omc_autopilot.py new --id feat-x --title "기능 X 구현"
python scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json
python scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json --dry-run

# 상태/훅/compact
python scripts/omc.py setup --target .
python scripts/omc.py hook session_start
python scripts/omc.py state status
python scripts/omc.py state compact
```

## 4) 프로필 사용(추천)

```bash
python scripts/compose_prompt.py --profile debugging
python scripts/compose_prompt.py --profile code_review
```

## 5) 자동 추천(규칙 기반)

```bash
roles=$(python scripts/suggest_roles.py --text "버그 원인 분석하고 수정해줘")
python scripts/compose_prompt.py --roles "$roles" --out /tmp/prompt.md
```

## 6) 운영 팁(권장 루프)

1) `search`로 SSOT 확보(파일/문서/근거)
2) `analysis`로 원인 가설/검증 설계
3) `senior_coding`으로 구현/검증
4) `code_review`로 diff 리스크 점검

## 7) 삭제/정리 작업 안전수칙

- 기본 정책: 삭제는 `rm`이 아니라 휴지통 이동으로 처리합니다.
- `scripts/omc_guard.py`는 파괴적 삭제 패턴(`rm`, `find -delete`, `git reset --hard`)을 기본 차단합니다.
- 정리 명령은 아래 스크립트를 사용합니다.

```bash
python omc_kit/scripts/safe_trash.py <path1> <path2> ...
```
