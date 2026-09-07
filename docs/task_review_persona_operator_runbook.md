# Task Review Persona Pilot Operator Runbook

이 문서는 `task-review-persona-effectiveness-20260904-v1`의 외부 custody 실행 절차다. OMC는 private key를 받거나 서명하지 않으며 canonical payload 생성과 receipt 검증만 담당한다.

## 시작 전

다음 네 공개키를 operator 환경에 설정한다.

- `OMC_TASK_REVIEW_PILOT_TRUSTED_EXECUTION_PUBLIC_KEY`
- `OMC_TASK_REVIEW_PILOT_TRUSTED_RECONCILIATION_PUBLIC_KEY`
- `OMC_TASK_REVIEW_PERSONA_TRUSTED_ADJUDICATION_PUBLIC_KEY`
- `OMC_TASK_REVIEW_PERSONA_TRUSTED_STUDY_PUBLIC_KEY`

공개키를 준비한 뒤 아래 순서를 바꾸지 않는다. machine-readable authoritative 순서는 preregistration의 `execution.common_sequence`다.

1. study·reconciliation authority가 8개 canonical calibration fixture 본문, 각 본문의 hash와 숨긴 gold hash를 먼저 공동 서명한다. fixture에는 failure boundary 정답을 노출하지 않는다. adjudicator가 gold를 보지 않고 answer를 서명한 뒤 두 authority가 gold를 공개한다. qualification은 boolean·criterion pass·failure boundary 판정만 비교하며 evidence와 correction instruction은 필수성만 검증하고 문구는 비교하지 않는다.
2. T0 전에 study 표본에서 제외되는 synthetic rehearsal을 완료한다. rehearsal은 source commit·preregistration hash·네 authority와 signing payload roundtrip, synthetic execution receipt, terminal validation, blind packet validation의 typed canonical payload·개별 hash·전체 bundle hash를 결속한다. frozen custody policy도 같은 네 서명 payload에 포함한다.
3. anonymous arm mapping을 서명한다.
4. calibration qualification·rehearsal receipt·mapping hash를 포함한 registration을 공동 서명해 fresh T0를 연다. registration은 preregistration SHA-256, contract revision, 판정 임계값, 21일 deadline, canonical repository roster와 네 authority 공개키를 포함한다. `signature`와 `reconciliation_signature`를 빈 문자열로 둔 동일 payload에 study authority와 reconciliation authority가 각각 서명한다. 이 순서나 한 field라도 어긋나면 T0를 열지 않는다.

## Canonical signing payload

receipt 초안의 signature 필드를 빈 문자열로 둔 뒤 다음 명령으로 외부 signer에 전달할 exact bytes와 SHA-256을 만든다.

```bash
python3 scripts/omc_task_review_pilot.py persona-signing-payload \
  --kind registration --receipt registration.json --output registration-payload.json
```

`--kind`는 `registration`, `arm_mapping`, `enrollment`, `adjudication`, `collection_close`, `calibration_commitment`, `calibration_answer`, `calibration_gold_reveal`, `calibration_qualification`, `rehearsal` 중 하나다. 외부 custody signer는 `payload_base64`를 decode한 bytes에 Ed25519 서명하고 base64 signature를 원 receipt의 signature 필드에 기록한다. registration과 calibration의 공동 서명 문서는 두 authority가 같은 payload bytes를 서명한다. private key와 decoded payload는 저장소에 저장하지 않는다.

`rehearsal`도 같은 payload 생성 명령을 사용하며 네 signature 필드를 모두 빈 문자열로 둔 동일 bytes를 네 authority가 각각 서명한다. authority 독립성은 서로 다른 사람 수가 아니라 서로 다른 키와 정보 접근 경계로 주장한다. adjudicator는 calibration answer 전 gold, 최종 adjudication 전 arm mapping에 접근할 수 없고 executor는 adjudication을 겸할 수 없다.

## Case 실행 순서

1. pre-T0 registration·mapping binding을 다시 검증하고, 검증된 registration hash를 첫 enrollment에 사용한다. mapping이나 registration을 재생성·재서명하지 않는다.
2. 각 자연 발생 implementation을 먼저 enrollment하고 state-evidence cursor 구간을 결속한다.
3. `persona-freeze-case`와 `persona-paired-dry-run`을 순서대로 실행한다.
4. 외부 Codex 두 arm은 dry-run의 arm별 configuration을 그대로 사용한다. OMC arm에는 frozen persona contract 원문이 있고 baseline에는 명시적인 `null` marker가 있어야 한다. executor는 최종 diff와 정규화된 verification을 `omc-task-review-persona-evaluation-artifact/v1` JSON으로 만든다. exact 필드는 `schema_version`, `artifact_kind`, `content`, `content_sha256`뿐이며 arm·executor·skill·workflow metadata는 허용하지 않는다. 각 descriptor를 result에 포함해 함께 서명하고, 그 receipt로 `arm-receipt`와 `terminal-receipt`를 만든다.
5. 각 terminal 뒤 `persona-prepare-blind-evaluation`을 실행한다. subject에는 terminal receipt와 frozen request·persona contract·DoD·verification command 원문만 넣는다. packet builder가 signed terminal bundle에서 평가 artifact를 직접 추출해 중립 이름으로 봉인하며, caller가 별도 artifact를 지정할 수 없다.
6. case 10 뒤 blind adjudicator는 packet에 포함된 계약 원문과 anonymous artifact를 보고 requirement coverage·DoD completeness·incorrect completion·correction-required, persona fidelity criterion별 `pass`와 비어 있지 않은 `evidence`, OMC arm 추측과 confidence를 기록한다. 최종 `persona_fidelity_pass`는 criterion pass의 논리곱과 정확히 같아야 한다. 이 결과를 포함한 `omc-task-review-persona-adjudication/v4` receipt를 서명하고 `persona-decision`을 실행한다. arm mapping은 adjudication 서명 뒤에만 해석하며 OMC arm을 9건 이상 맞히면 `status=INCONCLUSIVE`, `reason=blinding_failed`로 종료한다.
7. 생성된 sealed decision을 즉시 다시 검증한다.

```bash
python3 scripts/omc_task_review_pilot.py persona-prepare-blind-evaluation \
  --subject terminal-bound-blind-subject.json --arm-mapping arm-mapping.json \
  --artifact-root evidence --output blind-packet.json
```

```bash
python3 scripts/omc_task_review_pilot.py persona-verify-decision \
  --decision persona-decision.json --artifact-root evidence --output verified-decision.json
```

`verified-decision.json`은 원본 decision과 canonical하게 같아야 한다. registration, mapping, enrollment, terminal, adjudication 또는 참조 evidence가 누락·변조·교체되면 검증은 구조화된 `blocked`와 exit 2로 종료한다.

## Deadline shortfall

21일 deadline에 10건이 모이지 않으면 reconciliation authority가 `collection_close` payload를 서명하고 `persona-collection-close`로만 `INCONCLUSIVE`를 발행한다. case를 교체하거나 기간을 연장하지 않는다.
