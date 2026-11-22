# OMC Quickstart KR

## Codex에서 Superpowers와 함께 시작하기

Codex에서 Superpowers로 작업을 시작하고 계획·구현·검증을 진행합니다. OMC가 설치된 프로젝트 루트에서는 필요한 순간에 아래 기능을 직접 호출합니다.

| 필요한 때 | OMC 기능 |
|---|---|
| 작업을 시작하거나 다시 열었을 때 | `python3 scripts/omc.py state status --target .`로 상태를 조회합니다. Superpowers 계획 원장이 있으면 `--superpowers-ledger`에 경로를 지정할 수 있습니다. |
| 커밋하기 전에 요청 범위를 확인할 때 | `python3 scripts/omc.py scope-check --target . --expected-path scripts/example.py`로 요청 경로와 staged 경로를 비교합니다. |
| 기존 검증에서 빠진 UI 확인 항목을 찾을 때 | Codex에서 `$omc-qa`를 호출하고 기존 테스트 결과와 실행 환경을 함께 전달합니다. |
| 프로젝트 교훈을 찾을 때 | `python3 scripts/omc_lesson.py --target . search "키워드"`를 실행합니다. |

OMC는 Codex Agent Skills와 공통 CLI를 지원합니다. Claude Code·Gemini CLI·Antigravity·Cursor 전용 설치 지원은 종료됐습니다. 명령별 판정 기준은 [OMC Quickstart](omc_quickstart.md)를 참고하세요.

## 핵심 원칙

- 자동 세션 훅은 비활성이며 필요한 상태 조회와 교훈 검색은 명시적으로 실행합니다.
- 기본 정책은 `.omc/policy.json`의 `enforce_confirm=true`입니다.
- 최신 OMC 세션이 confirmed 되기 전에는 실행 가드가 상태 변경 명령을 차단합니다.
- 상태 압축은 사용자가 승인한 작업공간에서 명시적으로 실행합니다.
- 교훈은 명시적으로 검색할 수 있으며 자동 주입은 활성화되어 있지 않습니다.

## 자동 실행 정책

자동 세션·도구·Git 훅은 비활성입니다. 세션 시작 시 초기화 명령을 자동 실행하지 않습니다. 아래 CLI는 사용자가 요청한 작업 공간에서만 명시적으로 호출합니다.

## OMC 단독 운영 예시

OMC만으로 작업할 때는 Codex에서 필요한 스킬을 직접 호출하거나 아래처럼 공통 CLI에서 요청을 시작할 수 있습니다.

1. **요청 시작** — 역할 추천 + 컨펌

```bash
python3 scripts/omc.py "기능 구현 요청"
```

2. **역할 확인**
   - `추천 역할: ...`가 뜨면 `Enter`로 확정 또는 `+추가,-삭제` / `a,b,c`로 수정
   - 컨펌 전에는 파일 수정/명령 실행 없음

3. **상태 확인**

```bash
python3 scripts/omc.py state status
python3 scripts/omc_doctor.py --target .
```

4. **교훈 기록 (30초)**

```bash
python3 scripts/omc_lesson.py add -i
```

---

### 단독 운영에서 단일 작업 요청

```bash
python3 scripts/omc.py "로그인 기능 구현해줘"
```

내부 동작:
- 자동 훅은 실행하지 않습니다. 필요한 컨텍스트·검증 명령만 승인된 작업 공간에서 명시적으로 실행합니다.
- 역할/모드 자동 선택 → 컨펌 UI
- 프롬프트 합성 → LLM 실행

---

### 단독 운영에서 자율 루프 (Autopilot)

여러 스텝을 자동 실행하고 `expect` 검증으로 품질을 확인합니다.

```bash
# 태스크 파일 생성
python3 scripts/omc_autopilot.py new --id feat-login --title "로그인 기능"

# .omc/tasks/feat-login.json 편집 후 실행
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-login.json

# 계획 확인 (LLM 호출 없음)
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-login.json --dry-run

# 실행 기록
python3 scripts/omc_autopilot.py status

# 관제 요약
python3 scripts/omc_autopilot.py overview
```

---

## 시나리오 3. 교훈 검색 및 기록

```bash
# BM25 유사도 검색
python3 scripts/omc_lesson.py search "타입 오류" --top 3

# 교훈 추가
python3 scripts/omc_lesson.py add -i

# 최근 목록
python3 scripts/omc_lesson.py list
```

---

## 시나리오 4. 현재 상태 확인

```bash
python3 scripts/omc.py state status
python3 scripts/omc_doctor.py --target .
cat .omc/context.md    # 마지막 세션 컨텍스트
```

---

## 시나리오 5. 수동 compact

승인된 작업공간에서 필요할 때 수동으로 압축합니다:

```bash
python3 scripts/omc.py state compact
```

내부 동작:
- `pre_compact` 훅 → 메모리 스냅샷 저장
- 상태 압축 (최근 25개 보존)
- `post_compact` 훅 → notepad 재생성

---

## 시나리오 6. 새 프로젝트에 설치

```bash
python <OMC_SOURCE>/scripts/omc.py setup --target /path/to/project
cd /path/to/project
python scripts/omc_doctor.py --target .
python scripts/omc.py version --target .
```

설치 버전과 원본 최신성, 관리 파일 무결성을 기계적으로 확인하려면
`python scripts/omc.py version --target . --json`을 사용합니다. 기존 schema v1
설치 영수증은 다음 `setup --force` 또는 안전한 자동 업데이트에서 v2로 승격됩니다.

---

## 공통 CLI의 Gemini 실행기 예시

Gemini CLI가 별도로 설치된 환경에서 공통 CLI를 통해 실행기를 직접 지정하는 예시입니다. OMC의 Gemini 전용 설치 지원 범위와는 별개입니다.

```bash
OMC_EXECUTOR=gemini python3 scripts/omc.py "작업 요청"
python3 scripts/omc_chat.py --executor gemini "작업 요청"
```

---

## 시나리오 8. 수동 라이프사이클

설치는 자동 훅을 활성화하지 않습니다. 대상 프로젝트의 `.codex/hooks.json`에 활성 훅이 있으면
설치기는 `--force`를 사용해도 파일을 변경하기 전에 중단합니다. 설치하려면 기존 설정을 백업한 뒤
`SessionStart`, `PreToolUse`, `UserPromptSubmit`, `PostToolUse`를 포함해 모든 이벤트의 훅 배열을
빈 배열로 설정하고 다시 실행합니다.
라이프사이클 명령이 필요하면 그 부작용과 작업 범위를 확인한 뒤 명시적으로 실행합니다.

## 시나리오 9. TDD 파이프라인

```bash
# 테스트 먼저 작성 후 RED 등록
python3 scripts/omc_pipeline_guard.py red-done path/to/test_file.py

# 구현 파일 생성 (RED 등록 후에만 허용)
# ... 구현 ...

# 예외 허용
python3 scripts/omc_pipeline_guard.py allow path/to/config.py --reason "설정 파일"

# 상태 확인
python3 scripts/omc_pipeline_guard.py status
```

---

## 시나리오 10. 모호 메시지와 선택 기록

자동 `UserPromptSubmit` 훅은 비활성이므로 아래는 명시적으로 실행한 경우의 기록 경계입니다.

동작 조건:
- 세션 상태가 `confirmed` (이전 작업 완료 후 새 작업 선언 전)
- 입력이 "응", "ㅇ", "진행하자", "계속해" 등 15자 미만 모호 패턴
- 명시적 스킬명(`$omc-task`, `$omc-plan` 등)이 없을 때

단, 에이전트가 현재 세션의 로컬 커밋 선택지를 `state decision-open`으로 먼저 기록했다면 정확히 일치하는 짧은 선택은 현재 작업 승인으로 상속됩니다. 같은 세션과 변경 범위에서만 유효하고, 커밋 성공 후 `state decision-consume`으로 한 번만 소비됩니다. push·PR·deploy·delete 권한은 상속하지 않습니다.

```bash
# 선택지를 사용자에게 보여주기 직전에 기록
python3 scripts/omc.py state decision-open --target . \
  --decision-id commit-group \
  --action local_commit \
  --options-json '[{"id":"1","aliases":["1","1번"],"value":"selected-group"}]'

# 커밋 성공 후 receipt 소비
python3 scripts/omc.py state decision-consume --target . --decision-id commit-group
```

여러 커밋 그룹을 제시한다면 각 option에 `"paths":["path/to/file"]`을 넣어야 합니다. 선택되지 않은 그룹의 파일은 승인된 커밋으로 소비되지 않습니다.

출력 예시:
```
[OMC] 모호한 진행 요청입니다 — 무엇을 진행할까요?
  직전 스킬: omc-review
```

Codex에서 계획을 이어가려면 `$omc-plan 작업 설명`처럼 스킬을 명시합니다.

---

## 시나리오 11. GitHub Actions CI

이 시나리오는 `.github/workflows/omc-ci.yml`이 설치된 레포에서만 적용됩니다.

```yaml
# push / PR 마다 자동 실행
# ubuntu: 단위 테스트 + 이벤트 base SHA 기준 omc_tdd_check.py --base <sha>
# macOS:  omc-pipeline-check.sh 등 셸 훅 테스트
```

로컬에서 CI와 동일한 검사를 실행하려면:
```bash
# TDD 게이트 (staged 파일 기준)
python3 scripts/omc_tdd_check.py --staged

# 전체 테스트 (CI ubuntu 기준)
python3 -m pytest scripts/ -q \
  --ignore=scripts/test_omc_pipeline_check_exit.py \
  --ignore=scripts/test_omc_post_file_check.py \
  --ignore=scripts/test_omc_pipeline_check_gemini.py

# 빠른 회귀 테스트 (외부 health 실행 제외)
python3 -m pytest scripts/ -q -m "not slow"

# 느린 health 테스트 별도 실행
python3 -m pytest scripts/test_omc_health.py -q -m slow
```

---

## 비용 추적

```bash
# 작업 후 비용 기록
OMC_EXECUTOR=claude python3 scripts/omc_cost.py record \
  --model claude-sonnet-4-5 --task "작업명"

# 현황
python3 scripts/omc_cost.py report
```
