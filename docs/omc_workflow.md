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
