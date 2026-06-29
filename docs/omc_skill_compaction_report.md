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

## 최근 커밋 요약

### Commit `b3e675b` `Further compact omc-task skill`

| Skill | Before | After | Line Delta | Char Delta | Notes |
|---|---:|---:|---:|---:|---|
| `omc-task` | 59 | 51 | -8 | -3 | 2차 압축에서도 실제 길이 감소 확인 |

- 해석: `omc-task`는 이미 한 번 줄인 뒤에도 단계 헤더와 반복 문구를 더 평탄화할 여지가 있었고, 줄 수 기준으로는 의미 있는 추가 개선이 나왔다.
- 판단: `토큰 절감 + 구조 압축`이 함께 있었던 좋은 2차 압축 케이스.

### Commit `3f90a32` `Compact orchestration and planning skills`

| Skill | Before | After | Line Delta | Char Delta | Notes |
|---|---:|---:|---:|---:|---|
| `omc-autopilot` | 55 | 47 | -8 | -47 | 실제 텍스트 총량도 줄어든 우수 케이스 |
| `omc-critique` | 64 | 57 | -7 | -1 | 구조 압축 중심, 토큰 절감은 약함 |
| `omc-plan` | 46 | 40 | -6 | -3 | 구조 압축 중심, 텍스트 총량 변화는 작음 |
| `pr-create` | 66 | 55 | -11 | -6 | 절차형 설명 평탄화, 토큰 효과는 제한적 |

- 해석:
  - `omc-autopilot`은 `lines + chars`가 함께 줄어든 좋은 압축이었다.
  - `omc-critique`, `omc-plan`, `pr-create`는 `chars` 감소폭이 작아 실제 토큰 절감보다는 스캔 속도와 가독성 개선 쪽 가치가 컸다.
- 판단:
  - 앞으로는 `줄 수 감소`만으로 계속 압축하지 말고, `chars도 함께 줄어드는지`를 같이 보며 ROI를 판단하는 편이 낫다.

## 후속 기준 제안

- 계속 압축 진행:
  - `line 감소`와 `char 감소`가 함께 보이는 스킬
  - 반복 호출 빈도가 높아 고정 프롬프트 오버헤드가 큰 스킬
- 압축 중단 또는 보류:
  - `line 감소`는 있지만 `char 감소`가 거의 없는 스킬
  - 계약이 이미 빽빽해서 더 줄이면 안전장치 훼손 위험이 큰 스킬
