# OMC Quality Gate Proposal Contract

프로젝트 품질 명령은 OMC 코어가 추측하지 않습니다. 설정이 없거나 근거가 바뀌면 LLM은 아래 계약으로 후보만 제안하고 멈춥니다.

## 근거 우선순위

1. 기존 `.omc/quality-gates.json`
2. CI 설정
3. `README.md`, `ETHOS.md` 등 프로젝트 문서
4. 프로젝트 manifest와 선언된 scripts

후보에 사용한 파일은 상대 경로와 SHA-256을 기록합니다. 근거가 충돌하거나 실행 범위를 확정할 수 없으면 후보를 만들지 않고 `HOLD`합니다.

## 출력 계약

```json
{
  "schema_version": "omc-quality-gate-proposal/v1",
  "config": {
    "schema_version": "omc-quality-gates/v1",
    "base_ref": "<project-base-ref>",
    "evidence": [
      {"path": "<relative-path>", "sha256": "<sha256>"}
    ],
    "gates": [
      {
        "id": "test",
        "purpose": "test",
        "argv": ["<executable>", "<arg>", "{changed_files}"],
        "scope": "changed",
        "required": true,
        "timeout_sec": 300
      }
    ]
  },
  "rationale": [
    {
      "gate_id": "test",
      "evidence_paths": ["<relative-path>"],
      "scope_reason": "<why this scope is sufficient>"
    }
  ]
}
```

허용 placeholder는 `{changed_files}`, `{base_ref}`, `{head_ref}`뿐입니다. `full` 범위는 `full_scope_requested=true`가 있어야 후보 검증을 통과하며 실행 승인도 별도로 필요합니다.

## 검증과 승인

```bash
python3 scripts/omc_quality_gate.py --target . proposal-validate <proposal.json>
python3 scripts/omc_quality_gate.py --target . proposal-apply <proposal.json> --expect-absent
python3 scripts/omc_quality_gate.py --target . status
python3 scripts/omc_quality_gate.py --target . approve --config-sha256 <shown-sha256>
python3 scripts/omc_quality_gate.py --target . run
```

기존 설정을 교체할 때는 `--expect-absent` 대신 `--expected-current-sha256 <현재-hash>`를 사용합니다. 적용은 설정 변경만 수행하며 실행 승인이 아닙니다. **승인 전 실행 금지**입니다. 승인 영수증은 설정 hash에 결합되며 설정이나 근거 파일이 바뀌면 재승인이 필요합니다.

기존 설정이 `invalid`이면 `status`가 표시한 `config_file_sha256`을 사용해 `proposal-apply <proposal.json> --expected-current-file-sha256 <raw-file-hash>`로만 교체합니다. 이 경로는 파싱할 수 없는 기존 파일을 위한 복구 전용이며, 교체 후에도 별도 `approve`가 필요합니다.

`setup --force`는 프로젝트 소유 `.omc/quality-gates.json`을 덮어쓰지 않습니다. 설치 검증의 `quality_gate_readiness`는 `missing / invalid / approval_required / approval_stale / ready` 중 하나이며, 설치 무결성과 별도로 보고됩니다.
