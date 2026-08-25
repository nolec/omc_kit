# Bounded N-child Acceptance

OMC의 N-child 실행 경로는 고정된 5개 case를 모두 통과하기 전까지 production-ready로 판정하지 않는다.

## 고정 Case

| Case | Child | 기대 결과 |
|---|---:|---|
| `success-3-child` | 3 | `completed` |
| `success-5-child` | 5 | `completed` |
| `provider-failure-3-child` | 3 | `parent_review` |
| `provider-timeout-3-child` | 3 | `parent_review` |
| `scope-policy-violation-3-child` | 3 | `parent_review` |

case 구성, 순서, packet hash, source commit, threshold 중 하나라도 바뀌면 기존 manifest는 무효다.

## Provider 계약

`scripts/omc_provider_exec_adapter.py`는 `OMC_PROVIDER_BACKEND`가 가리키는 실행 파일과 handshake한다. 백엔드는 다음 capability를 모두 제공해야 한다.

```json
{
  "protocol": "omc-provider-backend/v1",
  "hard_total_token_limit": true,
  "hard_output_limit": true
}
```

둘 중 하나라도 없으면 외부 호출 전에 차단한다. 실행 후 token receipt가 없거나 서명된 한도를 넘겨도 결과를 적용하지 않는다. adapter와 scheduler는 별도로 응답 바이트와 timeout을 제한한다.

## Packet 준비

임의의 축약 JSON을 직접 만들지 않는다. 고정된 다섯 packet은 production 코드의
deterministic generator로 생성한다.

```python
from pathlib import Path
from scripts.omc_n_child_acceptance import write_acceptance_fixture_packets

write_acceptance_fixture_packets(Path("/path/to/source"), Path("/path/to/packets"))
```

생성기는 유효한 v2 children, child grants, prompts, aggregate budget과 scope 파일을
함께 만든다. fixture source를 커밋한 뒤 manifest를 준비한다.
`approval.proposal_sha256`는 case별 격리 clone의 target identity에 맞춰 runner가 결합한다.

## 실행

```bash
python3 scripts/omc.py n-child-acceptance prepare \
  --source-root . \
  --packet-root /path/to/packets \
  --acceptance-id n-child-production-v1 \
  --out /path/to/manifest.json

python3 scripts/omc.py n-child-acceptance validate \
  --manifest /path/to/manifest.json

OMC_PROVIDER_BACKEND=/path/to/hard-limit-backend \
python3 scripts/omc.py n-child-acceptance run \
  --manifest /path/to/manifest.json \
  --packet-root /path/to/packets \
  --source-root . \
  --artifact-root /path/to/new-artifact-directory \
  --provider-adapter scripts/omc_provider_exec_adapter.py
```

runner는 source HEAD가 manifest의 commit과 정확히 일치할 때만 시작한다. 각 case는 별도 detached clone에서 실행되고 원본 worktree에는 patch를 적용하지 않는다. artifact 경로를 재사용하지 않으며 자동 retry, model switching, ship은 수행하지 않는다.

## 판정

PASS 조건은 다음과 같다.

- 고정 5개 case 결과와 영수증이 모두 존재한다.
- success 2건이 모두 완료된다.
- failure, timeout, policy violation은 모두 parent review로 전환된다.
- 중복 실행, scope 위반 적용, budget 위반 수용, 실패 patch 적용, receipt 누락이 모두 0이다.

판정 지표는 결과 JSON의 독립 필드를 신뢰하지 않는다. DAG/child ledger에서 산출한
`metrics`와 ledger hash를 receipt 내부에 포함한 뒤 receipt hash로 함께 봉인한다.
ledger가 없거나 형식이 잘못되면 지표를 0으로 간주하지 않고 `unverified`로 차단한다.
알 수 없는 추가 case와 executor 예외도 각각 실패 및 fail-closed receipt로 남는다.

기존 artifact를 다시 판정할 때도 임의의 `results.json`만 입력하지 않는다. `finalize`는
artifact root 아래의 result, case receipt, DAG ledger, child ledger를 모두 다시 읽고 hash,
metric, raw result binding을 재계산한다.

```bash
python3 scripts/omc.py n-child-acceptance finalize \
  --manifest /path/to/manifest.json \
  --artifact-root /path/to/artifact-directory \
  --out /path/to/report.json
```

이 acceptance는 실행 안전성 판정이다. provider별 품질, 비용, p50/p95 latency 비교는 별도 benchmark로 수행한다.
