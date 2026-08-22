# Automatic Model Routing Roadmap

## Current Roadmap

OMC의 제품 목표는 사용자가 모델·executor·작업 단계를 직접 조합하지 않아도 요청의 난이도·위험·실패 신호에 따라 안전한 실행 경로를 선택하는 도구 중립 오케스트레이터다. 현재 판단과 다음 작업은 이 문서를 기준으로 하며, 완료 이력과 과거 실험 원문은 [Roadmap History](automatic_model_routing_roadmap_history.md)에 보존한다.

### 현재 위치

| 트랙 | 상태 | 현재 근거 | 남은 핵심 |
|---|---|---|---|
| V1 Skill-based Routing | 완료 | task kind와 skill 기반 profile 선택 | 운영값 유지 |
| V2 Step-level Routing | 완료 | step metadata가 실제 profile 선택에 반영 | 운영 surface 미세조정 |
| V3 Failure-driven Escalation | 완료 | failure class·retry·reroute·hold decision 통합 | multi-run tuning |
| V4 Telemetry-driven Tuning | 완료 | token·cost·retry·reroute·readiness KPI 수집 | 운영 drift 감시 |
| V5 Learned Orchestrator | 부분 반영 | single child, exact 2-child 실행, bounded 3–5 child DAG grant | N-child scheduler·provider 실행 |
| Operator Experience | 진행중 | output contract와 Lite/Full routing 기반 구축 | 지연·개입 횟수 운영 검증 |

현재 OMC는 `rule-based orchestration v1`을 넘어 telemetry와 승인 계약을 갖춘 `bounded orchestration` 단계다. 완전 자동 N-child 위임, 실패 재분배, 자동 모델 전환, 자동 ship은 아직 완료되지 않았다.

### Implementation P0

**bounded N-child scheduler**를 단일 구현 최우선 작업으로 둔다.

- 입력: 승인된 `3–5` child DAG grant, dependency, prompt, scope, aggregate budget
- 선행 조건: symlink·case·glob scope 정규화와 child `approval_id` 고유성
- 실행: ready child claim, 독립 child 제한 병렬 실행, dependency 완료 후 다음 child 해제
- 안전 경계: provider call·elapsed·token budget 초과 차단, idempotency와 expiry 재검증
- 실패 정책: retry·자동 재분배·fallback·resume 없이 bounded `parent_review`로 전환
- 완료 기준: 실제 3–5 child 작업에서 중복 실행·범위 침범·예산 초과 없이 완료하고 실패·timeout이 같은 parent review 계약으로 수렴

### Operational Obligation

**Plan Batch B receipt 수집**은 Implementation P0와 별개로 중단 없이 병행한다.

- 등록된 관측 창: `2026-08-20`부터 `2026-09-18`
- 수집 계약: lock-backed implementation receipt, 최소 3개 저장소, 저장소별 최대 5건, 전체 최대 15건
- 현재 감사 상태: raw provisional receipt `7건/3개 저장소`, cap 초과 `3건` 제외, source snapshot 검증 전 `validated_eligible=0`
- 금지: 관측 창 사후 연장, quota 변경, synthetic·document·benchmark maintenance 혼입
- 다음 단계: 관측 종료 후 source snapshot 동결, universe·shortlist 10건, 독립 gold sign-off, paired 실행, blind adjudication

### Active Quality Validation

품질 대체 판정은 구현 완료와 분리한다. 같은 작업의 durable raw output과 독립 adjudication 없이는 우월성이나 완전 대체를 선언하지 않는다.

### Plan Quality Validation

- 현재 판정: 다중 저장소 기준 `NOT_PROVEN`
- 참고 근거: 단일 corpus Fresh Batch A의 `PROVISIONALLY_REPLACEABLE`은 repository-scoped pilot로만 보존
- 다음 마일스톤: Operational Obligation의 Batch B 수집과 독립 confirmatory batch 완료
- 종료 기준: 신규 disjoint Batch B 통과 후 별도 독립 confirmatory batch에서 재현해야 `REPLACEABLE`; 두 독립 배치가 primary gain·confidence gate까지 통과해야 `BENCHMARK_SUPERIOR`

### Review Quality Validation

- 현재 판정: durable native provider 원문 부재로 `NOT_PROVEN`
- 현재 근거: 실사용 anonymized diff 10건의 historical same-diff batch와 gold-label sign-off는 보존했지만 durable raw provider output이 없어 참고 evidence로만 사용한다.
- 참고 수치: Codex `3/8 hit, 3 FP`, OMC `6/8 hit, 6 FP`는 참고 수치일 뿐 대체 판정 근거가 아니다.
- 다음 마일스톤: durable raw output을 남긴 native review-agent 동일 10건 재실행, blind gold-label과 false-positive 재측정
- 종료 기준: OMC가 핵심 탐지율·evidence 정확도에서 Codex보다 높고 false-positive가 같거나 낮아야 대체 가능
- 대체 판정은 위 증거 마일스톤 완료 때만 갱신하며 historical pilot만으로 승격하지 않는다.
- 상세 계약: [OMC Review Synthetic Comparison](omc_review_synthetic_comparison.md)

### Operator Experience 1차 통합안

- 작은 작업: 안전 조건을 만족하면 Lite `task → review`
- 복잡한 작업: Full `plan → task → review`
- 모호·고위험 작업: 필요한 경우에만 brainstorm·office-hours·critique를 선행
- 목표: 품질 gate를 유지하면서 반복 확인, p50/p95 지연, input/output/total token을 줄인다.
- 현재 유효 latency 표본은 history의 운영 기록을 따르며 표본 기준 충족 전 라우팅 경계를 확정하지 않는다.

## 로드맵 검증 매트릭스

| 로드맵 완료 항목 | 실제 반영 증거 | Fugu 비교에 쓰는 축 | 판정 규칙 |
|---|---|---|---|
| V1~V4 routing | 코드·회귀 테스트·운영 telemetry | routing·cost·recovery | 문구만 있으면 `문서만 반영`, 코드와 테스트가 있으면 `반영 확인` |
| Operator Experience | observed request·latency·intervention evidence | 사용자 개입·지연 | 운영 표본까지 acceptance를 통과해야 `체감 개선 확인` |
| V5 orchestration | grant·scheduler·실제 child execution receipt | 자동 분해·위임 | grant만으로 실행 완료를 주장하지 않으며 실제 acceptance가 필요 |

Fugu 비교 문구는 `현재 상태 참조`와 `반영 검증 완료`를 구분한다. 경쟁 제품의 문서 주장과 OMC의 구현 근거도 같은 증거 수준처럼 혼합하지 않는다.

## Oh My Claude Code 벤치마크 후 개선 백로그

공식 저장소 `v4.15.10`의 Team·Autopilot·Ralph·UltraQA·멀티 provider CLI surface를 기준으로 비교했다. OMC는 승인 hash, scope·dependency·budget, provenance, fail-close 검증에서 경쟁 가능한 기반을 갖췄지만 실제 N-child 실행, 자동 복구, 자연어 중심 UX에서는 열위다. 경쟁 제품의 토큰 절감 주장과 OMC의 안전성 우위 가설은 동일 작업 직접 측정 전까지 확정 사실로 사용하지 않는다.

| 우선순위 | 개선 축 | 다음 범위 | 종료 기준 |
|---|---|---|---|
| P0 | bounded N-child 실제 실행 | scheduler·provider 실행과 budget enforcement | 실제 3–5 child acceptance 통과 |
| P1 | 자동 모드 선택과 사용자 개입 감축 | Lite/Full 자동 분기와 fail-safe 승격 | 품질 유지 + 개입·p50/p95 감소 |
| P1 | 비용·품질 운영 증거 | single-agent 대비 token·elapsed·retry·intervention 비교 | 사전 등록 10건 acceptance 통과 |
| P2 | 도구 중립 UX 차별화 | Codex·Claude Code·Gemini·Cursor 공통 receipt·readiness | 3개 이상 호스트 동일 fixture 통과 |

작업 순서는 `P0 N-child scheduler → P1 개입 감축 → P1 비용·품질 검증 → P2 멀티 호스트 UX`로 고정한다. 새로운 스킬 수를 늘리거나 경쟁 제품의 mode를 복제하는 작업은 이 acceptance를 앞당기지 않으면 우선하지 않는다.

## 제품 원칙과 금지선

- 제품 포지션: 승인된 범위와 예산 안에서 결과를 재현 가능한 receipt로 증명하는 도구 중립 오케스트레이터
- 사람의 명시 승인 없이 push·PR·deploy·delete·reset 권한을 확장하지 않는다.
- 운영 evidence 없이 자동 model switch와 자동 재분배를 열지 않는다.
- synthetic·historical pilot만으로 대체 가능성을 주장하지 않는다.
- 특정 프레임워크·테스트 도구를 OMC 코어 정책에 하드코딩하지 않는다.
- 실행 로그 없이 정책 규칙만 늘리거나 모델 선택을 블랙박스로 만들지 않는다.

## 검증과 문서 SSOT

- 현재 상태·우선순위·active validation: 이 문서
- 완료 구현·중단 실험·상세 설계 기록: [Roadmap History](automatic_model_routing_roadmap_history.md)
- Review 판정 원문: [OMC Review Synthetic Comparison](omc_review_synthetic_comparison.md)
- Plan runtime·gold·preregistration 원문: `scripts/fixtures/omc_plan_*`
- 현재 문서가 history와 충돌하면 전용 benchmark artifact를 확인하고 현재 요약을 교정한다.

## 다음 실행 순서

1. P0 선행 정책인 scope normalization과 child approval ID 고유성을 고정한다.
2. bounded N-child scheduler와 provider execution adapter를 구현한다.
3. 3–5 child 실제 acceptance와 비용·개입 telemetry를 축적한다.
4. Lite/Full 경계를 운영 지표로 조정한다.
5. 멀티 호스트 동일 fixture로 도구 중립 UX를 검증한다.

## 한 줄 결론

현재 OMC의 다음 제품 전환점은 새로운 스킬 추가가 아니라, 이미 검증한 N-child grant를 예산·범위·승인 계약 안에서 실제 실행하는 scheduler다.
