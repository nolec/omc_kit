# OMC Skill Compaction Report

업데이트: 2026-06-29

## 대상

- `omc-task`
- `omc-plan`
- `omc-review`
- `omc-critique`

## 측정 방식

- 기준 파일: `.agents/skills/<skill>/SKILL.md`
- 비교 기준: `HEAD` 대비 현재 워킹트리의 non-empty line 수
- 안전 장치: 각 스킬별 `scripts/test_omc_*_skill_contract.py`

## 결과

| Skill | Before | After | Delta | Contract Limit | Status |
|---|---:|---:|---:|---:|---|
| `omc-task` | 75 | 59 | -16 | 75 | 여유 큼 |
| `omc-plan` | 60 | 46 | -14 | 63 | 여유 큼 |
| `omc-review` | 60 | 57 | -3 | 57 | 상한선 맞춤 |
| `omc-critique` | 79 | 64 | -15 | 68 | 여유 있음 |

## 이번 확인 포인트

### 1. `omc-critique`

| 항목 | 값 |
|---|---:|
| Before | 79 |
| After | 64 |
| Delta | -15 |
| Reduction | 19.0% |
| Contract Limit | 68 |
| Remaining Headroom | 4 |
| Current Chars | 2472 |

- 판단: `omc-critique`는 토큰 효율성 관점에서 의미 있는 압축입니다.
- 이유: 절감 폭이 크고, 핵심 계약인 `PLAN/CODE 모드`, `VERDICT`, `변경 비용 추정`, `다음 추천`을 유지한 채 줄였습니다.
- 해석: 자주 호출되는 비판 스킬에서 고정 프롬프트 오버헤드를 약 19% 줄였으므로 proxy 기준 개선 효과가 분명합니다.

### 2. `omc-review`

| 항목 | 값 |
|---|---:|
| Before | 60 |
| After | 57 |
| Delta | -3 |
| Reduction | 5.0% |
| Contract Limit | 57 |
| Remaining Headroom | 0 |
| Current Chars | 2592 |

- 판단: `omc-review`도 개선은 맞지만, 토큰 효율성 효과는 제한적입니다.
- 이유: 이미 lean한 상태였고, 필수 계약인 `파일:라인`, `검증 커맨드`, `판정`, `decision/risk/next_action`, `공통 결정표`가 많아 더 줄일 공간이 거의 없습니다.
- 해석: 지금 단계에서 `omc-review` 추가 압축은 절감 이득보다 계약 훼손 리스크가 더 큽니다.

## 해석

- `omc-task`: 단계 설명과 안전 항목을 한 줄 중심으로 압축해 절감 폭이 가장 컸습니다.
- `omc-plan`: CONTRACT, 최소 설계, TDD 예시를 평탄화해 큰 폭으로 줄었습니다.
- `omc-review`: 이미 lean 상태라 미세 압축만 적용했고, 계약 상한선 `57`에 맞춰졌습니다.
- `omc-critique`: 모드 설명, verdict, 비용 체크포인트를 압축해 높은 절감 효과를 얻었습니다.

## 품질 메모

- 이번 리포트는 토큰 사용량 실측이 아니라 `스킬 프롬프트 길이` 기반의 proxy benchmark입니다.
- `omc-review`는 추가 절감 여지가 작고, `omc-task` / `omc-plan` / `omc-critique`는 절감 대비 품질 손상 없이 정리된 편입니다.
- 다음 최적화 우선순위는 길이 자체보다 `출력 계약 유지율`과 `다음 스킬 추천 정확도`를 함께 보는 것이 좋습니다.

## 권장 후속

1. `omc_skill_benchmark.py` 결과에 이 리포트 표를 함께 링크
2. 실제 세션 로그 기준으로 출력 길이와 next action 정확도를 같이 비교
3. 이후 압축 작업은 `omc-review`보다 read-only 스킬이나 반복 설명이 큰 스킬부터 검토

## 압축 우선순위 결론

- 계속 압축할 가치 높음: `omc-critique`
- 추가 압축 중단 권장: `omc-review`
- 다음 후보는 `pr-create`, `omc-task`, `omc-autopilot`처럼 절대 길이가 큰 스킬이 더 낫습니다.
