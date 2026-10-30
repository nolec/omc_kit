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

## Autopilot 최종 보고의 C1 진단

Autopilot `expect.checks` 실행 결과는 실패 시 기존 C1 진단 함수를 통해 `diagnosis`(classification, label, reason, remaining_action)를 저장하고 `[AUTOPILOT REPORT]`에 표시한다. 기준 gate 식별자는 check의 `id`, 없으면 `label`, 없으면 명령 문자열이다. 기존 기준 기록의 gate ID와 argv가 모두 일치해야 기존·신규 비교가 가능하며, 기준 부재·손상·비교 불가 출력은 `unknown`이다. 비교에는 표시용 500자 요약이 아닌 stdout·stderr 원문을 사용한다. 기준 기록을 자동 생성·교체하지 않는다.

성공에는 실패 진단을 붙이지 않고 dry-run은 진단하지 않는다. 파일 존재 검사에는 명령 실패 분류를 적용하지 않는다. 직접 Python 검증 파일 부재는 실행 불가로 유지하며 경로·파일 확인을 안내한다. 진단은 참고 정보다. 기존 실패라도 expect 검증 실패는 완료를 차단하며, 모델 자유 응답의 완료 선언은 실행기 최종 상태를 대체하지 않는다. 이 연결은 실행기 보고에 대한 것으로 모델 응답 문구를 보장하지 않는다.

check의 ID·label이 문자열이 아니거나 비어 있으면 `unknown / invalid_diagnostic_metadata`로 안내한다. 표시 label은 명령 요약으로 대체하며 원래 검증 종료 상태와 완료 차단을 유지한다.


## Ship 진입 계약 v1 — 설정 승인과 실행 준비 분리

원본 SSOT는 templates/.agents/skills/omc-ship/SKILL.md와 templates/.claude/commands/ship.md의 동일 omc-ship-entry/v1 정형 블록이다. 스킬의 명령·입력·중단 조건과 직접 CLI 회귀는 같은 블록을 사용한다. 별도 실행기 추가나 모델 절차 준수 보장이 아니다.

최종 구현·문서·버전 커밋을 먼저 고정하고, 원본 리뷰·설정·명시적 실행 승인을 확인한다. 원본은 읽기 전용, control은 새 제어/기록 폴더, candidate는 prepare가 소유한 새 복제본이다. 기존 변경·인덱스·검증 원문은 보존한다.

순서는 control 초기화/가드 → 원본 리뷰·품질 승인 확인 → prepare → 격리 리뷰 capture/seal/record·품질 승인 연결 → check → 격리 리뷰·품질·TDD 재확인 → 별도 승인된 run이다. prepare 성공만으로 quality status가 ready가 되지 않는다. 원본 영수증이나 승인 범위를 자동 복사하지 않는다.

preflight의 quality_config_sha256는 설정 파일 원문 해시이고 quality approve의 config_sha256는 정규화된 설정 해시다. --allow-full은 동일 후보·설정의 명시적 full 실행 승인이 있을 때만 전달한다. 리뷰 원문 해시와 전체 expected-path 목록, 기준·후보 커밋을 고정한다. 후보나 설정이 달라지면 중단한다.

준비 총480초(입력40·prepare240·승인 연결40·check90·정리/보관70), prepare/check 내부 예산 포함. 새 명령은 남은 구간과 최종 보관 예산이 확보될 때만 호출한다. 조기 중단 마감은 min(중단+70초,T0+480초)다. 전체 품질 검사는 별도의 승인된 실행 횟수·총마감·정리/보관 예산이 필요하며 미지정 시 실행하지 않는다.

prepare/check의 종료 코드0·stdout baseline_verified·operation·계약/대상·cleanup_confirmed·evidence_complete·외부 시간 확인을 함께 소비한다. evidence_only final.json의 requires_cli_completion만으로 성공을 주장하지 않는다. stdout/stderr/종료 코드·디스크 원문·해시를 보관한다. 종료 미확인은 부분 증거로 남기고 원문을 덮어쓰지 않는다.

기존 ready는 설정 승인 의미를 유지한다. 설정 승인됨·실행 준비됨·검사 통과·배포 완료는 스킬 보고에서 구분하고 기존 CLI status와 종료 코드를 바꾸지 않는다. 준비 불가·승인 불일치·코드 실패·증거/종료 미확인은 별도로 보고한다.

회귀는 템플릿의 정형 블록을 읽어 임시 저장소 공개 CLI를 실행한다. 순서/prepare 누락·원본 쓰기 명령·승인 누락·후보 변경을 주입해 품질 실행0회를 확인한다. 실제 합성 검사 실패와 원문/시간 미확인을 구분한다. 설치 검사는 미커밋 후보5개 파일을 해시·권한 그대로 복제한 source kit에서 새 임시 사용처를 생성하고 task/ship 생성 결과를 확인한다. 원본 checkout·실제 사용처는 설치하지 않는다.

이 회귀 통과는 실제 프로젝트 출하·사용처 스킬 동기화·업무 효과의 증거가 아니다. 실제 홈 NOT_VERIFIED·비교 HOLD는 별도 검증 전까지 유지한다.

### Git으로 관리되는 `.omc`의 prepare 재사용

prepare는 매번 새 target·record 폴더를 독점 예약합니다. 기존 대상·기록 폴더를 받아들이거나 삭제하지 않습니다. 다만 이번 prepare가 새로 소유한 clone 안에서 고정 HEAD가 복원한 일반 `.omc` 디렉터리는 다음 조건으로만 재사용합니다. CLI 인자·계약 schema·출하 승인 정책은 바뀌지 않습니다.

| 검사 시점 | 허용 자료와 판정 |
|---|---|
| clone 직후 인수 | HEAD의 추적 `.omc` 파일과 필요한 부모 폴더만 허용합니다. 실제 내용·종류·실행 권한과 목록을 대조하고 추가 파일·빈 폴더·링크·삭제·교체를 차단합니다. 추적 `.omc/state`, `.omc/verification`, `.omc/verification-bases` 및 `.omc/pipeline_session.json`은 이전 런타임 증거 충돌로 차단합니다. |
| prepare 설정 생성 후 | 위 자료에 이번 계약 원문 해시의 `quality-gates.json`만 추가 허용합니다. 기존 설정은 내용이 같아도 덮어쓰지 않으며 폴더·깨진 링크·생성 경합도 차단합니다. 추적 파일과 기존 디렉터리 권한은 변경하지 않습니다. |
| 새 승인 결속 후 check | clone 직후의 엄격한 목록 제한을 재적용하지 않습니다. 해당 후보에 공개 CLI로 새로 만든 승인 기록을 허용하되 기존 HEAD·기준 ref/OID·조상·변경 범위·추적 내용·설정 해시·검사 전후 보존 검증을 유지합니다. 승인 진위·범위·후보 일치는 별도 `validate-ship`·quality `status`가 판정합니다. |

HEAD에도 실제 경로에도 `.omc`가 없으면 기존 새 폴더 생성 방식을 유지합니다. 추적 근거 없이 clone에 주입된 기존 폴더는 빈 폴더라도 거부합니다. HEAD에 추적 자료가 있는데 디렉터리가 사라졌으면 다시 만들지 않고 중단합니다. 원본의 미추적 승인·상태를 복사하거나 prepare가 새 승인을 발급하지 않습니다. check의 `baseline_verified`만으로 승인 READY나 출하 가능을 주장하지 않습니다.

clone 반환 후 다른 Git 명령 전에 첫 nofollow lstat와 디렉터리 fd를 고정합니다. 최초 관측 전에는 HEAD 대비 내용·목록·종류를 확인하며, 동일 내용으로 교체된 사실 자체는 탐지 보장 밖입니다. 최초 lstat/open 사이와 fd 고정 후 검증·쓰기 전후에는 경로·fd·부모 식별 변경을 차단합니다. 쓰기는 고정 fd 상대 배타적 생성으로만 수행하며 대체 경로나 링크 대상에는 쓰지 않습니다. 이는 신뢰된 부모에서의 소유권 검사이며 악의적인 동일 UID에 대한 보안 격리를 보장하지 않습니다.

prepare240초(작업210초)·check90초(작업60초), 기존 정리20초·기록10초를 유지합니다. 새 목록 순회와 읽기·쓰기 경계도 작업 마감에 포함하며 실패·마감 초과·증거 미확인을 성공으로 완화하지 않습니다. 디스크 evidence_only 결과만으로 CLI 정상 종료를 대신하지 않습니다.

직접 회귀는 폐기 가능한 Git fixture에서 추적/비추적 `.omc`의 prepare→check, 새 후보 승인 capture/seal/record·quality approve→check→기존 승인 소비자, 승인 누락·변조, 관측 시점별 교체, 충돌·링크·설정 생성 경합·오류·시간 경계를 확인합니다. 실패 여부뿐 아니라 의도한 단계·사유와 대체 경로 보존을 함께 대조합니다. 이 fixture의 합성 승인은 실제 후보 출하 승인이 아니며, 회귀 통과가 실제 prepare 재실행·전체 검사·재설치 권한을 의미하지 않습니다.

설정 생성 직전에는 마지막 HEAD 검사 뒤의 실제 메타데이터 목록을 다시 대조합니다. 첫 목록 검사와 HEAD 검사 사이에 추가된 파일·폴더도 설정을 쓰기 전에 차단합니다. 직접 회귀는 이 구간에 파일/폴더를 각각 주입해 `config` 단계·`cloned_metadata_inventory_mismatch`, 주입 항목 보존과 설정 미생성을 함께 확인합니다.
