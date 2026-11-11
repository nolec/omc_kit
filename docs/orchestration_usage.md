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


## 단일 승인 V0: 격리 후보까지만

`omc-autopilot-mission/v2`는 Codex·Python/pytest·기존 파일 수정 전용 opt-in이다. v1 mission과 기존 pipeline의 동작은 유지한다. 독립 Python 검수기를 준비할 수 없는 작업, 새 파일·삭제·API 변경·여러 실행기·중단 후 자동 재개는 지원하지 않는다.

준비 담당은 `omc_mission.freeze_mission_packet`으로 요청 해시·base·목표·DoD·비목표와 `execution`을 고정하고 사용자의 실제 승인 이후에만 `build_mission_approval_receipt`를 호출한다. 이 로컬 receipt와 해시는 인간 승인 진위를 인증하는 수단이 아니다. 승인의 session ID와 새 격리 실행 session ID는 다르며 부모가 둘을 결속한다. 사용자가 역할·스킬 이름·내부 JSON을 직접 조립하지 않도록 준비 담당이 처리한다.

`execution` 필드: `allowed_paths`(정확한 기존 .py 파일), `test_paths`(그 일부인 기존 test_*.py), `verification_commands`(argv 배열 목록), `red_case_id`(pytest 실패 ID), `max_time_sec`(1~7200), `max_revisions`(0~2), `approval_expires_epoch`(절대 만료), `verifier_path`(작업 밖의 독립 Python 검수 원본), `verifier_sha256`. 선택 필드 `codex_model`은 승인 시 고정한 명시 모델이며 전역 모델 프로필을 변경하지 않고 task/review에 동일하게 전달한다. `validation.max_total_rounds`는 최초 구현+수정 횟수와 같아야 한다. 수정 횟수는 이슈별로 갱신하지 않으며 독립 검사 실패도 같은 총한도를 소비한다.

기존 `freeze-work-contract`에 같은 mission·승인 receipt·범위·검증 명령을 전달하면 `isolated_candidate_only` 정책을 고정한다. 실행은 `pipeline --work-contract <고정계약> --instruction <동일요청> --branch <내부후보명> --executor codex --max-time <승인한도>`로 진입한다. CLI의 시간이 더 커도 승인 시간을 늘리지 않는다. source는 clean base여야 하며 정책이 다르면 실행 전 차단한다. 생성된 run 디렉터리는 한 번만 사용하며 오류를 이유로 기존 run 기록을 지우거나 덮어쓰지 않는다.

부모가 baseline 검사를 확인한 뒤 테스트만 수정하는 RED 호출→실제 실패 등록→구현→검증→독립 검사→리뷰를 연결한다. 기존 테스트 함수는 삭제·약화하지 못하며, RED의 과거 원문은 수정 후에도 보존한다. 검증 실패·명시적 REVISE만 수정으로 돌아간다. BLOCK·모호한 판정·승인/기록 불일치·범위 위반·만료·인증 실패는 중단한다. 모델·provider·로그인을 자동 변경하지 않는다.

구현 작업 폴더는 하나이며 리뷰/독립 검수는 별도 복제본에서 수행한다. 단일 승인 작업의 Codex 명령은 `/tmp` 및 TMPDIR의 기본 쓰기 허용을 제외한다. 독립 검수 원본은 구현·검수 폴더 밖 읽기 전용 파일로 보존하며, 검사 프로세스가 끝난 후 부모가 결과와 전후 해시를 확인한다. OS sandbox가 없으면 검사를 생략하지 않고 차단한다. 신뢰된 부모 실행기와 같은 로컬 사용자의 악의적 직접 변조까지 인증하는 격리는 아니다.

완료 가드 통과 이후 후보 커밋 생성·내용 일치·불변 전체 결과 저장까지 통과해야 `candidate_ready`다. 후보는 격리 폴더에만 남기며 원본 브랜치·작업 파일·원본 index를 변경하지 않는다. 완료 가드 성공 뒤 후보 생성 실패는 별도 blocked 결과이며 과거 성공 원문을 고치지 않는다. 결과 저장/표시 실패는 성공 전달로 계산하지 않으며 상태 재조회는 기록을 읽고 작업을 재실행하지 않는다.

자동 테스트 통과는 실제 모델 준수나 사용자 개입 감소의 증거가 아니다. 첫 실제 Codex 실행은 기존 모델 라우팅의 gpt-5.4-mini가 ChatGPT 계정에서 지원되지 않아 RED 모델 호출이 실패했다. 사용자 승인 후 전역 설정을 보존하는 명시 모델 실행 경로를 추가했다. 실패 원문은 보존했다. 이후 gpt-6.1-sol을 명시한 실제 정상 경로와 통제된 검증 실패/리뷰 REVISE 경로가 격리 후보 생성까지 통과했다. 통제된 오류 주입 시험이며 자연 작업 생산성·사용자 개입 감소의 증거는 아니다. 전역 모델 프로필은 변경하지 않았다.


### 단일 승인 V0 리뷰 수정 경계

- pytest 종료 코드만으로 통과를 기록하지 않는다. 부모가 보존한 읽기 전용 pytest 보고 플러그인으로 node ID·setup/call/teardown·결과를 수집하고, baseline에서 실제 통과한 케이스 및 RED 케이스가 수정 후에도 실행·통과했는지 대조한다. 새 skip·누락은 수정 예산 안에서 재검증하며 보고 누락·수집 오류는 차단한다. 같은 pytest 프로세스 내부의 로컬 이벤트 기록이며 악의적인 테스트 코드에 대한 실행 진위 인증은 아니다.
- RED 검사 명령 직전과 직후 작업 파일 snapshot을 비교한다. 구현·테스트 등 작업 파일이 검사 실행 중 바뀌면 RED 근거를 등록하지 않는다. 승인·Git 상태 검사는 별도로 유지한다.
- 승인 소비는 run ID나 임시 run 폴더가 아니라 Git 공용 metadata의 `omc-single-approval-claims`에 승인 session ID·decision ID를 결속해 한 번 기록한다. run ID·run 폴더·worktree를 바꿔도 같은 저장소의 소비된 승인을 재사용하지 못한다. 실패해도 소비 기록과 원문을 보존하며 새 실행은 명시적 새 승인이 필요하다. 원본 작업 파일·브랜치·인덱스는 변경하지 않고 이 승인 metadata만 추가한다. 독립적인 저장소 복제본 사이의 전역 승인 인증을 제공하는 원장은 아니다.
- 테스트 함수 AST 보존 시 파일·클래스 경로·함수명을 함께 식별한다. 서로 다른 클래스에서 같은 메서드 이름을 쓰는 정상 pytest 케이스를 중복으로 오인하지 않는다. 최종 실행 여부는 AST가 아니라 실제 pytest node ID 결과로 확인한다.

## 완료 기록 수집 상태와 로컬 커밋

커밋 전에는 pre-commit 승인·TDD와 post-commit 완료 기록 소비 조건을 함께 확인한다. 현재 완료 연결은 단일 커밋 기준이다. 기능별 다중 커밋의 전체 작업 연결은 아직 지원하지 않으며 첫 커밋의 완료 기록을 전체 묶음 완료로 보고하지 않는다.

`python3 scripts/omc.py state complete --target .`는 대기 기록이 없으면 `status=skipped`, `reason_code=no_pending_completion`, 종료0으로 정상 생략한다. 정상 수집도 종료0이다. 손상 JSON·잘못된 필드·세션/커밋 불일치·읽기 실패는 `status=error`와 구체적 `reason_code`, 종료1을 반환한다. 모호한 링크 입력은 정상 없음으로 취급하지 않는다. 이 엄격한 읽기는 완료 수집에 한정하며 공용 상태 읽기 계약은 유지한다.

post-commit의 오류는 이미 생성된 Git 커밋을 취소하지 않는다. `Git 커밋 성공, OMC 완료 기록 실패` 안내와 원문을 보존하고 Git 성공·기록 상태·테스트 결과를 각각 보고한다. 커밋을 다시 실행하거나 과거 영수증·대기 기록을 임의 복원하지 않는다. 기존 사용자 post-commit의 종료 상태는 유지한다. 기록 복구와 다중 커밋 등록/연결은 후속 설계이며 자동 재개하지 않는다.

검증은 격리 저장소의 공개 CLI와 native/Husky 실제 post-commit을 대상으로 한다. 권한 오류는 읽기 경계에 PermissionError를 주입한 단위 검증이며 실제 계정 권한 제한이나 자연 작업 준수의 증거는 아니다. 사용처 설치·배포는 별도 요청으로 수행한다.


## plan·critique-plan 발행 실패의 제한된 설명
이 절은 plan/critique-plan의 model_declared 실패 안내에 한해 공통 실패 출력 규칙보다 우선한다. 분석·계획 원문·요약을 먼저 저장하고 발행 직전에 prepare한다. 요약을 prepare --reference에 포함하고 publish의 --draft-summary-file·--draft-summary-sha256으로 명시한다. 같은 현재 세션·작업·후보·참조가 검증된 경우에만 stderr로 공식 판정 전달 미확인과 작성 초안을 구분해 표시하며 종료2를 유지한다. 이 실패 안내의 stderr 원문을 사용자에게 전달하되 승인·완료·자동 후속으로 해석하지 않는다. 요약은 최대12줄·1,200자이며 빈 값/초과는 절단하지 않고 원문 위치만 안내한다. 요약 크기가 정상 판정 발행을 막지 않는다. 다른 세션·변조·링크·후보 변경이면 기존 오류 안내만 제공한다. task/review/ship·critique-code에는 적용하지 않는다. 재발행·과거 조회·기한 연장은 지원하지 않는다. 이 지침 테스트가 실제 모델 준수를 보장하지 않는다.

진단 저장 여부와 표시 시도는 판정 발행 성공과 별개다. 기존 manifest와 실패 원문을 보존하며, 표시 실패 후 기록을 수정하거나 재발행하지 않는다.


### 만료된 완료 작업의 명시적 정리

`python3 scripts/omc_edit_resume.py --target <저장소> retire-expired-work --work-id <작업ID> --approval-reference <사용자승인근거>`는 8시간이 지난 작업의 활성 포인터만 보관 영역으로 옮깁니다.
pending 완료 기록이 남은 작업은 `EXPIRED_UNFINISHED`, 이미 소비된 유효한 완료 영수증이 있는 작업은 `EXPIRED_COMMITTED`로 기록합니다. 후자는 소유 세션과 완료 영수증의 원본을 유지하고 보관 영역에 바이트·권한 스냅샷을 추가합니다. 새 작업이나 승인을 생성하지 않습니다.

일반 완료 인식은 기록된 커밋이 현재 HEAD의 조상이어야 합니다. 명시적으로 승인한 정리에서만 이 조상 조건을 제외하며, 세션·작업·Git 커밋·변경 경로 검증은 유지합니다. `session_start`가 승인 필드를 초기화한 정확한 형태도 기존 완료의 소유권과 구분해 처리합니다.
중단된 정리는 같은 작업ID와 승인 근거로 재개합니다. 새 pending 기록, 변경된 증거 또는 알 수 없는 보관 상태가 발견되면 중단하며 원장을 수리하거나 승인을 복사하지 않습니다.

소비자에 관리 파일 로컬 수정이 있으면 자동 업데이트는 충돌로 중단됩니다. 통합 후에도 충돌이 자동으로 사라지지는 않습니다. 전체 충돌 목록과 원본 SHA·권한을 확인하고 별도 백업한 뒤, 해당 파일 덮어쓰기 승인을 받은 유지보수 구간에서만 기존 `--force` 설치를 사용합니다. 추가 충돌이나 동시 작업이 있으면 덮어쓰지 않습니다. 과거 정리 보관 영역은 설치 전후 바이트·권한 보존 및 JSON 읽기를 별도로 확인합니다. 격리 테스트 통과는 운영 소비자 설치나 릴리스 완료를 뜻하지 않습니다.
