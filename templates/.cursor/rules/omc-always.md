## OMC-first (Cursor Chat)

이 프로젝트에서는 **Cursor 채팅(LLM 대화)** 에서도 OMC 운영 규칙을 따릅니다.

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

### 슬래시 커맨드 인식 (Cursor 전용)

사용자가 아래 커맨드를 입력하면 지정된 동작을 수행합니다.
커맨드는 메시지 맨 앞 `/`로 시작합니다.

#### 코어 커맨드

| 입력 | 동작 |
|------|------|
| `/plan [작업]` | 구현 전 TDD 태스크 분해: 목표/범위/DoD/제약 확정 → 태스크마다 RED→GREEN→VERIFY 명시 |
| `/task [설명]` | 7단계 TDD 파이프라인 진입: CONTRACT → DESIGN → RED → GREEN → REFACTOR → TDD GATE → REVIEW |
| `/review` | `git diff` 또는 현재 변경사항 기준 코드 리뷰: 치명/중대/경미/제안 4단계 분류 |
| `/investigate [이슈]` | 4단계 디버깅: ROOT CAUSE → PATTERN ANALYSIS → HYPOTHESIS TESTING → IMPLEMENTATION. 근본 원인 확인 전 수정 시작 금지 |
| `/lesson [키워드]` | `.omc/lessons/`에서 BM25 유사도 검색 후 관련 교훈 출력: `python3 scripts/omc_lesson.py search "[키워드]"` |
| `/status` | OMC 상태 확인: `python3 scripts/omc.py state status --target .` 실행 후 요약 |
| `/ship` | TDD 게이트 확인 → 타입/린트/테스트/빌드 체크 → 배포 준비 보고 |

#### 선택 커맨드

| 입력 | 동작 |
|------|------|
| `/brainstorm [주제]` | 소크라테스식 4단계 탐색: What → Why → How → Decide. Phase 4 전 코드 작성 금지 |
| `/office-hours [요청]` | 6개 강제 질문 양식 작성: 대상 사용자/고통/성공기준/MVP/비범위/10점 버전 |
| `/ceo-review [모드]` | 10가지 체크리스트로 기능 범위 재검토. 모드: EXPAND/SELECTIVE/HOLD(기본)/REDUCE |
| `/retro` | 최근 세션 히스토리 분석 + 회고 포맷 출력 + 교훈 캡처 |
| `/autopilot` | autopilot 태스크 파일 생성/실행 안내: `python3 scripts/omc_autopilot.py new --id <id>` |

---

### 채팅 로그를 OMC 상태에 남기기

작업을 마친 뒤 아래를 실행합니다:

```bash
python3 scripts/omc.py state note --kind chat_response --text "<처리 결과/결정/다음 액션 요약>"
```

---

### 실행/상태 확인 루틴

```bash
python3 scripts/omc.py state status    # 현재 상태
python3 scripts/omc.py state compact   # 메모리 압축
```

---

### ⛔ 스킬 완료 후 자동 진행 금지 (MANDATORY)

**AI는 아래 상황에서 반드시 멈추고 사용자의 다음 명령을 기다린다.**

스킬이 완료되면 판정·결과를 출력한 뒤 다음 스킬로 자동 진입하지 않는다.
사용자가 "진행하자", "계속해", "응" 같은 짧은 승인을 해도
**명시적으로 다음 스킬 이름이 언급되지 않으면 자동 진입 금지.**

| 완료된 스킬 | 금지 동작 |
|---|---|
| omc-office-hours | PROCEED 판정 후 자동으로 omc-plan 실행 금지 |
| omc-plan | Phase 완료 후 자동으로 omc-task 실행 금지 |
| omc-critique | 판정 후 자동으로 omc-plan/omc-task 실행 금지 |
| omc-benchmark | 분석 후 자동으로 omc-office-hours/omc-plan 실행 금지 |
| omc-brainstorm | 결론 후 자동으로 omc-plan 실행 금지 |
| omc-task | 완료 후 자동으로 omc-review/omc-ship 실행 금지 |

**올바른 동작:**
> "PROCEED 판정입니다. 다음으로 `/omc-plan` 진행할까요?"
> → 여기서 멈추고 사용자 응답을 기다린다.

**금지 동작:**
> "PROCEED 판정입니다. 바로 플랜을 작성하겠습니다. [plan 내용 시작]..."
