# OMC Orchestration Usage

## 기존 코드 변경이 있는 문서 작업: 독립 clone V0

`document_only` 시작이 무관한 코드 변경으로 차단되면 원래 저장소의 가드를 수정하지 않습니다.
사용자가 승인한 문서만 독립 clone으로 가져가 작업합니다. 새 범위 옵션이나 자동 교체 기능은 없습니다.
단일 작업용 절차이며, 동시 작업이 있거나 입력 문서가 달라지면 중단하고 다시 확인합니다.

1. 원본의 HEAD, 스테이징 diff, 선택 문서 내용, 설치 receipt를 저장소 밖에 보관합니다.
   현재 커밋에 같은 경로로 존재하는 일반 Markdown 파일만 선택합니다. 절대·상위 경로, 심볼릭 링크, 새·미추적 문서 및 이름 변경한 문서는 선택하지 않습니다.
2. 승인된 별도 위치로 `git clone --no-hardlinks -- "원본 절대 경로" "격리 절대 경로"`를 실행합니다.
   원본 작업 파일이나 인덱스를 공유하지 않습니다. clone에는 원본의 미커밋 변경이 없으므로
   선택 문서의 기존 미커밋 내용을 별도로 확인하여 가져옵니다. 기존 OMC 상태·receipt는 복사하지 않습니다.
3. 새 clone의 설치 파일을 먼저 확인하고, 원본 OMC로 clone에만 setup을 실행합니다.
   disposable clone에만 `--force --skip-session-start`를 사용할 수 있으며 설치 감사가 실패하면 중단합니다.
   기존 프로젝트/OKX에 이 옵션을 적용하는 절차가 아닙니다.
4. 원본 OMC의 `scripts/omc_guard.py sync-require`에 clone을 `--target`으로 지정하고
   `--mode autopilot --title 문서작업 --request "확정한 문서 작업" --roles senior_coding
   --work-class document_only --for task`로 실제 세션을 시작합니다.
   `directive` 단독 역할에는 work_class를 지정할 수 없습니다. 작업 중 코드를 추가/변경하면 문서 작업을 계속하지 않습니다.
5. 선택 문서만 검증하고 정확한 경로로 스테이징합니다. 범위 밖 파일이 스테이징돼 있으면 커밋하지 않습니다.
   Git diff, 설치된 커밋 훅의 확인, 커밋 후 변경 경로와 `completion.json`의 document_only 결속을 확인합니다.
   원본의 HEAD·인덱스·코드·기존 문서·receipt가 시작 때와 같은지도 확인합니다.
6. 결과는 격리 clone의 커밋과 문서 diff로 제시합니다. 원본 반영은 별도 승인 후 수행하며
   원본 문서가 시작 때와 달라졌거나 충돌이 있으면 반영하지 않습니다. 자동 cherry-pick·덮어쓰기·정리는 하지 않습니다.

### 입력과 원본 변경 확인

아래 코드를 저장소 밖 임시 파일에 보관하고 `python3 <확인파일> <원본 저장소 절대 경로>
<문서 상대 경로> [시작 때 출력된 SHA-256]`으로 실행합니다. 문서를 가져오기 전에 해시를
보관하고, 원본 반영 직전에 동일 경로와 그 해시로 다시 확인합니다. 각 문서를 개별 확인합니다.
종료 상태가 1이면 중단합니다. 정상 출력은 해당 시점의 내용 확인만 뜻하며 반영 승인이 아닙니다.
동시 수정 가능성이 있으면 이 확인만으로 반영하지 않습니다. 이 코드는 파일을 쓰거나 복사하지 않습니다.
V0는 기존 문서의 내용 수정만 다룹니다. 새 문서는 스테이징해도 허용하지 않으며,
새 문서 생성·문서 이름 변경은 별도로 출처와 작업 범위를 확정해야 합니다.

<!-- OMC:document-isolation-check -->
```python
import hashlib
import stat
import subprocess
import sys
from pathlib import Path

try:
    if len(sys.argv) not in (3, 4):
        raise ValueError('인자: 원본 저장소, 문서 상대 경로, 선택적 시작 해시')
    root = Path(sys.argv[1]).resolve(strict=True)
    path = Path(sys.argv[2])
    if path.is_absolute() or not path.parts or '..' in path.parts or path.suffix.lower() != '.md' or any(p in {'.git', '.omc'} for p in path.parts):
        raise ValueError('허용되지 않은 문서 경로')
    top = subprocess.check_output(['git', '--no-optional-locks', '-C', str(root), 'rev-parse', '--show-toplevel'], text=True).strip()
    if Path(top).resolve() != root:
        raise ValueError('저장소 루트가 아님')
    committed_type = subprocess.check_output(['git', '--no-optional-locks', '-C', str(root), 'cat-file', '-t', 'HEAD:' + path.as_posix()], text=True).strip()
    if committed_type != 'blob':
        raise ValueError('현재 커밋의 일반 문서가 아님')
    committed_entry = subprocess.check_output(['git', '--no-optional-locks', '-C', str(root), 'ls-tree', '-z', 'HEAD', '--', path.as_posix()])
    records = [entry for entry in committed_entry.split(b'\0') if entry]
    if len(records) != 1:
        raise ValueError('현재 커밋에서 경로를 특정할 수 없음')
    metadata, committed_path = records[0].split(b'\t', 1)
    if metadata.split()[0] not in (b'100644', b'100755') or committed_path.decode('utf-8', errors='surrogateescape') != path.as_posix():
        raise ValueError('현재 커밋의 일반 문서 경로가 아님')
    for i in range(1, len(path.parts) + 1):
        if root.joinpath(*path.parts[:i]).is_symlink():
            raise ValueError('심볼릭 링크 경로')
    entries = subprocess.check_output(['git', '--no-optional-locks', '-C', str(root), 'status', '--porcelain', '-z']).split(b'\0')
    index = 0
    while index < len(entries) and entries[index]:
        entry = entries[index].decode('utf-8', errors='surrogateescape')
        if 'R' in entry[:2] or 'C' in entry[:2]:
            index += 1
            other = entries[index].decode('utf-8', errors='surrogateescape')
            pair = [entry[3:], other]
            if path.as_posix() in pair and any(Path(p).suffix.lower() != '.md' for p in pair):
                raise ValueError('문서가 아닌 파일에서 이름 변경됨')
        index += 1
    target = root / path
    before = target.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('일반 파일이 아님')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    after = target.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after):
        raise ValueError('확인 도중 문서 변경됨')
    if len(sys.argv) == 4 and digest != sys.argv[3]:
        raise ValueError('원본 문서가 시작 때와 다름: 반영 중단')
    print('확인 통과:', digest)
except (OSError, ValueError, subprocess.CalledProcessError, IndexError) as exc:
    print('확인 실패:', exc, file=sys.stderr)
    raise SystemExit(1)
```

회귀 검증: `python3 -m pytest scripts/test_omc_guard.py -q -k document_isolation`.
이 시험은 임시 저장소에서 기존 코드 스테이징·문서 미커밋 변경·receipt 보존과
실제 setup→세션 저장→커밋 훅→완료 기록을 검증합니다. 실제 OKX 문서 이동·반영 및 재설치는 수행하지 않습니다.

## 중단된 workflow-v2의 복구 판단

기본 중단 안내는 최대 8줄·800자의 핵심 요약만 표시합니다. 긴 제목·다수 단계·변경은
일부 생략을 명시하고, 파일 비교와 현재 재실행 보류를 구분합니다.
근거·경로·현재/목표 변경 요약·상세 diff는 동일 명령에 `--recovery-details`를 추가해 확인합니다.
이 옵션은 출력만 바꾸며 승인·재개 정책이나 파일 상태를 바꾸지 않습니다. 재개 근거 승인 명령과는 함께 쓰지 않습니다.

### OMC의 자동 확인 범위

선택적 `completion.target_path`는 `artifact_sha256` 단계에만 지정할 수 있습니다.
사용자가 제공한 작업 루트 내부 목표 파일의 바이트 해시가 `expected_sha256`과 맞아야
`현재 내용 → 목표 내용` 차이를 보여줍니다. 미지정·해시 불일치·읽기 실패이면 상세 비교 불가를 표시합니다.
상세 비교는 심볼릭 링크·특수 파일을 거부하고, 일반 UTF-8 파일당 최대 64 KiB·1,000줄로 제한합니다.
검증과 차이는 동일 읽기 스냅샷을 사용하며, 읽기 중 식별·크기·수정 정보가 달라지면 거부합니다.
차이는 최대 40줄·4,000자로 제한하고 제어문자를 이스케이프하며 생략 여부를 표시합니다.
현재 파일과 선언된 목표 파일의 역할·경로를 먼저 표시하고, 추가·삭제·교체할 내용을 요약합니다.
요약과 상세 diff는 동일 스냅샷에서 만들며 두 출력이 함께 40줄·4,000자 예산을 사용합니다.
표시한 변경은 선언된 목표와 같아지기 위한 내용이며, 사용자 의도나 이번 실행의 반영을 증명하지 않습니다.
목표 일치는 선언된 파일과의 일치만 뜻합니다. 차이를 검토해도 파일 수정이나 재개는 별도 승인입니다.

`파일 확인`은 현재 파일 상태를 완료 기록과 별도로 표시합니다. `artifact_sha256`은 선언된
목표 해시와 현재 파일 바이트를 비교해 일치·불일치를 설명하고, 파일 부재·읽기 실패도 구분합니다.
`json_object_fields`는 필수 항목 존재만 확인하므로 목표 내용 일치로 취급하지 않습니다.
목표와 일치하더라도 이번 실행이 작성했거나 외부 작업이 반영됐다는 증명이 아니며,
유효 완료 기록·선행 결속이 없으면 현재 완료 확인은 계속 불가로 남습니다.

복구 안내 첫 부분의 `OMC 확인 결과`는 현재 요청·단계·시도에 결속된 로컬 실행 관측과
선언된 산출물·완료 receipt·선행 결속을 읽기 전용으로 재검증한 결과입니다.
실행 호출 전에 요청 기록을 저장하고 실행기가 반환하면 반환 기록을 저장합니다.
요청 기록만 있으면 `실행 요청 중 중단됨`으로 표시하되 실제 호출이나 외부 반영을 단정하지 않습니다.
반환 기록도 외부 작업 완료 증명이 아니며, 파일 존재만으로 완료로 표시하지 않습니다.
완료 검증이 통과하지 않거나 관측 기록이 누락·손상·다른 요청/시도에 속하면 `확인 불가`를 표시합니다.
로컬 digest는 기록 일치 검사이며, 상태와 digest를 함께 조작하는 공격의 인증 수단이 아닙니다.
기존 실행에는 관측 기록을 소급 생성하지 않습니다. 자동 확인은 승인이나 재개를 실행하지 않으며,
외부 일부 반영·미실행 여부는 증명하지 않습니다.

provider 호출 중 실행기가 종료된 작업은 재실행 시 `manual_reconciliation_required`로 차단됩니다.
첫 안내는 **단계 상태 → 지금 할 일 → 승인할 실행 범위** 순서입니다.
`기록상 완료`는 저장된 완료 기록이고, `실행 결과 불확실`은 외부 실행 대조가 필요한 단계입니다.
상태가 없거나 해석할 수 없으면 `확인 불가`로 표시하며, 단계 순서만으로 미실행을 추정하지 않습니다.
저장된 `pending` 상태만 `대기—이번 재개에서 실행하지 않음`으로 표시합니다.
불확실한 단계가 여러 개면 모두 대조하기 전 한 단계 재개를 승인할 수 없습니다.
재개 결과에서도 선택 단계 상태와 지금 할 일을 표시하며, 승인은 한 단계에만 적용되고 후속 단계는 자동 실행하지 않습니다.
안내 이해도는 새 격리 사례의 도움 없는 사용자 응답으로 별도 확인합니다. 설명 후 수행한 사례는 독립 사용성 통과로 계산하지 않습니다.
CLI의 기록상 완료는 현재 재검증 완료를 뜻하지 않습니다. 표시된 상태 기록의 단계 상태와
`steps.<단계>.last_output_diagnostics`를 확인하세요. 실행 응답 진단은 원본 실행 로그나 외부 완료 증명이 아니며,
강제 종료된 단계에서는 미수집일 수 있습니다. 외부 provider 실행 기록은 이 안내에서 수집하지 않으므로
사용한 provider의 해당 작업 실행 이력과 외부 결과를 별도로 대조해야 합니다.

산출물은 선언된 경로와 현재 파일 존재 여부를 함께 표시합니다. `존재`는 내용 검증이나 완료 판정이 아니며,
`없음`만으로 외부 작업이 실행되지 않았다고 판단할 수 없습니다. 확인할 수 없으면 차단을 유지하세요.
단일 단계 재개 V0는 미실행이 확인된 held 단계 한 개만 명시적 승인 후 재개합니다.
상태 삭제나 완료 표시 수정으로 재실행하지 마세요. 불명확하거나 이미 실행된 단계의 완료 승격은 지원하지 않습니다.

근거 JSON에는 해당 provider 실행의 `execution_id`, 기록 출처 `source`, 시간대가 있는 확인 시각 `checked_at`,
확인자의 판단 `judgment: "not_executed"`가 필요합니다. OMC는 외부 미실행을 자동 증명하지 않으며,
실제 실행 이력·외부 결과를 확인한 사람이 승인해야 합니다. AI 판단이나 파일 부재만으로 승인하지 마세요.

```bash
# 작업 루트에서 근거 확인 후 승인 기록만 생성 (provider 실행 없음)
python3 scripts/omc.py autopilot --task-file .omc/tasks/example.json --recovery-step step-id --recovery-evidence evidence.json --recovery-reviewer reviewer-id --confirm-not-executed
# 위 승인과 현재 상태·근거가 일치할 때 단계 하나만 실행
python3 scripts/omc.py autopilot --task-file .omc/tasks/example.json --recovery-step step-id
```

승인은 저장소 경로·요청 해시·중단 상태 전체·선택 단계·근거 해시에 결속하며 기존 단계 승인도 요구합니다.
일반 실행과 재개는 동일 작업 잠금을 사용합니다. 완료 receipt·산출물·의존 단계·원본 승인을 호출 전에 재검증합니다.
승인 소비와 실행 의도를 상태에 영속화한 뒤 호출하며, 그 이후 중단되면 호출 여부를 추정하지 않고 차단합니다.
재중단 시 새 근거 확인과 승인 명령이 필요하며 이전 소비 기록은 이력으로 보존합니다.
성공해도 후속 단계를 자동 실행하지 않고 차단을 유지합니다. 실패 시 자동 재시도하지 않으며,
정확히 1회 외부 실행을 보장하지 않습니다. `expect_only`와 격리 단계 재개는 V0에서 지원하지 않습니다.

사용성 확인은 자동 테스트와 별도로 진행합니다. 격리된 중단 사례의 안내만 보고 사용자가 완료 단계,
불확실 단계, 확인할 근거, 차단 유지 이유를 찾을 수 있는지 확인하고 그 결과를 따로 기록합니다.

## 기본 원칙

- 오케스트레이션의 기본 목표는 `요청 하나를 올바른 다음 단계로 보내고, 적절한 지점에서 멈추는 것`입니다.
- 강제 진입점은 항상 CLI입니다.
- 플랫폼별 입력 방식은 달라도 종료 규칙은 같아야 합니다.
- 스킬은 안내와 추천 레이어이고, 실제 강제와 기록은 `scripts/omc.py` 계열이 맡습니다.
- 기본값은 **요청 본문에 직접 필요한 작업만 수행**하는 것입니다.
- `setup force`, `로드맵 최신화`, `커밋/푸시`, `벤치마크`, `회고`, `전파 작업`은 요청에 명시되었거나 현재 작업을 끝내는 데 필수일 때만 포함합니다.
- 애매하면 부가 작업을 자동 포함하지 말고 `별도 요청`으로 분리합니다.
- 이미 제시한 로컬 커밋 범위·그룹에 대한 짧은 선택은 `state decision-open` receipt가 같은 세션·범위·TTL을 증명할 때만 상속합니다. 이는 새 스킬 진입이나 push·PR·deploy·delete 권한이 아니며, 성공한 로컬 커밋 뒤에는 `state decision-consume`으로 1회 소비합니다.
- 커밋 그룹이 여러 개면 각 option의 `paths`에 해당 그룹 파일을 명시해야 하며, 선택되지 않은 그룹의 커밋은 consume 단계에서 거부됩니다.

## Operator Experience 기준

이 문서는 `docs/automatic_model_routing_roadmap.md`의 Operator Experience 통합안과 같은 기준으로 읽습니다.

- 다음 액션은 1개만 추천한다.
- confidence가 낮으면 reroute/delay로 보낸다.
- 출력은 한 화면에서 다음 선택을 판단할 수 있을 만큼만 남긴다.
- `plan / task`는 fast, `critique / review`는 normal을 기본으로 둔다.

## 입력 선정 기준

- 권장 입력: 가장 안정적이고, 가장 재현 가능하며, 설치 직후 바로 쓰기 쉬운 입력
- 허용 입력: 자주 쓰는 습관 입력이지만 같은 오케스트레이션 경로로 수렴 가능한 입력
- fallback: 훅, 스킬, 슬래시가 불확실할 때 항상 작동하는 CLI 입력
- 판단 기준:
  - `필수`: 이번 요청 없이는 결과가 완성되지 않는 작업
  - `부가`: 있으면 좋지만 이번 요청의 완료 조건에는 직접 필요하지 않은 작업
  - `별도 요청`: 다른 세션이나 별도 승인으로 빼야 하는 작업

| 분류 | 포함 기준 | 예시 |
|---|---|---|
| 필수 | 요청 본문에 직접 필요한가 | UI 수정, 버그 수정, 해당 기능의 최소 검증 |
| 부가 | 현재 작업을 더 좋게 만들지만 없어도 끝나는가 | README 보강, 추가 벤치마크, 회고 |
| 별도 요청 | 범위를 넓히거나 다른 저장소/세션으로 번지는가 | setup force, 로드맵 재정리, 커밋/푸시, 전파 |

## 스킬별 사고 속도 기준

모든 스킬을 항상 깊게 생각하게 만드는 것보다, **스킬별로 fast / normal 기준을 나누는 편이 더 낫습니다.**
목표는 판단 시간을 줄이되, 실패 비용이 큰 구간의 안전성은 유지하는 것입니다.

| 스킬 | 기본 모드 | fast가 유리한 조건 | normal을 유지할 조건 |
|---|---|---|---|
| `plan` | fast | 범위가 이미 좁혀졌고, 선택지가 1~2개로 줄었을 때 | 새 파일/API 변경, 범위 불명확, 위험도 높음 |
| `task` | fast | DoD가 고정됐고, 구현 범위가 한 문장으로 설명될 때 | 새 파일 생성, 여러 파일 연쇄 변경, 검증 경로가 길 때 |
| `critique` | normal | 없음 | 기본값 유지. 실패 가능성, 누락, 리스크 탐지가 목적이므로 너무 빠르게 줄이지 않는다 |
| `review` | normal | 변경 범위가 작고, diff가 단순할 때 | 회귀 가능성, 파일 영향이 넓을 때, ship 직전 |

공통 규칙:

- fast 모드는 **출력 압축**이지 **검증 생략**이 아니다.
- `plan`과 `task`는 범위가 고정되면 fast로 가도 되지만, 범위가 흔들리면 normal로 돌아온다.
- `critique`와 `review`는 속도보다 실패 탐지가 우선이라, fast를 기본값으로 두지 않는다.
- high-risk 작업, 새 파일, API 변경, 다중 파일 연쇄 변경은 normal을 기본값으로 둔다.
- 다음 단계가 애매하면 속도를 높이기보다 먼저 `plan`으로 범위를 좁힌다.

## 기본 오케스트레이션

기본 오케스트레이션은 다음 세 가지를 다룹니다.

1. 요청을 해석한다.
2. 다음 스킬 또는 실행 경로를 정한다.
3. 결과를 보여주고 멈출지, 다음 추천 1개를 줄지 결정한다.

## 확장 오케스트레이션

확장 오케스트레이션은 `autopilot`처럼 여러 단계를 자동으로 이어서 실행할 때만 사용합니다.
보통의 계획, 구현, 리뷰 요청은 기본 오케스트레이션으로 충분합니다.

### autopilot 작업 단위 기준

- 큰 로드맵은 사람 계획으로 남기고, 작은 실행 단위는 autopilot으로 넘깁니다.
- `autopilot`에는 spec이 고정된 한 태스크씩만 넘깁니다.
- task file 기준은 "작업 범위가 한 문장으로 설명되고, 입력/출력/검증이 고정된 단위"입니다.
- 범위가 흔들리거나 다음 선택이 남아 있으면 autopilot보다 `plan`이나 `review`를 먼저 사용합니다.
- autopilot은 실행 보조용이지, 로드맵 전체를 대신하는 오케스트레이터가 아닙니다.

### 제한 자동 실행 pilot

planner의 자동 실행은 기본적으로 dry-run입니다. 실제 실행은 사용자가
`--execute-simple`을 명시적으로 선택한 경우에도 아래 조건을 모두 만족할 때만 허용합니다.

- `single_task`
- `risk=low`
- `policy_confidence=high`
- `user_selection_needed=false`
- `scope_fixed=true`
- 신규 파일·API 변경·삭제·민감 경로·dirty scope 충돌 없음

```bash
python3 scripts/omc.py orchestrate \
  --request "README 오타를 고쳐줘" \
  --execute-simple
```

실행 경로는 기존 `omc_autopilot.py`의 `task -> review`만 사용합니다.
복잡하거나 불확실한 요청은 실행하지 않고 차단 사유만 출력하며, 자동 ship·commit·push는 수행하지 않습니다.

## 플랫폼별 사용 모델

| 플랫폼 | 권장 입력 | 허용 입력 | fallback |
|---|---|---|---|
| Codex | `$omc-plan`, `$omc-task`, `$omc-review` | 자연어 키워드 요청 | `python3 scripts/omc.py ...` |
| Claude Code | `/plan`, `/task`, `/review` | 추가 확인 전까지 slash command 유지 | `python3 scripts/omc.py ...` |
| Gemini CLI | `/plan`, `/task`, `/review` | 자연어 요청 | `python3 scripts/omc.py ...` |

## 시작-멈춤-다음 추천

### 계획 요청

| 플랫폼 | 권장 입력 | 기대 출력 | 멈춤 지점 | 다음 추천 |
|---|---|---|---|---|
| Codex | `$omc-plan 로그인 기능 설계` | CONTRACT, 최소 설계, TDD 태스크 | 설계 출력 직후 | `$omc-task` |
| Claude Code | `/plan 로그인 기능 설계` | CONTRACT, 최소 설계, TDD 태스크 | 설계 출력 직후 | `/task` |
| Gemini CLI | `/plan 로그인 기능 설계` | CONTRACT, 최소 설계, TDD 태스크 | 설계 출력 직후 | `/task` |

### 구현 요청

| 플랫폼 | 권장 입력 | 기대 출력 | 멈춤 지점 | 다음 추천 |
|---|---|---|---|---|
| Codex | `$omc-task 로그인 버튼 구현` | CONTRACT, RED, GREEN, GATE, handoff | 구현 완료 + gate 통과 직후 | `$omc-review` |
| Claude Code | `/task 로그인 버튼 구현` | CONTRACT, RED, GREEN, GATE, handoff | 구현 완료 + gate 통과 직후 | `/review` |
| Gemini CLI | `/task 로그인 버튼 구현` | CONTRACT, RED, GREEN, GATE, handoff | 구현 완료 + gate 통과 직후 | `/review` |

### 리뷰 요청

| 플랫폼 | 권장 입력 | 기대 출력 | 멈춤 지점 | 다음 추천 |
|---|---|---|---|---|
| Codex | `$omc-review` | 치명/중대/경미/제안 분류 리뷰 | 리뷰 출력 직후 | `$omc-ship` 또는 사용자 선택 대기 |
| Claude Code | `/review` | 치명/중대/경미/제안 분류 리뷰 | 리뷰 출력 직후 | `/ship` 또는 사용자 선택 대기 |
| Gemini CLI | `/review` | 치명/중대/경미/제안 분류 리뷰 | 리뷰 출력 직후 | `/ship` 또는 사용자 선택 대기 |

## 운영 메모

### Hub 자동 동기화 폐기

- `omc_hub_push.py`의 복사·commit·push·dry-run은 폐기되었습니다. CLI는 안내 후 exit 2로 종료하며 파일이나 Git 상태를 변경하지 않습니다.
- `omc-hub-push.sh`는 구형 hook 호출 호환을 위한 조용한 exit 0 stub입니다. 신규 설치 템플릿에는 Hub 자동 실행 지시가 없습니다.
- non-force 재설치는 기존 실행 파일·hook을 보존하므로 기존 사용처의 폐기 완료를 보장하지 않습니다.
- force 업데이트도 소유권 정책을 따릅니다. 수정되었거나 프로젝트 소유인 잔여 rule은 임의 삭제하지 않고 설치 출력에 보고합니다.
- 배포 대상에서 제거된 정확한 `.cursor/rules/omc-hub-sync.mdc`에 한해 이전 소유권·hash가 확인된 수정 regular file을 `policy=preserve`, `ownership=preserved`로 기록합니다. 반복 설치도 보존하며 source 일치를 주장하지 않습니다.
- 이 보존 receipt의 strict audit 통과는 bookkeeping 정합성만 의미합니다. 잔여 Hub 지시의 안전성·폐기 완료를 뜻하지 않으며, 일반 managed drift와 symlink의 차단을 완화하지 않습니다.
- 원본 구현 완료와 사용처 전환 완료는 별개입니다. rollout은 별도 승인하며 공유 `.omc/hub.path`와 skill-check 기능은 유지합니다.

- 기본값은 `권장 입력`입니다.
- Codex와 Gemini는 짧은 자연어 입력도 허용되지만, 가능하면 플랫폼별 권장 입력으로 수렴시키는 편이 안정적입니다.
- Claude Code는 현재 문서 기준으로 slash command를 기본값으로 유지합니다.
- 훅이 미동작하거나 컨텍스트가 흔들리면 CLI fallback으로 바로 전환합니다.
- 이 문서는 `스킬별 사고 속도 기준`과 `시작-멈춤-다음 추천`의 SSOT입니다.
- 다른 스킬 문서가 이 기준과 다르면, 먼저 이 문서를 기준으로 맞춥니다.


## 만료 작업의 완료 판정 전용 재승인

이 절차는 이미 받은 사람의 명시적 재승인을 기록하며 승인자의 신원을 인증하지 않는다.
작업 실행·편집·커밋·Claude 호출·비교 재실행을 허용하거나 과거 성공을 소급 생성하지 않는다.
기존 파이프라인 시작 시각·CONTRACT·승인 개정·RED·검증 원문은 바꾸지 않는다.

새 공개 진입점은 `python3 scripts/omc_pipeline_guard.py reapprove-completion --file <작업 내부 요청 JSON>`이다.
요청은 다음 필드만 가진다: `schema_version=omc-completion-reapproval-request/v1`, `session_id`,
`work_id`, `approval_reference`, `pipeline_sha256`, `basis_sha256`, `verification_file`,
`verification_sha256`, `code_snapshot_sha256`. 해시는 현재 원본 바이트로 계산하며 요청자가 발급 시각을 지정할 수 없다.
만료된 원래 작업과 유효한 pending 작업 연결이 있어야 하며, 코드·승인·모든 필수 검사도 현재 조건을 충족해야 한다.

각 필수 검증 항목에는 기존 검증 JSON에 명시된 `evidence_files` 배열이 있어야 한다.
각 원소는 작업 내부 상대 `path`와 실제 원문의 `sha256`만 가진다.
근거 문자열에서 경로를 추정하거나 과거 검증 JSON에 이 필드를 소급 추가해 통과시키지 않는다.
승인 원문·세션 계보·RED와 그 출력·검증 JSON 및 그 명시적 파일/해시 참조를 함께 고정한다.
고정 테스트·시각·홈 영수증은 기존 소비자가 계속 검사하며 실패·누락·미실행·불일치를 재승인으로 덮지 않는다.
원문을 특정할 수 없는 기존 검증은 차단이 정상 결과다. 코드가 달라졌다면 기존 234개 결과 등 과거 증거는 재사용할 수 없다.

발급 기록은 `.omc/completion-reapprovals/<work_id의 SHA-256>.json`에 완전한 파일로 원자적 공개한다.
동시 요청 중 하나만 성공하며 같은 작업은 세션·HEAD·파이프라인 해시가 바뀌어도 두 번째 기록을 발급하지 않는다.
공개 전 실패는 발급으로 계산하지 않는다. 공개한 기록은 덮어쓰기·삭제·자동 연장하지 않는다.
별도 발급 시각부터 `0 <= 경과시간 < 28800초`에만 완료 재판정에 유효하고 정각 8시간부터 차단한다.
미래·비정상 시각과 잘못된 경로·심볼릭 링크를 거부한다. 로컬 파일·시계의 의도적 조작 방지는 보장하지 않는다.

`complete-task`와 수집기는 같은 읽기 전용 판정을 사용한다. 기록의 유효기간과 모든 원문·현재 코드가 일치할 때
TTL 차단만 해제하고 기존 CONTRACT·RED·검증·미스테이징 검사를 그대로 수행한다.
판정은 유효기간 안에 반복 조회할 수 있으나 발급은 한 번이다. 만료 뒤 재발급은 하지 않는다.
구현 테스트는 폐기 가능한 임시 fixture에서만 발급하며 실제 만료 작업 복구 발급은 별도 요청으로 분리한다.

## 별도 판정 기록과 사용자 안내
신규 안내의 mode는 detached_v1, 기존 입력 mode는 legacy_inline이다. 실행 전 capabilities와 작업·세션·stage·report_id·절대마감을 고정한다. 기계 payload/VERDICT는 omc-output/v1 의미를 유지하고 .omc/state/output-records/<work>/<report>/manifest.json에 원문·표시·현재 코드/세션·증거 해시를 결속한다. 일반 모델 선언과 완료 가드 평가는 서로 대체하지 않는다.
`python3 scripts/omc_output_contract.py prepare --target . --stage review --report-id <새ID> --budget-sec <승인된남은초>`의 내부 JSON 메타데이터를 호출자가 보존한다. 원문을 고정한 후 publish에 source-file/source-sha256와 output-context/output-context-sha256를 전달한다. task/transfer는 원래 완료CLI에 output-mode detached_v1 및 같은 context 경로/해시를 전달한다. 최종 사용자 답변은 한글 stdout만이며 메타데이터를 꼬리표로 붙이지 않는다.
발행은 원문·표시 파일을 독점 예약 폴더에 쓰고 readback·참조 검증 후 manifest를 마지막에 원자 발행한다. 중간 파일은 미발행이다. 발행 후 stdout 실패는 전달 미확인이며 기존 평가·manifest를 고치지 않는다. 기록 실패를 다시 기록할 수 없어도 원래 호출의 stderr·종료를 남긴다. 전달오류2는 업무0/1과 다르고 구형 parse로 승격하지 않는다.
collector에는 output-mode, output-context 및 해시, output-record 및 해시를 명시한다. 경로는 .omc/state/output-records의 예정 보고 ID에서만 읽고 native 호출 인자/반환·최종 답변·현재 evaluate 결과를 별도 대조한다. 신형에서 누락/손상/혼합은 차단한다. 구형으로 시작한 원문만 기존 parse/normalize를 적용한다. 해시와 로컬 기록은 진위 인증·동시 공격 방지·실제 모델 화면 숨김 보장이 아니다.
autopilot pipeline은 --output-execution <내부JSON> --output-execution-sha256 <해시>로 명시 사용한다. omc-output-execution/v1은 output_mode=detached_v1과 entries 배열(step_name/context/context_sha256)을 실행 순서대로 고정한다. 계획한 시도 수보다 더 호출하거나 순서가 바뀌면 모델 호출 전 중단한다. 감독은 원래 진단을 별도 보존하고 마지막 검증된 한글 표시만 출력한다. 구형 기본 동작은 바꾸지 않는다.
검증 범위는 공개CLI·합성native event·임시설치이며 실제 모델의 자유응답 준수·실제 홈·업무 효과는 미검증이다.

종료 계층을 구분한다. 일반 모델 선언 publish는 판정이 보류/수정 필요여도 발행이 정상적이면0이며, 자동 실행기의 바깥 provider 정상 반환도0을 유지한다. 내부 완료 가드의 업무 차단1과 혼동하지 않는다. 수집기는 완료 CLI의0/1·실행기의 provider0·전달오류2를 각 호출 계층별로 대조한다.
