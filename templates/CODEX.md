## OMC Overlay For Codex (o1/o3/o4 계열)

이 프로젝트는 공통 운영 규칙의 SSOT를 `AGENTS.md`로 둡니다.
이 파일은 OpenAI Codex / o-series 실행기용 **추가 안내**입니다 — AGENTS.md의 모든 규칙이 기본 적용됩니다.

> **모든 판단·추천·구현은 `ETHOS.md`의 원칙을 따릅니다.**
> 완성하라 / 찾아라 / 사람이 결정한다 / 지루한 것을 선택해라

## Codex 전용 사고 패턴 (MANDATORY)

o1/o3/o4 계열은 **내부 추론(chain-of-thought)이 자동으로 작동**합니다.
과도한 사고 지시는 역효과입니다. 대신 **명확한 문제 정의와 출력 포맷**에 집중합니다.

### 프롬프트 원칙

| 원칙 | 이유 |
|---|---|
| 짧고 명확하게 | 긴 프롬프트는 추론 품질을 낮춤 |
| "step by step" 금지 | 이미 내부에서 하고 있음 — 방해만 됨 |
| 출력 포맷을 명시 | 형식 지정은 효과 있음 |
| 제약 조건 우선 명시 | "하지 말아야 할 것"을 먼저 선언 |

### 좋은 요청 패턴

```
✅ 좋음: "기존 useOrderFilter.ts 패턴을 따라 useProductFilter.ts를 작성해라.
         any 사용 금지. 반환 타입 명시 필수."

❌ 나쁨: "천천히 단계별로 생각하면서 useProductFilter.ts를 작성해줘.
         먼저 요구사항을 분석하고..."
```

### 불확실할 때

답을 모르면 추측하지 않고 **"이 부분은 확인이 필요합니다: [질문]"** 으로 명시합니다.

## 자동 실행 비활성

세션 시작·도구 실행·Git 자동 훅은 비활성 상태로 유지합니다.
원본에서 `state init`, `state status`, `session_start`를 자동 실행하지 않습니다.
읽기 전용 요청은 파일·Git 조회로 확인합니다. 일반 구현은 Superpowers 흐름과 프로젝트 규칙을 따릅니다.
사용자가 OMC 구현 흐름을 명시적으로 선택한 경우 승인된 격리 작업 공간에서 기존 진입·CONTRACT·RED·검증 절차를 적용합니다.

## Codex 진입점

OMC 작업 흐름을 선택할 때 필요한 스킬을 직접 호출합니다.

```
$omc-plan 로그인 기능 구현
$omc-task 버튼 컴포넌트 추가
$omc-review
```

OMC 단독 운영 시 권장 순서: `$omc-plan` → `$omc-task` → `$omc-review` → `$omc-ship`

세부 규칙 전체: **AGENTS.md** 참조

설치 소스는 `.omc/install-source.json`에 기록된 경로로 식별합니다. 변경은 해당 소스와 templates SSOT에서 수행하며 원본 기록을 추정 생성하지 않습니다.
