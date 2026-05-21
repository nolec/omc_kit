# /ship — OMC 배포 준비

OMC 가드 확인 후 배포 전 체크리스트를 실행합니다.

## 실행 순서

### 1. OMC 가드 확인 (미컨펌 세션 차단)
```bash
python3 scripts/omc_guard.py require --target . --for "ship"
```

미확정 세션이 있으면 **배포를 중단**하고 컨펌을 요청합니다:
```bash
python3 scripts/omc.py state confirm --target .
```

### 2. TDD 강제 체크 (MANDATORY — 실제 스크립트 실행)

> 더 이상 AI 스스로 판단하지 않습니다. 스크립트가 물리적으로 차단합니다.

```bash
python3 scripts/omc_tdd_check.py --run-tests
```

- 신규/수정된 구현 파일 중 **테스트 파일이 없으면 즉시 종료 코드 1** 로 차단
- `--run-tests` 플래그: 테스트 파일 존재 확인 후 **실제 테스트까지 실행**
- 예외 허용(경고만 출력): `python3 scripts/omc_tdd_check.py --run-tests --report-only`

스크립트가 없으면 수동으로 확인:
```bash
git diff --name-only --diff-filter=ACM origin/main...HEAD \
  | grep -E '\.(ts|tsx|py)$' \
  | grep -vE '(\.spec\.|\.test\.|\.d\.ts|\.config\.|/types/|/constants/|\.styled\.)'
```

**위 목록에 파일이 있으면 배포 중단** — 테스트 없는 파일이 하나라도 있으면 안 됩니다.

### 3. 린트 / 타입 체크 / 전체 테스트
```bash
# 프로젝트에 맞는 명령어로 교체하세요
npx nx affected --target=test
```

### 4. Git 상태 확인
```bash
git status -sb
git log --oneline -5
```

---

## 배포 전 체크리스트

- [ ] OMC 가드 통과 (미확정 세션 없음)
- [ ] **TDD 체크 통과** (`omc_tdd_check.py --run-tests` 반환값 0)
- [ ] 타입 에러 0개
- [ ] 린트 에러 0개
- [ ] 테스트 전부 통과
- [ ] `.env` / 비밀값 커밋 없음

모든 항목 통과 시 배포 진행, 하나라도 실패 시 중단하고 이슈를 보고합니다.

---

## 배포 후 — 작업 규모 기록 (자동)

배포 완료 후 아래를 실행해 이번 작업의 규모를 기록합니다.

**Claude Code**
```bash
python3 scripts/omc_cost.py record --executor claude-code --task "$ARGUMENTS"
```
토큰 실측 포함:
```bash
# claude --output-format json ... > /tmp/llm_out.json 으로 실행한 경우
python3 scripts/omc_cost.py record --executor claude-code --model claude-sonnet-4 \
  --task "$ARGUMENTS" --llm-json /tmp/llm_out.json
```

**Gemini CLI**
```bash
python3 scripts/omc_cost.py record --executor gemini --task "$ARGUMENTS"
```
토큰 실측 포함:
```bash
# gemini --json ... > /tmp/llm_out.json 으로 실행한 경우
python3 scripts/omc_cost.py record --executor gemini --model gemini-2.5-pro \
  --task "$ARGUMENTS" --llm-json /tmp/llm_out.json
```

**Codex / OpenAI**
```bash
python3 scripts/omc_cost.py record --executor codex --task "$ARGUMENTS"
```
토큰 실측 포함:
```bash
python3 scripts/omc_cost.py record --executor codex --model gpt-4o \
  --task "$ARGUMENTS" --llm-json /tmp/llm_out.json
```

비용 리포트 확인:
```bash
python3 scripts/omc_cost.py report
```

---

## 배포 후 — Compound Engineering (30초)

배포 완료 후 아래 질문에 답하세요. 교훈이 있으면 기록합니다.

```
이번 배포에서 배운 점이 있나요?
  - 왜 처음부터 제대로 못 했나?
  - 어떤 요구사항을 놓쳤나?
  - 다음에 추가할 규칙은?

교훈이 있으면:
  python3 scripts/omc_lesson.py add -i

없으면 그냥 넘어가세요.
```

---

배포 대상: $ARGUMENTS (미지정 시 전체)
