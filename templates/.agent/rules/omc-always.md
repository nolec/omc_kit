## OMC-first (Antigravity Workspace Rule)

이 프로젝트에서는 **Antigravity 채팅(LLM 대화)** 에서도 OMC 운영 규칙을 따릅니다.

### 세션 시작 시 자동 컨텍스트 로드

**대화 첫 메시지를 처리하기 전**, 아래 파일을 읽어 현재 세션 컨텍스트를 파악합니다:

```bash
# 세션 컨텍스트 (BM25 교훈 + 세션 상태)
.omc/summary.md

# 없으면 생성
python3 scripts/omc.py hook session_start --target .
```

이 파일이 존재하면 내용을 현재 대화의 배경 지식으로 사용합니다.

### 기본 원칙

- 사용자의 자연어 요청은 OMC 방식으로 다룹니다.
- **응답은 항상 한국어**로 합니다.
- 실행/파일 변경이 필요한 경우, 역할 추천 → 컨펌 후 진행합니다.

---

### 슬래시 커맨드

`/` 를 입력하면 아래 Workflows가 목록에 나타납니다.

#### 코어 커맨드

| 커맨드 | 동작 |
|--------|------|
| `/plan` | 구현 전 TDD 태스크 분해: 목표/범위/DoD/제약 확정 → RED→GREEN→VERIFY |
| `/task` | 7단계 TDD 파이프라인: CONTRACT → RED → GREEN → REFACTOR → GATE → REVIEW |
| `/review` | 코드 리뷰: 치명/중대/경미/제안 4단계 분류 |
| `/investigate` | 4단계 디버깅: 근본 원인 확인 전 수정 금지 |
| `/status` | OMC 상태 확인 |
| `/ship` | TDD 게이트 → 타입/린트/테스트 체크 → 배포 준비 |

#### 선택 커맨드

| 커맨드 | 동작 |
|--------|------|
| `/brainstorm` | 소크라테스식 4단계 요구사항 탐색 |
| `/office-hours` | 6개 강제 질문으로 제품 사고 먼저 |
| `/ceo-review` | 기능 범위 CEO 관점 재검토 |
| `/lesson` | Compound Engineering 교훈 캡처 |
| `/retro` | 주간 회고 + 교훈 캡처 |

---

### 실행/상태 확인 루틴

```bash
python3 scripts/omc.py state status    # 현재 상태
python3 scripts/omc.py state compact   # 메모리 압축
```
