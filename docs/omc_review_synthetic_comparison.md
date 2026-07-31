# OMC Review Synthetic Comparison

## 목적과 현재 상태

이 문서는 `omc-review`와 Codex exec structured review의 비교 실험 계약을 관리한다. 현재 결과는 파이프라인과 fixture의 경계를 검증하는 용도이며, 운영 품질 우열이나 Codex native review-agent 교체 가능성을 증명하지 않는다.

- Source: `synthetic`
- Comparison scope: `same_diff`
- Providers: Codex CLI and OMC review
- Metrics source: `omc_review_synthetic_runtime_cases.json`
- 전역 상태판: [Automatic Model Routing Roadmap](automatic_model_routing_roadmap.md#review-quality-validation)

## Historical Pilot

기존 4건 controlled case는 historical pilot이다.

| Provider | Completed | Critical-or-higher hits | Misses | False positives | Evidence matches | Provenance |
|---|---:|---:|---:|---:|---:|---|
| Codex | 4 | 4 | 0 | 0 | 4 | `cli_completed` |
| OMC review | 4 | 4 | 0 | 0 | 4 | `manual_rule_application` |

- Codex 결과는 경로를 `src/...`로 정규화한 Codex CLI 원문이다.
- OMC 결과는 reviewer basis metadata를 붙인 수동 기록이다.

두 provider가 기대 결함을 모두 잡았지만, OMC 결과가 독립 model-executor 실행이 아니라 manual rule application으로 기록됐다. 따라서 이 결과는 동률 파일럿일 뿐 최종 비교 표본이 아니다.

## Observed V5 파일럿: gold sign-off 완료

기존 산출물은 raw Git object ID 차이와 신규 파일 staging 누락 때문에 provenance가 보류됐습니다. source commit의 diff와 새로 재구성한 v4 isolated workspace를 canonical hash(`canonical_review_diff_sha256(git diff --binary HEAD)`)로 재검증한 결과 10/10 일치했습니다.

| 지표 | Codex exec structured review | OMC review |
|---|---:|---:|
| 전체 이슈 탐지율 | 3/8 (37.5%) | 6/8 (75.0%) |
| P0/P1 탐지율 | 2/4 (50.0%) | 3/4 (75.0%) |
| 미탐 | 5 | 2 |
| 오탐 후보 | 3 | 6 |
| evidence 위치 유효율 | 1/8 (12.5%) | 2/8 (25.0%) |
| 입력 토큰 | 2,023,434 | 2,087,770 |
| 출력 토큰 | 14,044 | 16,325 |

gold-label 초안은 사용자가 그대로 승인했고, 위 지표는 당시 실행의 역사적 비교값으로 보존합니다. 핵심 수치는 P0/P1 gold finding 4건을 기준으로 계산합니다. OMC는 전체·P0/P1 탐지율은 높지만 오탐 후보가 6건으로 Codex의 3건보다 많았습니다. 다만 provider 원문은 외부 임시 경로에만 있었고 현재 저장소에 보존돼 있지 않아, 이 결과는 재현 가능한 Codex native review-agent 대체 근거가 아닙니다. 상세 hash와 원문 미보존 상태는 `scripts/fixtures/omc_review_v5_raw_evidence_manifest.json`에 기록합니다.

## 증거 제외 규칙

- provenance 오염, 출력 덧붙임, 경로/commit 불일치, 실행 실패는 최종 모수에서 제외한다. raw Git object ID는 canonical hash에서 정규화하고, 신규 파일은 baseline 이후 staged diff로 포함한다.
- 수동 기록 OMC 결과와 synthetic fixture는 실행 파이프라인 확인에는 쓸 수 있지만, 실제 품질 우열 결론에는 쓰지 않는다.
- 임시 실행 폴더와 로컬 산출물 경로는 이 문서의 영구 근거로 삼지 않는다.

## 실사용 비교 게이트

교체 가능성 판단은 실사용 anonymized diff 10건이 아래 조건을 모두 만족한 뒤에만 한다.

1. 각 diff를 같은 baseline과 commit에서 isolated same-diff로 준비하고, provider 실행 직전과 직후의 canonical hash를 모두 기록한다. V5 historical batch는 이 단계가 10/10 일치했다.
2. OMC와 Codex native review-agent 결과를 서로 독립적으로 영구 저장하고, 실행 명령·commit SHA·cwd·exit status·timestamp를 provenance로 남긴다. V5는 Codex exec structured review 원문이 외부 임시 경로에만 남아 이 조건을 충족하지 않는다.
3. 판정자는 provider 이름을 보지 않는 blind gold-label로 기대 결함과 evidence를 확정한다. V5의 사용자 sign-off는 역사적 비교값의 gold-label로 보존한다.
4. 핵심 이슈 탐지율, evidence 정확도, false positive를 provider별로 같은 기준으로 계산한다.

## 대체 주장 보류 조건

이 게이트 전에는 OMC review가 Codex native review-agent를 대체한다고 주장하지 않는다. 특히 V5 raw output이 보존되지 않았으므로 대체 판정의 입력으로 사용하지 않는다. 게이트 통과 후에도 결론은 observed 10건의 표본 범위로 제한하며, 새 실사용 사례가 쌓이면 재측정한다.

## 갱신 정책

- 이 문서: fixture 변경, 실행 결과, 제외 사유, adjudication마다 갱신한다.
- 전역 로드맵: 수집 게이트 준비, provenance 통과, 10건 provider 실행 완료, blind gold-label 완료, 최종 비교 결론의 마일스톤 완료 때만 갱신한다. V5는 원문 미보존 historical batch이므로, native review-agent 결과를 영구 보존하는 재실행이 다음 단계다.
