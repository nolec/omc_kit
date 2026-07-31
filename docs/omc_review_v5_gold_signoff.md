# OMC Review V5 Gold Sign-off

## 결정 방법

각 행의 `판정`만 확인해 주세요.

- `승인`: 이 finding이 실제 버그이고 파일·라인·심각도가 맞음
- `수정`: 오탐, 위치 오류, 또는 심각도 수정이 필요함
- `없음 승인`: 해당 diff에 actionable issue가 없음

기존 gold-label 초안을 사용자가 그대로 승인했습니다. 다만 V5 provider 원문은 외부 임시 경로에만 있었고 현재 보존돼 있지 않습니다. 따라서 아래는 Codex exec structured review의 역사적 비교값이며, Codex native review-agent 대체 판정에는 사용하지 않습니다.

## 예비 재평가

기존 adjudication 기록은 보존하며, 동일 diff provenance는 당시 10/10 확인했습니다. Codex exec structured review와 OMC 모두 동일 isolated workspace에서 10건 실행한 것으로 기록돼 있으나, provider 원문이 현재 재검증 가능하지 않으므로 아래 결과는 역사적 gold-label 기준값으로만 유지합니다.

| Provider | Gold hit | Gold miss | False-positive 후보 | Evidence 위치 일치 | 입력+출력 토큰 |
|---|---:|---:|---:|---:|---:|
| Codex exec structured review | 3 | 5 | 3 | 1 | 2,037,478 |
| OMC review | 6 | 2 | 6 | 2 | 2,104,095 |

OMC는 이 역사적 비교값에서 탐지력은 높지만 오탐 후보가 더 많고 총 토큰도 약 3.3% 높습니다. raw output 보존과 native review-agent 실행이 빠졌으므로 현재 결론은 `대체 판정 보류`입니다. 다음 검증은 durable raw output을 남긴 동일 10건 native review-agent 재실행이어야 합니다.

| Case | Evidence | 예비 판단 | 이유 |
|---|---|---|---|
| `health-aware` | `scripts/omc_health.py:72` | 반려 반영 | `--fast`는 문서와 테스트에서 slow check 생략으로 정의됨 |
| `policy-handoff` | `scripts/omc_exec.py:301-321` | 승인 유지 | balanced/cost_saver도 cost evidence가 없으면 `pending`으로 처리 |

사람의 gold-label 승인은 완료됐습니다. 다만 provider 원문이 보존되지 않아 현재 대체 판정은 `not_ready_unreproducible`입니다. 역사적 false-positive 수치는 재실행 이후에만 대체 가능성 판단에 반영합니다.

## False-positive 최종 adjudication

추가 sign-off에서 false-positive 후보 6건을 재분류했습니다. 확정 false positive는 행동 회귀가 아닌 trailing whitespace 1건뿐입니다. 나머지 5건은 현재 diff만으로 판단을 확정할 수 없는 `insufficient_evidence`로 유지합니다.

- 확정 false positive / suppression 허용: 1건 (`marketing-conversion`)
- insufficient evidence / suppression 보류: 5건
- 최종 기록: `scripts/fixtures/omc_review_v5_false_positive_final_adjudication.json`

이 결정은 OMC의 false-positive 수치를 즉시 줄였다는 뜻이 아닙니다. V5 원문이 미보존이므로 Codex 대체 판정은 `not_ready_unreproducible`이며, native review-agent 결과를 영구 보존하는 동일 10건 재실행 전까지 유지됩니다.

## 한눈에 보는 판정표

| # | Case | Gold finding | 심각도 / Evidence | 판정 |
|---:|---|---|---|---|---|
| 1 | `failed-capability` | 없음 | - | [ ] 없음 승인 |
| 2 | `executor-shadow` | `retry_limit` 상한 누락 | P2 · `omc_executor_shadow.py:124` | [ ] 승인 [ ] 수정 |
| 3 | `storefront-header` | 토큰을 두 번 읽어 cache key/request 불일치 가능 | P1 · `useRestockQuery.ts:33-42` | [ ] 승인 [ ] 수정 |
| 4 | `partner-rejection` | `rejectionReason` 빈 값 fallback 없음 | P2 · `PortfolioTable.tsx:593` | [ ] 승인 [ ] 수정 |
| 5 | `marketing-conversion` | API 순서 기반 매핑으로 KPI 오매핑 가능 | P1 · `sync_weekly_kpi.py:183` | [ ] 승인 [ ] 수정 |
| 6 | `health-aware` | compileall의 `__pycache__` 생성 | P2 · `omc_health.py:81` | [ ] 승인 [ ] 수정 |
| 7 | `health-context` | 없음 | - | [ ] 없음 승인 |
| 8 | `policy-handoff` | cost_delta 누락 hit 판정; boolean `True` 비교 오류 | P1/P2 · `omc_exec.py:301-321,332-337` | [ ] 승인 [ ] 수정 |
| 9 | `request-delegation` | 없음 | - | [ ] 없음 승인 |
| 10 | `review-boundary` | adjudication 없이 confirmed case 통과 가능 | P1 · `omc_review_compare.py:163-174` | [ ] 승인 [ ] 수정 |

> Case는 10개이고 gold finding은 8개입니다. 사용자가 기존 gold-label 초안을 승인해 정식 비교 기준으로 확정했습니다.

## Diff 열기

```bash
ROOT=/private/tmp/omc-review-observed-candidates-v5

less "$ROOT/observed-executor-shadow-v5.diff"
less "$ROOT/observed-storefront-header-v5.diff"
less "$ROOT/observed-partner-rejection-v5.diff"
less "$ROOT/observed-marketing-conversion-v5.diff"
less "$ROOT/observed-health-aware-v5.diff"
less "$ROOT/observed-policy-handoff-v5.diff"
less "$ROOT/observed-review-boundary-v5.diff"
```

## 응답 형식

표를 모두 확인한 뒤 아래처럼 답하면 됩니다.

```text
9개 finding 승인
이슈 없음 3개 승인
```

수정이 있으면 case와 이유만 적습니다.

```text
policy-handoff P2는 오탐
health-aware P2는 P3로 변경
```

V5 gold sign-off는 역사적 기록이며, 현재 대체 판정은 `not_ready_unreproducible`입니다. 다음 작업은 false-positive 후보를 조정하는 것이 아니라, native review-agent 결과를 영구 보존하며 동일 10건을 재실행하는 것입니다.
