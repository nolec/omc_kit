# OMC Quality Gate Proposal Contract

프로젝트 품질 명령은 OMC 코어가 추측하지 않습니다. 설정이 없거나 근거가 바뀌면 LLM은 아래 계약으로 후보만 제안하고 멈춥니다.

OMC Kit 자체의 local ship 명령과 CI 관계는 일반 README가 아니라
[`quality_gate_contract.md`](quality_gate_contract.md)에 고정합니다. 각 사용처는
자신의 동일한 성격의 프로젝트 전용 계약 파일을 evidence로 사용해야 합니다.

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

## 기존·신규 실패 진단

승인된 품질 명령을 변경 전 상태에서 명시적으로 실행해 기준 기록을 생성합니다.

```bash
python3 scripts/omc_quality_gate.py --target . baseline-capture
python3 scripts/omc_quality_gate.py --target . run
```

기준 실행이 실패하면 기록은 생성되지만 CLI 종료 코드는 1입니다. 성공한 기준 실행만 0을 반환합니다.
기록 위치는 .omc/state/failure-baseline.json이며 기존 파일을 자동으로 덮어쓰지 않습니다.
기준 revision·설정 해시·실행 환경과 실행 파일 해시, 생성 시각, 변경 diff 해시, 실행 원문을 보존합니다.
원문에 민감정보가 있을 수 있으므로 로컬 기준 파일은 소유자 전용 권한으로 저장하며 공유·커밋하지 않습니다.

run의 JSON 최종 보고에 diagnosis가 추가됩니다. 각 실패 gate에 기존 실패(existing), 신규 실패(new), 구분 불가(unknown), 근거 및 출력 지문을 표시합니다.
전체 stdout·stderr·종료 코드의 정확한 일치를 비교합니다. 따라서 같은 원인이어도 출력이 달라지면 신규로 분류될 수 있습니다.
혼합 출력은 gate 전체를 신규로 표시하며, 개별 실패 원인의 동일성·해결 여부를 추정하지 않습니다.
빈 출력·타임아웃·미실행·실행 오류·1MB 초과 출력은 구분 불가입니다.
직접 Python 스크립트 실행에서 파일 부재와 인터프리터의 `can't open file`·`[Errno 2]` 진단이 함께 확인되면 `missing_verification_file` 근거로 구분 불가 처리합니다. 종료 코드 2만으로 실행 불가를 추정하지 않습니다. 이 파일 부재 판별은 `python check.py` 형태에 적용되며, 래퍼·옵션·다른 인터프리터의 출력까지 일반화하지 않습니다.
기준 없음·손상·설정/환경 불일치·기준 revision이 현재 revision의 조상이 아니면 진단만 구분 불가로 표시합니다.
현재 확장 명령이 기준 명령과 다르면 해당 gate 역시 구분 불가입니다.

진단은 기존 status·종료 코드·필수 실패 차단을 바꾸지 않습니다. 기준에 이미 존재한 필수 실패도 완료를 차단합니다.
기준 실행 중 revision·설정·환경 또는 tracked diff가 변경되면 저장하지 않습니다.
tracked와 비무시 untracked 파일은 실행 전후 대조합니다. .omc 상태·ignored 파일·외부 서비스의 동시 변화까지 원자적으로 고정하지 못하므로 기준 실행 중 작업을 변경하지 않아야 합니다.
이 기능은 quality gate CLI 보고 및 `omc_tdd_check.py --run-tests`의 터미널 출력에 적용됩니다. 별도 autopilot 보고 형식이나 다른 실행기 전체의 분류 지원을 의미하지 않습니다.

작업 게이트는 검증을 한 번 실행한 결과로 진단을 계산하고, gate별 분류·사유 코드·남은 조치를 출력합니다. 성공한 gate에는 실패 진단을 표시하지 않습니다. 손상된 기준 기록은 구분 불가 안내로 처리하며 실제 필수 실패는 계속 차단합니다. 신규 분류는 전체 출력의 변화이며 이번 변경이 실패 원인이라는 인과 판정이 아닙니다.

각 실행 결과는 실행 당시 설정의 `required` 값을 함께 전달합니다. 선택 검증 실패·미실행은 참고 조치로 안내하고 완료를 차단하지 않으며, 필수 검증 실패 조치와 구분합니다.
