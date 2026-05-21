# Agent Behavior Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them. Don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it. Don't delete it.

When your changes create orphans:
- Remove imports, variables, or functions that your changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" -> "Write tests for invalid inputs, then make them pass"
- "Fix the bug" -> "Write a test that reproduces it, then make it pass"
- "Refactor X" -> "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```text
1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Test-Driven Development (TDD) — MANDATORY

**테스트 없이 완료 선언은 인정하지 않습니다.**

### 핵심 규칙 (비협상)

1. **새 함수·모듈·기능 추가 시** → 반드시 대응하는 테스트를 먼저 작성합니다.
2. **버그 수정 시** → 버그를 재현하는 테스트를 먼저 RED로 만들고, 수정 후 GREEN으로 확인합니다.
3. **리팩터링 시** → 기존 테스트가 GREEN인 상태에서 시작하고, 끝나도 GREEN이어야 합니다.
4. **테스트 파일이 없는 신규 코드 파일**은 "미완성"으로 간주합니다.

### RED → GREEN → REFACTOR 사이클

```
RED    : 실패하는 테스트를 먼저 작성한다
GREEN  : 테스트를 통과하는 최소 구현을 작성한다
REFACTOR: 테스트가 GREEN인 상태에서 코드를 정리한다
```

### 테스트 누락 시 동작

- 새 파일(`*.ts`, `*.py` 등)을 만들었는데 대응 테스트가 없으면:
  1. **사용자에게 알린다** — "테스트 파일이 없습니다. 작성할까요?"
  2. 사용자가 "건너뜀" 명시 시에만 예외 허용
- `/ship` 실행 시 테스트 없는 신규 파일이 있으면 경고 출력

### 테스트 파일 위치 규칙

| 언어 | 테스트 파일 위치 |
|------|----------------|
| TypeScript | `*.spec.ts` 또는 `*.test.ts` (같은 폴더 또는 `__test__/`) |
| Python | `test_*.py` 또는 `*_test.py` |

### 예외 허용 조건 (명시적 승인 필요)

- 타입 정의 파일 (`*.d.ts`, `types.ts`)
- 설정 파일 (`*.config.ts`, `*.json`)
- 진입점/index 파일 (`index.ts`) — 단, 로직이 없을 때만
- 사용자가 명시적으로 "테스트 없이 진행" 승인한 경우

