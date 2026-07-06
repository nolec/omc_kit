# Fugu Benchmark

## 현재 OMC 수준

OMC는 현재 learned orchestrator가 아니라 `rule-based orchestration`에 가깝다.
주요 근거는 `scripts/omc_role_suggest.py`, `scripts/omc_skill_benchmark.py`, `prompts/ROLE_ORCHESTRATOR.md`이다.
즉, 현재 강점은 예측 가능성과 통제 가능성이고, 약점은 오케스트레이션 품질이 데이터 학습으로 계속 좋아지는 구조는 아니라는 점이다.

## 부분 반영

지금 상태는 Fugu를 그대로 적용한 것이 아니라 partial adoption 단계다.
반영된 것은 response mode 분기, reroute 감소 측정, latency/quality에 가까운 모델 프로필 분기다.

## 채택한 항목

- 초기 라우팅 품질을 response mode benchmark로 측정
- reroute rate, task start delay 같은 orchestration 지표 추적
- 단일 요청에서 추천 시작 스킬을 정하는 정책 계층 유지

## 아직 미채택 항목

- learned orchestrator
- dynamic worker composition
- single-entry runtime orchestration
- provider/privacy 제약 기반 agent pool 제어

## 도입 가치

Fugu benchmark의 핵심 가치는 OMC를 단순 스킬 모음이 아니라 오케스트레이션 품질을 측정하고 개선하는 제품으로 보게 만든다는 점이다.

- 어떤 요청이 처음부터 올바른 시작 스킬로 갔는지 측정할 수 있다.
- response mode, reroute, task start delay를 통해 흐름 품질을 숫자로 본다.
- `learned orchestrator` 전체를 바로 도입하지 않더라도, 현재 rule-based 계층의 약점을 더 선명하게 찾을 수 있다.
- single-entry 방향을 지금 당장 구현하지 않아도, 어떤 조건에서 그 투자가 필요한지 판단 근거를 마련한다.

## Codex / Claude / Gemini 적용

세 실행기 모두 동일한 learned orchestrator를 바로 공유하는 단계는 아니다.
대신 각 실행기 입력 방식이 달라도 같은 오케스트레이션 품질 기준으로 비교하는 것이 현재 적용 방식이다.

- Codex: `$omc-plan`, `$omc-task`, `$omc-review` 같은 명시적 스킬 진입점을 유지한 채 response mode와 next-action 정확도를 측정한다.
- Claude: slash command 중심 흐름에서 잘못된 자동 진입, 누락된 다음 액션, reroute 발생 여부를 본다.
- Gemini: slash command와 자연어 요청이 섞일 때도 같은 benchmark 지표로 비교한다.

즉, 현재 단계의 목표는 실행기 통합이 아니라 `공통 오케스트레이션 품질 기준`을 맞추는 것이다.

## 1단계 범위

1단계는 전략 문서화와 benchmark 최소 확장까지만 포함한다.
즉, Fugu 대비 현재 수준을 문서로 고정하고, 초기 라우팅 불일치율 같은 최소 orchestration KPI 1개를 추가한다.

## 반영 검증 기준

Fugu 비교에서 중요한 건 "무엇을 만들었는가"보다 "무엇이 실제로 반영됐는가"를 같은 기준으로 읽는 것이다.

| 로드맵 항목 | 실제 반영 증거 | Fugu 비교 축 | 신뢰도 |
|---|---|---|---|
| V4 observed tuning | roadmap 문구 + benchmark report/overview/autopilot completed 상태 | feedback loop / telemetry tuning | 높음 |
| Operator Experience | roadmap 문구 + response-mode fixture + expensive-flow summary | next-step quality / operator control | 중간 |
| Fugu식 MVP 설계 | roadmap 문구 + benchmark 문서 + 후속 설계 태스크 | single-entry runtime orchestration | 중간 |

- 문서만 있으면 `부분 반영`
- 테스트/benchmark/autopilot 상태까지 있으면 `반영 확인`
- 실제 사용자 체감 또는 운영 판정까지 닫히면 `체감 개선 확인`
- 비교 판단은 `현재 상태 참조`와 `반영 검증 완료`를 구분한다.
