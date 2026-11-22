# OMC Kit Map

## 목적

이 문서는 OMC/멀티비서 킷을 다른 프로젝트로 옮길 때 무엇을 가져가야 하는지 빠르게 판단하기 위한 공통 지도입니다.

## 지원 범위

Codex 스킬과 공통 CLI를 배포합니다. 일반 작업은 Superpowers와 프로젝트 규칙을 따릅니다. 세션·도구·Git 자동 훅은 비활성 상태입니다. 설치 절차는 [새 프로젝트에 OMC 설치](next_project_pack.md)를 참고하세요.

## 원본과 설치 프로젝트

공통 파일의 원본은 `.omc/install-source.json`의 `source_path`에 기록된 체크아웃입니다. 체크아웃 이름과 위치는 프로젝트마다 다를 수 있습니다. 공통 파일을 갱신할 때는 원본의 `scripts/`, `templates/`, `docs/`에서 해당 파일을 수정하고, 대상 프로젝트에서 필요한 경우 명시적으로 재설치합니다.

## 파일 구조

```
OMC 원본 체크아웃/
  VERSION                OMC 릴리스 버전
  scripts/               공통 CLI와 검증 도구
  templates/
    AGENTS.md            프로젝트 규칙에 병합되는 OMC 관리 블록
    CODEX.md             AGENTS.md를 가리키는 안내
    .agents/skills/      Codex에서 $스킬명으로 호출하는 Agent Skills
    .codex/commands/     Quickstart와 상세 운영 문서 링크
    .agent-hooks/        신규 기본 설치에서 제외되는 레거시 셸 훅 원본
  docs/                  사용 및 운영 문서
  prompts/               공통 역할·모드 프롬프트

설치된 프로젝트/
  AGENTS.md              프로젝트 규칙과 OMC 관리 블록
  project_prompts/       프로젝트별 역할·프로필 (선택)
  .omc/                  설치 출처, 교훈, 세션·작업 기록
```

레거시 셸 훅 7개는 원본 킷에 남아 있으며, 기존 설치 사본은 일반 재설치와 `--force`에서 보존됩니다.
라이프사이클 작업은 `python3 scripts/omc.py hook <이벤트>`로 명시적으로 실행합니다.

## 핵심 스크립트 목록 (SSOT)

| 스크립트 | 역할 |
|---|---|
| `omc.py` | 단일 진입점 |
| `omc_state.py` | 세션 상태 R/W + compact |
| `omc_guard.py` | 미확정 세션 실행 차단 |
| `omc_hooks.py` | 명시적 라이프사이클 처리 |
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
| `omc_doctor.py` | 설치 상태 진단 |
| `omc_version.py` | 릴리스·원본·설치 무결성 상태 판정 |
| `omc_utils.py` | 공통 유틸 (project_root) |
| `install.py` | 다른 프로젝트로 배포 |
| `auto_prompt.py` | 프롬프트 합성 |

## 실무 규칙

공통 로직을 고칠 때:
- 원본 체크아웃에서 공통 파일을 수정
- 대상 프로젝트에서 `python3 scripts/omc.py setup --target . --force`로 동기화
- `omc_doctor.py --target .`로 검증

현재 프로젝트만 다르게 하고 싶을 때:
- `project_prompts/` 또는 `.omc/policy.json`을 수정

프로젝트 추가할 때:
- [설치 안내](next_project_pack.md)에 나온 설치 명령을 실행
- `python scripts/omc.py version --target .`으로 설치 버전과 최신성 확인

## CI/CD

`.github/workflows/omc-ci.yml`이 설치된 레포에서는 아래 검사를 자동화할 수 있습니다.

- **ubuntu**: 셸 독립 단위 테스트 + GitHub 이벤트 base SHA 기준 `omc_tdd_check.py --base <sha>` (TDD 대응 관계 게이트)
- **macOS**: 셸 의존 훅 테스트 (`omc-pipeline-check.sh`, `omc-post-file-check.sh` 등)
