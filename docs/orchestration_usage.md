# OMC Orchestration Usage

## 기본 원칙

- 오케스트레이션의 기본 목표는 `요청 하나를 올바른 다음 단계로 보내고, 적절한 지점에서 멈추는 것`입니다.
- 강제 진입점은 항상 CLI입니다.
- 플랫폼별 입력 방식은 달라도 종료 규칙은 같아야 합니다.
- 스킬은 안내와 추천 레이어이고, 실제 강제와 기록은 `scripts/omc.py` 계열이 맡습니다.

## 입력 선정 기준

- 권장 입력: 가장 안정적이고, 가장 재현 가능하며, 설치 직후 바로 쓰기 쉬운 입력
- 허용 입력: 자주 쓰는 습관 입력이지만 같은 오케스트레이션 경로로 수렴 가능한 입력
- fallback: 훅, 스킬, 슬래시가 불확실할 때 항상 작동하는 CLI 입력

## 기본 오케스트레이션

기본 오케스트레이션은 다음 세 가지를 다룹니다.

1. 요청을 해석한다.
2. 다음 스킬 또는 실행 경로를 정한다.
3. 결과를 보여주고 멈출지, 다음 추천 1개를 줄지 결정한다.

## 확장 오케스트레이션

확장 오케스트레이션은 `autopilot`처럼 여러 단계를 자동으로 이어서 실행할 때만 사용합니다.
보통의 계획, 구현, 리뷰 요청은 기본 오케스트레이션으로 충분합니다.

## 플랫폼별 사용 모델

| 플랫폼 | 권장 입력 | 허용 입력 | fallback |
|---|---|---|---|
| Codex | `$omc-plan`, `$omc-task`, `$omc-review` | 자연어 키워드 요청 | `python3 scripts/omc.py ...` |
| Claude Code | `/plan`, `/task`, `/review` | 추가 확인 전까지 slash command 유지 | `python3 scripts/omc.py ...` |
| Gemini CLI | `/plan`, `/task`, `/review` | 자연어 요청 | `python3 scripts/omc.py ...` |

## 시작-멈춤-다음 추천

### 계획 요청

| 플랫폼 | 권장 입력 | 기대 출력 | 멈춤 지점 | 다음 추천 |
|---|---|---|---|---|
| Codex | `$omc-plan 로그인 기능 설계` | CONTRACT, 최소 설계, TDD 태스크 | 설계 출력 직후 | `$omc-task` |
| Claude Code | `/plan 로그인 기능 설계` | CONTRACT, 최소 설계, TDD 태스크 | 설계 출력 직후 | `/task` |
| Gemini CLI | `/plan 로그인 기능 설계` | CONTRACT, 최소 설계, TDD 태스크 | 설계 출력 직후 | `/task` |

### 구현 요청

| 플랫폼 | 권장 입력 | 기대 출력 | 멈춤 지점 | 다음 추천 |
|---|---|---|---|---|
| Codex | `$omc-task 로그인 버튼 구현` | CONTRACT, RED, GREEN, GATE, handoff | 구현 완료 + gate 통과 직후 | `$omc-review` |
| Claude Code | `/task 로그인 버튼 구현` | CONTRACT, RED, GREEN, GATE, handoff | 구현 완료 + gate 통과 직후 | `/review` |
| Gemini CLI | `/task 로그인 버튼 구현` | CONTRACT, RED, GREEN, GATE, handoff | 구현 완료 + gate 통과 직후 | `/review` |

### 리뷰 요청

| 플랫폼 | 권장 입력 | 기대 출력 | 멈춤 지점 | 다음 추천 |
|---|---|---|---|---|
| Codex | `$omc-review` | 치명/중대/경미/제안 분류 리뷰 | 리뷰 출력 직후 | `$omc-ship` 또는 사용자 선택 대기 |
| Claude Code | `/review` | 치명/중대/경미/제안 분류 리뷰 | 리뷰 출력 직후 | `/ship` 또는 사용자 선택 대기 |
| Gemini CLI | `/review` | 치명/중대/경미/제안 분류 리뷰 | 리뷰 출력 직후 | `/ship` 또는 사용자 선택 대기 |

## 운영 메모

- 기본값은 `권장 입력`입니다.
- Codex와 Gemini는 짧은 자연어 입력도 허용되지만, 가능하면 플랫폼별 권장 입력으로 수렴시키는 편이 안정적입니다.
- Claude Code는 현재 문서 기준으로 slash command를 기본값으로 유지합니다.
- 훅이 미동작하거나 컨텍스트가 흔들리면 CLI fallback으로 바로 전환합니다.
