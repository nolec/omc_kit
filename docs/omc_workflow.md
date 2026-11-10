# OMC Workflow (역할 분리 + 자동 합성)

이 셋업은 “한 명의 만능 AI” 대신, **역할(비서) 단위로 프롬프트를 분리**해 필요할 때 조합합니다.

빠르게 구조를 파악하거나 다음 프로젝트로 옮길 때는 아래 두 문서를 먼저 보면 됩니다.

- `docs/kit_map.md`
- `docs/next_project_pack.md`

## 1) 역할(비서) 개념

- 역할 프롬프트는 `prompts/ROLE_*.md`에 저장합니다.
- 역할/프로필(조합)은 `prompts/team.json`이 SSOT입니다.

## 0) 중요한 한계(자동 인식)

LLM이 로컬 파일을 “자동으로” 읽는지는 사용하는 도구가 어떤 파일을 컨텍스트로 로드하느냐에 달려 있습니다.

- 기본 언어: 사용자에게 보이는 안내/컨펌/보고는 **한글**을 기본으로 합니다(사용자가 영어를 명시하면 전환).

- Codex CLI: `AGENTS.md`를 지침으로 사용할 수 있습니다.
- Claude Code: 보통 `.claude/CLAUDE.md`를 개인 오버레이로 활용합니다(도구 설정에 따라 다름).
- 기타 LLM/에디터: 프로젝트 지침 파일을 자동 로드하지 않을 수 있습니다.

그래서 이 킷의 `scripts/install.py`는 `AGENTS.md`와 실행기별 개인 오버레이(`.claude/CLAUDE.md`, `.gemini/GEMINI.md`)를 배치해,
“역할 제안 → 컨펌 → 진행” 습관이 기본으로 잡히도록 돕습니다.

OMC 방향으로는 `scripts/omc.py`를 단일 진입점으로 쓰고, `autopilot`, `team`, `ulw`, `ralph`, `deep-interview` 같은 모드 키워드를 자동 해석하는 구성이 권장됩니다.
또한 `.omc/` 아래에 `notepad.md`, `project-memory.json`, `state/sessions/`를 두고, 각 `omc` 실행마다 최근 작업 상태를 기록해 다음 세션에 다시 주입합니다.
이제 `session_start`, `session_end`, `pre_compact`, `post_compact` 훅도 같이 실행되어, compact 전 스냅샷과 compact 후 notepad 재생성이 자동화됩니다.
훅 정의는 `.omc/hooks.json`에서 바꿀 수 있고, 기본값은 builtin 훅이지만 `{"type":"shell","command":"..."}` 형태의 shell hook도 지원합니다.

## 2) 기본 사용(수동 조합)

```bash
python scripts/compose_prompt.py --roles search,analysis,senior_coding
```

## 3) OMC-style 단일 진입점(추천)

```bash
# 단일 작업 (역할 추천 + 컨펌)
python scripts/omc.py "로그인 기능 구현하고 싶어"
python scripts/omc.py team "프론트엔드/백엔드/테스트를 같이 나눠서 해줘"

# Autopilot: 태스크 파일 기반 멀티 LLM 자율 루프
python scripts/omc_autopilot.py new --id feat-x --title "기능 X 구현"
python scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json
python scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json --dry-run

# 상태/훅/compact
python scripts/omc.py setup --target .
python scripts/omc.py verify-install --target .  # 설치 무결성 + 현재 원본 freshness 검증(unknown도 실패)
python scripts/omc.py hook session_start
python scripts/omc.py state status
python scripts/omc.py state compact
```

## 4) 프로필 사용(추천)

```bash
python scripts/compose_prompt.py --profile debugging
python scripts/compose_prompt.py --profile code_review
```

## 5) 자동 추천(규칙 기반)

```bash
roles=$(python scripts/suggest_roles.py --text "버그 원인 분석하고 수정해줘")
python scripts/compose_prompt.py --roles "$roles" --out /tmp/prompt.md
```

## 6) 운영 팁(권장 루프)

1) `search`로 SSOT 확보(파일/문서/근거)
2) `analysis`로 원인 가설/검증 설계
3) `senior_coding`으로 구현/검증
4) `code_review`로 diff 리스크 점검

### Prospective work-class lock

Batch B처럼 구현 작업의 분류를 사전에 고정해야 할 때 외부 signer custody를 한 번 생성합니다.

```bash
python3 scripts/omc_work_class_lock.py init --target .
python3 scripts/omc_work_class_lock.py preflight --target .
```

- 기본 config와 private key는 `~/.config/omc/`에 생성되며 저장소나 setup-force 대상에 포함되지 않습니다.
- Guard는 사용자 config가 활성화되어 있으면 `senior_coding` 세션을 pending으로 기록하고 lock 봉인 성공 뒤에만 confirm합니다.
- `OMC_REQUIRE_WORK_CLASS_LOCK` 환경변수를 명시하면 환경변수 방식이 config보다 우선합니다. `0`은 명시적 비활성화이고, `1`이면 private key 경로와 pinned public key도 함께 필요합니다.
- 기존 lock 없는 receipt는 소급 변환하지 않습니다.

## 7) 삭제/정리 작업 안전수칙

- 기본 정책: 삭제는 `rm`이 아니라 휴지통 이동으로 처리합니다.
- `scripts/omc_guard.py`는 파괴적 삭제 패턴(`rm`, `find -delete`, `git reset --hard`)을 기본 차단합니다.
- 정리 명령은 아래 스크립트를 사용합니다.

```bash
python omc_kit/scripts/safe_trash.py <path1> <path2> ...
```

<!-- OMC:DOCUMENT-ISOLATION:BEGIN -->
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

<!-- OMC:DOCUMENT-ISOLATION:END -->

## 승인된 비구현 캐시 정리의 완료 경로

최초 승인 기준에 `completion_kind=verified_nonimplementation`과 `maintenance` 정책을 고정한 캐시 정리만 `complete-maintenance`를 사용합니다. 종류 생략·구현 작업은 계속 `complete-task`와 실제 RED를 요구합니다. work_class로 면제를 선택하지 않습니다. 이 기능 자체의 수정도 구현 경로를 따릅니다.

정책은 `profile=cache_cleanup`, `.pytest_cache`·`scripts/__pycache__`의 승인된 부분집합 `allowed_paths`, 보존 파일 `protected_files`, 내부 `.omc/`의 `evidence_root`입니다. 같은 작업의 개정·역할 전환으로 종류·정책을 바꾸거나 기준을 갱신하지 않습니다. 최초 `contract-done --required-checks <승인ID>`가 기준을 한 번 고정하며, 성공 안내 뒤 `maintenance_baseline_registered` JSON을 반환합니다. 별도 기준 등록·삭제 실행기는 없습니다.

기존 dirty 파일은 실제 내용·권한·HEAD·원본 인덱스로 보존하고 그 이후 변경은 차단합니다. 삭제는 승인 절대경로를 정렬한 단일 `/bin/rm -rf -- <경로...>` 호출만 지원하며, 직후 `python3 -c <omc_pipeline_guard.MAINTENANCE_POSTCHECK> <동일 경로...>`의 lstat 부재 확인이 필요합니다. 호출 전에 임의 Python 본문·wrapper·명령 연결로 바꾸지 않습니다.

실제 Bash JSONL의 CONTRACT 반환→삭제 호출/반환→직후 확인 호출/반환 prefix를 완료 호출 전에 내부 일반 파일로 공급합니다. `omc-maintenance-operation/v1`은 작업·세션·승인 해시, 기준·prefix 참조와 해시, 삭제·확인 call ID를 결속합니다. 검증 JSON의 `maintenance_operation_ref: {path, sha256}`와 각 필수 검사 `evidence_files: [{path, sha256}]`를 연결한 뒤 `complete-maintenance --verification-file <내부 JSON> --output-mode detached_v1 --output-context <문맥> --output-context-sha256 <해시>`로 판정합니다. 일반 publish의 모델 선언은 완료 근거가 아닙니다.

직후 부재 확인이 성공해야 제거 또는 NO_OP로 인정합니다. 삭제 실패·부분 실패, 확인 누락·권한 오류, 지원하지 않는 provider·메타데이터는 차단합니다. 이후 캐시 재생성은 기존 제거량에 더하지 않습니다. 수집기는 같은 판정과 실제 전체 로그의 prefix를 대조합니다. 로컬 기록 대조는 악의적 로컬 작성자나 커널 수준 실행 진위를 인증하지 않습니다. 캐시 정리 완료를 기존 dirty 구현·리뷰·출하 승인으로 전용하거나 과거 BLOCK에 소급 적용하지 않습니다.

### 캐시 정리 등록의 제한된 복구와 ready 전달

새 등록은 원본 state 해시·시작 시점을 고정한 baseline/v2를 배타적으로 게시합니다. 전체 UTF-8 직렬화 크기는 읽기와 같은 8MiB 이하여야 합니다. 기존 v1은 완료 검증을 유지하며 복구 가능한 v2로 변환하지 않습니다. 등록과 state 저장에는 세션 공유 잠금→작업 잠금 순서를 사용하고, 원래 TTL과 소유권을 다시 확인합니다.

실행기는 launch별 ignored 내부 control 디렉터리와 `OMC_MAINTENANCE_READY_FILE`을 준비합니다. 최초 CONTRACT가 기준 게시 후 state 저장에 실패하고 정상 이전 state가 남아 있으면 비성공 반환에 ready 경로를 제공합니다. 수집기는 실패 반환까지의 원본 prefix를 먼저 불변 게시한 뒤 `omc-maintenance-ready/v1`을 원자적으로 게시합니다. 이 기능의 통제된 어댑터 검증은 실제 provider 지원 증거가 아닙니다.

에이전트는 `maintenance-recovery-info --ready-file <실패 반환의 경로>`를 한 번 호출합니다. 최대 10초와 원래 TTL 남은 시간 중 짧은 시간만 기다리며, 성공 시 검증된 prefix 참조를 반환합니다. 그 결과로 `contract-done --required-checks <승인ID> --maintenance-recovery-file <상대경로> --maintenance-recovery-sha256 <SHA256>`을 한 번 호출합니다. 작업·launch·승인·기준·원본 state가 불일치하거나 반환 유실·이미 저장된 참조·삭제 이후·준비 시간 초과이면 자동 복구하지 않습니다. 기존 증거 삭제·덮어쓰기나 새 TTL로 재시작하는 절차는 없습니다.

복구 완료는 operation/v2, state, 성공 반환과 같은 recovery descriptor를 사용합니다. 실패 prefix를 필수 preservation 검증의 `evidence_files`에도 연결하며, CLI와 수집기는 공통 판정과 실제 전체 로그의 prefix를 대조합니다. ready는 전달용 metadata이며 승인 증거가 아닙니다. 코드 보완 A의 통제된 회귀와 실제 실행기 운용 검증 B는 별도입니다. 현재 실제 provider의 종료 정보와 ready 왕복은 미확인이므로 운용 검증 B는 보류 상태입니다.

## 릴리스 기준의 전용 로컬 참조

릴리스 기준 커밋을 원격에 없는 `origin/...` 참조로 임시 보관하면 일반적인
`fetch --prune`이 그 참조를 삭제할 수 있습니다. 원격 추적 참조를 수동 복원해
재시도하는 대신 source 준비 단계에서 다음 전용 이름을 사용합니다.

- 형태: `refs/omc/release-bases/<고정 base의 40자리 소문자 SHA-1>`.
- 이름의 OID, preflight의 `expected_base_oid`, 실제 commit OID는 같아야 합니다.
  기존 `origin/name`과 `refs/remotes/origin/name` 별칭은 계속 지원합니다.
- 이 이름은 불변 저장소나 승인 인증 장치가 아닙니다. 등록된 모든 remote의
  양의 fetch 목적지가 해당 정확한 참조와 일치하면 준비할 수 없습니다.
  `+refs/*:refs/*` 같은 broad/mirror 설정과 해석할 수 없는 규칙은
  `diagnose` 단계에서 설명하며, 설정을 자동 변경하지 않습니다.
- 음의 refspec만으로 broad mapping을 안전하다고 취급하지 않습니다.
  source-only fetch는 별도로 구분합니다. 외부의 임의 command-line refspec이나
  직접 `update-ref`를 막지는 않으며, 이후 OID/설정 변화는 재검증에서 차단합니다.

### source 준비의 생성·확인 절차

이 절차는 구현·문서가 포함된 최종 커밋과 source 준비 범위가 승인된 뒤
준비 담당이 수행합니다. ship의 `prepare/check` 안에서 원본을 수리하지 않습니다.
명령은 셸 문자열 대신 인자 배열로 전달하며 `<...>`는 먼저 고정한 값입니다.

1. base를 commit으로 해석하고 fetch 지원 조건을 읽기 전용으로 확인합니다.
2. `git show-ref --verify --quiet <full-ref>`로 정확한 이름의 존재를 확인합니다.
   종료0은 존재,1은 부재이며 다른 종료는 오류입니다. 존재하면
   `git show-ref --verify --hash <full-ref>`와
   `git rev-parse --verify <full-ref>^{commit}`의 결과를 고정 OID와 비교합니다.
3. 없을 때만 `[git,update-ref,<full-ref>,<base-OID>,<40자리0>]`으로
   compare-and-create합니다. 성공 후 정확 조회를 반복합니다. 경쟁 생성 실패 시
   현재 값을 다시 읽어 동일 OID이면 변경 없이 확인하고, 다른 값/형태면 중단합니다.
4. 기존 동일 OID는 재작성하지 않습니다. 다른 OID·비commit·prefix 충돌·조회 오류를
   해결하려고 기존 참조를 삭제·이동하거나 덮어쓰지 않습니다.
5. 생성/동일값 확인 결과와 실제 call/return, full ref/base OID, 관찰 fetch 설정을
   기존 작업 근거에 보관합니다. clone은 전용 참조의 자동 전달을 가정하지 않으며
   자기 대상 안에서 정확 조회 또는 같은 CAS 생성·검증을 수행합니다.

### 검증과 출하 인계

통제 fixture의 검증은 `prepare → capture/seal/record → quality approve → check →
quality run`까지 수행하며, 양성 gate1회·음성 gate0회와 실제 종료·정리를 확인합니다.
이 결과는 원본의 전체 출하 검사나 사용처 설치 완료가 아닙니다.

인계 자료는 한 묶음으로 준비합니다: 최종 head/base/full ref·정확한 전체 변경 경로,
생성 또는 동일값 확인 원문, 유효 fetch 설정, 리뷰 결속, 관련 검증·보존 결과,
기존 설정 raw/정규화 해시와 `base_ref`만 다른 proposal, 남은 실행 승인·횟수·마감.
최종 커밋이나 source 준비가 아직 없으면 그 항목을 미완료로 표시합니다.
`docs/quality_gate_contract.md`와 `.github/workflows/omc-ci.yml` evidence와 품질 명령·범위·
마감은 이 절차 때문에 변경하지 않습니다. evidence가 달라졌다면 단순 base_ref 변경으로
보고하지 말고 실제 proposal 차이에 포함합니다.

실제 source 설정 적용은 별도 승인된 `proposal-validate → proposal-apply → approve`
절차를 따릅니다. 원본의 `validate-ship=READY`, quality `status=ready`, 고정 후보의
`diagnose`와 실행 승인이 확인되기 전 ship T0를 시작하지 않습니다. 과거 시도·실패·
시계·승인을 새 시도 기록으로 덮어쓰지 않습니다.


## 빠른 계약 검사와 진행 표시 · 2026-11-10

```bash
# 검사 실행 없이 현행 marker의 선택·fast/rest 분할만 기록
python3 scripts/omc_test_suite.py inventory --target .
# 명시적으로 승인된 검증에 사용: fast 실패 시 rest 실행0회
python3 scripts/omc_test_suite.py run --target .
```

runner는 현재 `not slow and not historical_evidence and not red_scaffold` 선택 전체를 유지한다. 기본 fast는 review runner·review/task/plan 스킬 계약4개 모듈이고 나머지는 직렬이다. 수집+fast 기본60초, 전체 기본3600초의 남은 시간을 사용하며 skip·xfail·수집 오류·누락은 차단한다. record-dir 생략 시 매번 `.omc/test-suite/<고유ID>`를 생성하고 원문·JUnit·선택 목록을 남긴다. 이 명령 예시는 전체 검사 실행 권한을 부여하지 않는다.

quality CLI의 stdout은 최종 JSON 하나이며, stderr의 `OMC_QUALITY_PROGRESS`가 단계 시작·5초 간격 경과·종료를 알린다. 진행은 통과 판정이 아니고 임의 자식 출력·환경값을 노출하지 않는다. 공개 CLI 취소 시 소유 그룹을 종료하며 생성/등록 경계와 자식의 정상 TERM 정리를 검증했다.

pilot은 최대2개 작업 프로세스의 별도 실험이다. 준비(snapshot·수집·양쪽 clone)180초, 직렬/병렬각300초, 마지막 정리60초로 최대840초이며 마감 후 비교 호출은 차단한다. 공유 브라우저·빌더 의존성의 격리가 증명되지 않은 실제4개 테스트는 실행0회·성능 미검증이다. 전체 suite 병렬 적용·통과 기록 재사용은 미구현이다.

현재 `.omc/quality-gates.json`과 `docs/quality_gate_contract.md`의 출하 명령은 기존 전체 pytest다. fast-first 정책 제안은 `.omc/tasks/quality-speed-v2-20261110/quality-proposal.json`과 제안 문서에 보존했고 적용하지 않았다. 정책·evidence 변경은 새 후보와 proposal/CAS/정식 승인으로 연결한다. 현재 코드 리뷰 승인이나 로컬 커밋을 전체 출하 검사·릴리스·재설치 통과로 해석하지 않는다.
