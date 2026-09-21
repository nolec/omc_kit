# OMC Kit Quality Gate Contract

이 문서는 OMC Kit 자체의 ship 품질 게이트가 무엇을 실행하고 왜 그 범위를
선택하는지 고정한다. 일반 제품 설명과 사용 안내를 담는 `README.md`는 이
계약의 evidence가 아니다.

## Local ship gate

```text
python3 -m pytest scripts -q -m "not slow"
```

- 목적: OMC Python 기능의 빠른 회귀 검증
- 범위: `full`
- required: `true`
- timeout: 3,600초
- 제외: `slow` marker가 붙은 health 검증. 이는 일상 ship gate가 아니라
  별도 장시간 검증으로 실행한다.

## CI 관계

`.github/workflows/omc-ci.yml`은 이 계약의 두 번째 evidence다. CI는 Linux에서
shell-independent 전체 suite를, macOS에서 shell-dependent hook tests를 실행한다.
따라서 CI 명령은 local ship 명령과 문자열까지 동일하지 않으며, 둘 중 하나를
다른 하나의 대체 근거로 취급하지 않는다.

## Evidence 정책

명시적 migration을 적용한 OMC Kit의 `.omc/quality-gates.json`은 다음 두 파일만
evidence로 결속한다. migration 전의 기존 config는 그 기존 evidence와 approval
상태를 유지한다.

1. `docs/quality_gate_contract.md`
2. `.github/workflows/omc-ci.yml`

README의 제품 상태, roadmap, 예시, 일반 문서 변경은 local ship 검증 계약을
변경하지 않는다. 반대로 위 두 evidence 중 하나가 바뀌면 승인 receipt는 stale이
되어야 한다.

## 기존 설정 migration

`setup --force`는 사용처가 소유한 `.omc/quality-gates.json`을 변경하지 않는다.
README를 evidence로 가진 기존 설정도 자동으로 바꾸지 않는다. 각 저장소에서
현재 config SHA를 확인한 뒤, 위 두 evidence SHA와 동일한 gate 명령을 포함한
proposal을 작성해 CAS로 적용하고 별도로 승인한다.

```text
python3 scripts/omc_quality_gate.py --target . status
python3 scripts/omc_quality_gate.py --target . proposal-validate <proposal.json>
python3 scripts/omc_quality_gate.py --target . proposal-apply <proposal.json> \
  --expected-current-sha256 <current-config-sha256>
python3 scripts/omc_quality_gate.py --target . approve --config-sha256 <new-config-sha256>
```

proposal·approval 이전에는 기존 설정을 삭제하거나 README evidence만 제거하지
않는다. 손상되었거나 parse할 수 없는 config의 복구는
`docs/omc_quality_gates.md`의 raw-file-hash 절차를 따른다.
