# OMC Review Synthetic Comparison

## 목적과 현재 상태

이 문서는 `omc-review`와 Codex native review의 비교 실험 계약을 관리한다. 현재 결과는 파이프라인과 fixture의 경계를 검증하는 용도이며, 운영 품질 우열이나 교체 가능성을 증명하지 않는다.

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

## 증거 제외 규칙

- provenance 오염, 출력 덧붙임, 경로/commit 불일치, 실행 실패는 최종 모수에서 제외한다.
- 수동 기록 OMC 결과와 synthetic fixture는 실행 파이프라인 확인에는 쓸 수 있지만, 실제 품질 우열 결론에는 쓰지 않는다.
- 임시 실행 폴더와 로컬 산출물 경로는 이 문서의 영구 근거로 삼지 않는다.

## 실사용 비교 게이트

교체 가능성 판단은 실사용 anonymized diff 10건이 아래 조건을 모두 만족한 뒤에만 한다.

1. 각 diff를 같은 baseline과 commit에서 isolated same-diff로 준비한다.
2. OMC와 Codex 결과를 서로 독립적으로 저장하고, 실행 명령·commit SHA·cwd·exit status·timestamp를 provenance로 남긴다.
3. 판정자는 provider 이름을 보지 않는 blind gold-label로 기대 결함과 evidence를 확정한다.
4. 핵심 이슈 탐지율, evidence 정확도, false positive를 provider별로 같은 기준으로 계산한다.

## 대체 주장 보류 조건

이 게이트 전에는 OMC review가 Codex native review를 대체한다고 주장하지 않는다. 게이트 통과 후에도 결론은 observed 10건의 표본 범위로 제한하며, 새 실사용 사례가 쌓이면 재측정한다.

## 갱신 정책

- 이 문서: fixture 변경, 실행 결과, 제외 사유, adjudication마다 갱신한다.
- 전역 로드맵: 수집 게이트 준비, 10건 실행 완료, blind gold-label 완료, 최종 비교 결론의 마일스톤 완료 때만 갱신한다.
