# Task Review Persona Pilot Operator Runbook

이 문서는 `task-review-persona-effectiveness-20260904-v1`의 외부 custody 실행 절차다. OMC는 private key를 받거나 서명하지 않으며 canonical payload 생성과 receipt 검증만 담당한다.

## 시작 전

다음 네 공개키를 operator 환경에 설정한다.

- `OMC_TASK_REVIEW_PILOT_TRUSTED_EXECUTION_PUBLIC_KEY`
- `OMC_TASK_REVIEW_PILOT_TRUSTED_RECONCILIATION_PUBLIC_KEY`
- `OMC_TASK_REVIEW_PERSONA_TRUSTED_ADJUDICATION_PUBLIC_KEY`
- `OMC_TASK_REVIEW_PERSONA_TRUSTED_STUDY_PUBLIC_KEY`

anonymous arm mapping을 먼저 서명한다. registration은 그 서명된 mapping의 canonical SHA-256과 preregistration SHA-256, contract revision, 판정 임계값, fresh T0와 21일 deadline, canonical repository roster와 네 authority 공개키를 포함한다. registration의 `signature`와 `reconciliation_signature`를 빈 문자열로 둔 동일 payload에 study authority와 reconciliation authority가 각각 서명한다.

## Canonical signing payload

receipt 초안의 signature 필드를 빈 문자열로 둔 뒤 다음 명령으로 외부 signer에 전달할 exact bytes와 SHA-256을 만든다.

```bash
python3 scripts/omc_task_review_pilot.py persona-signing-payload \
  --kind registration --receipt registration.json --output registration-payload.json
```

`--kind`는 `registration`, `arm_mapping`, `enrollment`, `adjudication`, `collection_close` 중 하나다. 외부 custody signer는 `payload_base64`를 decode한 bytes에 Ed25519 서명하고 base64 signature를 원 receipt의 signature 필드에 기록한다. registration의 두 authority는 같은 payload bytes를 서명한다. private key와 decoded payload는 저장소에 저장하지 않는다.

## Case 실행 순서

1. anonymous arm mapping을 먼저 서명하고, 그 receipt hash를 포함한 registration을 공동 서명해 case 1 전에 고정한다.
2. 각 자연 발생 implementation을 먼저 enrollment하고 state-evidence cursor 구간을 결속한다.
3. `persona-freeze-case`와 `persona-paired-dry-run`을 순서대로 실행한다.
4. 외부 Codex 두 arm의 signed execution receipt로 `arm-receipt`와 `terminal-receipt`를 만든다.
5. case 10 뒤 blind adjudication을 서명하고 `persona-decision`을 실행한다.
6. 생성된 sealed decision을 즉시 다시 검증한다.

```bash
python3 scripts/omc_task_review_pilot.py persona-verify-decision \
  --decision persona-decision.json --artifact-root evidence --output verified-decision.json
```

`verified-decision.json`은 원본 decision과 canonical하게 같아야 한다. registration, mapping, enrollment, terminal, adjudication 또는 참조 evidence가 누락·변조·교체되면 검증은 구조화된 `blocked`와 exit 2로 종료한다.

## Deadline shortfall

21일 deadline에 10건이 모이지 않으면 reconciliation authority가 `collection_close` payload를 서명하고 `persona-collection-close`로만 `INCONCLUSIVE`를 발행한다. case를 교체하거나 기간을 연장하지 않는다.
