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
        "id": "runtime",
        "purpose": "preflight",
        "argv": ["<portable-executable>", "<runtime-check>"],
        "scope": "full",
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

## 여러 컴퓨터에서의 실행 계약

새 proposal의 `argv`는 저장소를 다른 컴퓨터에 clone해도 같은 의미여야 합니다. executable은 `pnpm`, `python3`처럼 PATH에서 찾는 이름 또는 `scripts/check-runtime` 같은 저장소 상대경로만 허용합니다. 절대 executable, 상위 경로(`..`), `env`·shell wrapper, `/Users/<사용자>/...`, `/home/<사용자>/...`, HOME·PATH token은 `config_not_portable`로 거부합니다. 명령은 shell 해석 없는 직접 argv로 선언하고, 환경 변수는 wrapper로 주입하지 않고 실행 환경이 준비해야 합니다. 기존 v1 설정은 읽고 실행할 수 있지만 host-bound 설정을 새 proposal로 재승인할 수는 없습니다.

프로젝트가 특정 runtime 버전을 요구하면 가장 앞에 `purpose: "preflight"`인 required·full-scope gate를 선언합니다. 이 gate는 프로젝트가 소유한 portable 검사 명령으로 runtime 버전과 필수 도구를 확인합니다. OMC는 runtime을 자동 설치하지 않으며 PATH도 수정하지 않습니다.

OMC는 모든 required gate의 실제 실행 파일과 작업 디렉터리를 먼저 탐색합니다. 상대 PATH와 executable은 실제 gate의 작업 디렉터리를 기준으로 해석합니다. 기존 v1의 `env` wrapper도 내부 command와 `-C` 변경을 확인하지만 신규 portable proposal에서는 wrapper를 허용하지 않습니다. 실행 파일이나 작업 디렉터리가 하나라도 없으면 어떤 품질 명령도 실행하지 않고 `environment_not_ready`와 누락 항목을 반환합니다. required preflight가 실패하면 뒤의 test·typecheck·lint·build는 `preflight_failed`로 건너뜁니다. optional gate의 실행 오류는 기존처럼 전체 결과를 차단하지 않습니다.

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

`setup --force`는 프로젝트 소유 `.omc/quality-gates.json`을 덮어쓰거나 local exclude에 자동으로 숨기지 않습니다. 팀이 같은 품질 명령을 재현해야 한다면 이 파일을 저장소에서 명시적으로 관리합니다. 설치 검증의 `quality_gate_readiness`는 `missing / invalid / approval_required / approval_stale / ready` 중 하나이며, 설치 무결성과 별도로 보고됩니다.
