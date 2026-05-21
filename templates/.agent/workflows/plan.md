# /plan — OMC 설계·계획

OMC `analysis` + `senior_coding` 역할로 구현 전 요구사항을 명확히 하고 계획을 세웁니다.

## 실행 순서

1. OMC 현재 상태 확인:
```bash
python3 scripts/omc.py state status --target .
```

2. **DEEP INTERVIEW 모드**로 요구사항 인터뷰를 시작합니다.

---

## 계획 수립 프로토콜

### Phase 1: 요구사항 정리 (질문 먼저)
구현 전에 아래 4가지를 확정합니다:
- **목표**: 무엇을 달성하는가?
- **범위**: 포함하는 것 / 제외하는 것
- **DoD (완료 기준)**: 언제 완료로 볼 것인가?
- **제약**: 건드리면 안 되는 것, 기술 스택 제한

### Phase 2: 설계 (최소)
- 입력/출력 계약
- 실패·에러 정책
- 롤백/리스크

### Phase 3: TDD 태스크 분해 (MANDATORY)

각 태스크를 **RED → GREEN → REFACTOR** 사이클로 명시합니다.
**테스트 단계가 없는 태스크는 완료로 인정하지 않습니다.**

포맷:
```
태스크 1: [기능 설명]
  RED    : [작성할 실패 테스트 — 파일명 + 테스트 케이스 이름]
  GREEN  : [테스트를 통과하는 최소 구현]
  VERIFY : [확인 커맨드 — npm test, pytest 등]
```

### Phase 4: OMC 세션 기록
계획이 확정되면 OMC에 세션으로 기록합니다:
```bash
python3 scripts/omc.py state confirm --target .
```

---

계획할 작업: $ARGUMENTS
