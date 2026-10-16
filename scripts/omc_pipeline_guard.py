#!/usr/bin/env python3
"""
omc_pipeline_guard.py — TDD 파이프라인 세션 상태 추적기

AI가 구현 파일을 편집하기 전에 RED 단계(실패 테스트 작성)가
완료됐는지 확인하고, 미완료 시 차단합니다.

Cursor beforeToolCall 훅에서 호출됩니다:
  python3 scripts/omc_pipeline_guard.py check <file_path>

사용:
  python3 scripts/omc_pipeline_guard.py check <impl_file>      # 편집 허용 여부 체크
  python3 scripts/omc_pipeline_guard.py red-done <test_file>   # RED 완료 등록
  python3 scripts/omc_pipeline_guard.py status                 # 현재 세션 상태 출력
  python3 scripts/omc_pipeline_guard.py reset                  # 세션 상태 초기화
  python3 scripts/omc_pipeline_guard.py allow <impl_file>      # 예외 허용 (사용자 명시)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

_IMPL_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py"}

# 테스트 없어도 통과하는 파일 패턴
_BYPASS_PATTERNS = [
    r"\.spec\.",
    r"\.test\.",
    r"_test\.",
    r"test_",
    r"\.d\.ts$",
    r"\.config\.(ts|js)$",
    r"/types\.ts$",
    r"/types/",
    r"\.styled\.(ts|tsx)$",
    r"/constants/",
    r"\.constants\.(ts|tsx)$",
    r"/index\.(ts|tsx|js)$",
    r"__test__",
    r"__mocks__",
    r"/node_modules/",
    r"\.stories\.(ts|tsx)$",
    r"/queryKey\.(ts|js)$",
    r"\.min\.",
]

# 테스트 파일 판단 패턴
_TEST_PATTERNS = [
    r"\.spec\.",
    r"\.test\.",
    r"_test\.",
    r"test_",
    r"__test__",
    r"__tests__",
]

_SESSION_TTL_SECONDS = 8 * 60 * 60  # 8시간 이상 지나면 세션 만료

# ---------------------------------------------------------------------------
# 세션 상태 파일
# ---------------------------------------------------------------------------

def _state_path(root: Path) -> Path:
    return root / ".omc" / "pipeline_session.json"

def _load_state(root: Path) -> dict:
    p = _state_path(root)
    if not p.exists():
        return _empty_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # TTL 체크
        if time.time() - data.get("started_at", 0) > _SESSION_TTL_SECONDS:
            return _empty_state()
        return data
    except Exception:
        return _empty_state()

def _save_state(root: Path, state: dict) -> None:
    p = _state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _empty_state() -> dict:
    return {
        "started_at": time.time(),
        "red_done_tests": [],        # RED 완료된 테스트 파일 목록
        "allowed_impl_files": [],    # 사용자가 명시 예외 허용한 구현 파일
        "blocked_count": 0,
        "contract_confirmed": False, # CONTRACT 양식 작성 및 사용자 컨펌 완료 여부
        "contract_hash": "",         # CONTRACT 내용 SHA-256 해시 (우발적 위조 방지)
        "session_id": "",            # contract_confirmed가 속한 latest 세션 ID
    }


def _make_contract_hash(content: str) -> str:
    """CONTRACT 내용 문자열의 SHA-256 해시를 반환한다."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _is_impl_file(path_str: str) -> bool:
    p = Path(path_str)
    if p.suffix not in _IMPL_EXTENSIONS:
        return False
    normalized = path_str.replace("\\", "/")
    return not any(re.search(pat, normalized) for pat in _BYPASS_PATTERNS)

def _is_test_file(path_str: str) -> bool:
    normalized = path_str.replace("\\", "/")
    return any(re.search(pat, normalized) for pat in _TEST_PATTERNS)

def _infer_impl_stem(test_file: str) -> str:
    """테스트 파일 경로에서 구현 파일의 stem을 추론한다."""
    p = Path(test_file)
    stem = p.stem
    # .spec, .test 제거
    for suffix in (".spec", ".test"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    # test_ 접두사 제거
    if stem.startswith("test_"):
        stem = stem[5:]
    # _test 접미사 제거
    if stem.endswith("_test"):
        stem = stem[:-5]
    return stem

def _has_red_for_impl(impl_file: str, red_tests: list[str]) -> bool:
    """구현 파일에 대응하는 RED 테스트가 등록됐는지 확인한다."""
    impl_stem = Path(impl_file).stem.lower()
    for test in red_tests:
        test_stem = _infer_impl_stem(test).lower()
        if impl_stem == test_stem or impl_stem in test_stem or test_stem in impl_stem:
            return True
    return False

def _normalize_path(root: Path, path_str: str) -> str:
    """경로를 프로젝트 루트 기준 절대경로 문자열로 정규화한다.
    상대경로/절대경로 혼용으로 인한 비교 불일치를 방지한다.
    """
    p = Path(path_str)
    if not p.is_absolute():
        p = root / p
    return str(p.resolve())

_GREEN_FIRST_LINE_THRESHOLD = 50

def _warn_if_large_without_red(abs_path: Path, file_path: str, state: dict) -> None:
    """구현 파일이 크고 RED가 등록되지 않았으면 경고를 출력한다.

    차단이 아닌 경고만 수행한다. TDD 없이 대량 구현이 진행되고 있을 가능성을
    알려 RED → GREEN 순서를 상기시킨다.
    """
    try:
        line_count = len(abs_path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return

    if line_count < _GREEN_FIRST_LINE_THRESHOLD:
        return

    if _has_red_for_impl(file_path, state.get("red_done_tests", [])):
        return

    print(f"[PIPELINE] ⚠️  GREEN 먼저? {file_path} ({line_count}줄)")
    print(f"           RED 등록 없이 {line_count}줄 구현 파일이 수정되고 있습니다.")
    print(f"           테스트 먼저 작성 후 RED 등록:")
    print(f"             python3 scripts/omc_pipeline_guard.py red-done <테스트파일>")

def cmd_check(root: Path, file_path: str, bypass: bool = False) -> int:
    """구현 파일 편집 전 RED 완료 여부 체크. 0=허용, 1=차단

    bypass=True(--autopilot) 이면 신규 파일 생성 차단을 건너뛴다.
    """
    if bypass:
        return 0  # autopilot 모드 → 신규 파일 차단 없음

    if not _is_impl_file(file_path):
        return 0  # 구현 파일 아님 → 통과

    state = _load_state(root)

    # 명시 예외 파일 — 상대/절대 경로 혼용을 방지하기 위해 정규화 후 비교
    norm_file = _normalize_path(root, file_path)
    norm_allowed = [_normalize_path(root, f) for f in state["allowed_impl_files"]]
    if norm_file in norm_allowed:
        print(f"[PIPELINE] ⚠️  허용 예외: {file_path}")
        return 0

    # 기존 파일인지 확인 (신규 파일만 강제)
    abs_path = root / file_path
    if abs_path.exists():
        # 기존 파일 수정 — 줄 수 기반 "GREEN 먼저?" 경고
        _warn_if_large_without_red(abs_path, file_path, state)
        return 0

    # 신규 파일 — RED 체크
    if _has_red_for_impl(file_path, state["red_done_tests"]):
        print(f"[PIPELINE] ✅ RED 확인됨: {file_path}")
        return 0

    # 차단
    state["blocked_count"] = state.get("blocked_count", 0) + 1
    _save_state(root, state)

    impl_stem = Path(file_path).stem
    test_candidate = f"{Path(file_path).parent}/{impl_stem}.spec.ts"
    print()
    print("===========================================================")
    print(" PIPELINE GATE: RED 단계 미완료")
    print(f" 신규 파일 생성 차단: {file_path}")
    print()
    print(" ─── 복구 절차 ─────────────────────────────────────────")
    print(f" 1. 테스트 파일 생성:")
    print(f"      {test_candidate}")
    print(f" 2. 실패하는 테스트 케이스 작성")
    print(f" 3. 테스트 실행 (실제 FAIL 출력 확인):")
    print(f"      npx jest {test_candidate} --verbose 2>&1 | head -40")
    print(f" 4. RED 완료 등록:")
    print(f"      python3 scripts/omc_pipeline_guard.py red-done {test_candidate}")
    print(f" 5. 이후 구현 파일 생성 재시도")
    print()
    print(" ─── 예외 허용 (사용자 명시 필요) ──────────────────────")
    print(f"      python3 scripts/omc_pipeline_guard.py allow {file_path}")
    print()
    print(" 현재 세션 상태 확인:")
    print("      python3 scripts/omc_pipeline_guard.py status")
    print("===========================================================")
    return 1

def _red_evidence(root: Path, test_file: str, evidence_file: Path, state: dict,
                  historical: bool = False) -> dict:
    """Validate caller-recorded case identity, not semantic relevance or execution truth."""
    evidence_file = evidence_file.resolve()
    evidence_file.relative_to(root.resolve())
    raw = evidence_file.read_bytes()
    doc = json.loads(raw)
    basis = _load_approved_basis(root, state.get("session_id", ""))
    if (doc.get("session_id") != state.get("session_id") or doc.get("basis_sha256") != _basis_digest(basis)
            or state.get("basis_sha256") != _basis_digest(basis)):
        raise ValueError("RED 근거의 세션·승인 기준 불일치")
    path = (root / test_file).resolve()
    path.relative_to(root.resolve())
    if not _is_test_file(test_file) or doc.get("test_file") != test_file:
        raise ValueError("RED 테스트 파일 불일치")
    if not historical and doc.get("test_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError("RED 실행 당시 테스트 내용 불일치")
    if doc.get("check_id") not in {c["id"] for c in basis["checks"]}:
        raise ValueError("RED 근거에 승인된 검증 ID가 필요합니다")
    for key in ("expected_failure", "command", "case_id"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise ValueError("RED 요구·실패 케이스·명령 기록이 필요합니다")
    for key in ("baseline_failed_cases", "failed_cases"):
        if not isinstance(doc.get(key), list) or any(not isinstance(x, str) or not x.strip() for x in doc[key]):
            raise ValueError("RED의 기존·현재 실패 케이스 목록이 필요합니다")
    if doc["case_id"] not in set(doc["failed_cases"]) - set(doc["baseline_failed_cases"]):
        raise ValueError("기존 실패만으로 이번 요구의 RED를 등록할 수 없습니다")
    if type(doc.get("exit_code")) is not int or doc["exit_code"] != 1:
        raise ValueError("RED는 테스트 실패 종료 코드 1이 필요합니다; 실행 불가는 실패 근거가 아닙니다")
    output = (root / doc["output_file"]).resolve()
    output.relative_to(root.resolve())
    data = output.read_bytes()
    if not data.strip() or hashlib.sha256(data).hexdigest() != doc.get("output_sha256"):
        raise ValueError("RED 실제 출력 기록 불일치")
    return {"file": str(evidence_file.relative_to(root.resolve())), "sha256": hashlib.sha256(raw).hexdigest()}


def _red_evidence_problems(root: Path, state: dict) -> list:
    if not state.get("red_evidence_required"):
        return []  # Historical sessions keep their original registration semantics.
    receipts = state.get("red_evidence", {})
    if not isinstance(receipts, dict) or not receipts:
        return ["현재 요구에 연결된 RED 근거 등록이 없습니다"]
    try:
        # Every registered test must have an intact receipt. Refactored test bytes
        # may change later; this preserves the historical RED, not a current FAIL.
        for name in state.get("red_done_tests", []):
            receipt = receipts[name]
            current = _red_evidence(root, name, root / receipt["file"], state, historical=True)
            if current != receipt:
                raise ValueError("RED 등록 근거 불일치")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return ["RED 등록 근거 또는 승인 개정 불일치; 요구별 실패 근거를 다시 확인하세요"]
    return []


def cmd_red_done(root: Path, test_file: str, evidence_file: Path | None = None) -> int:
    """RED 단계 완료 등록."""
    if not _is_test_file(test_file):
        # 테스트 파일이 아니어도 수동 등록 허용 (사용자가 직접 호출)
        print(f"[PIPELINE] ⚠️  테스트 파일 패턴 아님: {test_file} — 그래도 등록합니다.")

    state = _load_state(root)
    if state.get("basis_sha256") or state.get("red_evidence_required"):
        if evidence_file is None:
            print("[PIPELINE] 승인된 작업의 RED는 --evidence-file <근거 JSON>이 필요합니다.")
            return 1
        try:
            if not state.get("contract_confirmed"):
                raise ValueError("현재 CONTRACT가 필요합니다")
            receipt = _red_evidence(root, test_file, evidence_file, state)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            print(f"[PIPELINE] RED 근거 거부: {exc}")
            return 1
        if state.get("red_session_id") != state.get("session_id"):
            state["red_done_tests"] = []
            state["red_evidence"] = {}
        state.setdefault("red_evidence", {})[test_file] = receipt
        state["red_evidence_required"] = True
        # Legacy file-name lists are not evidence for this requirement. Only
        # tests with a typed receipt may become current RED registrations.
        state["red_done_tests"] = list(state["red_evidence"])
    state["red_session_id"] = state.get("session_id", "")
    _save_state(root, state)
    if evidence_file is not None and state.get("red_evidence_required"):
        print(f"[PIPELINE] ✅ RED 완료 등록: {test_file} — 요구별 근거 확인·갱신")
        return 0
    if test_file not in state["red_done_tests"]:
        state["red_done_tests"].append(test_file)
        _save_state(root, state)
        print(f"[PIPELINE] ✅ RED 완료 등록: {test_file}")
        print(f"           이제 대응 구현 파일을 생성할 수 있습니다.")
    else:
        print(f"[PIPELINE] 이미 등록됨: {test_file}")
    return 0

def _allow_log_path(root: Path) -> Path:
    return root / ".omc" / "allow_log.jsonl"

def _append_allow_log(root: Path, impl_file: str, reason: str) -> None:
    """allow 사용 내역을 JSONL 로그에 추가합니다."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "file": impl_file,
        "reason": reason,
    }
    p = _allow_log_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def cmd_allow(root: Path, impl_file: str, reason: str = "") -> int:
    """사용자 명시 예외 — 해당 파일은 RED 없이 편집 허용.
    이유(reason)는 필수입니다. 빈 reason이면 exit 1로 차단합니다.
    """
    if not reason or not reason.strip():
        print("[PIPELINE] ❌ --reason 없이 allow 사용 차단")
        print(f"  python3 scripts/omc_pipeline_guard.py allow {impl_file} --reason \"이유 입력\"")
        print("  예외 허용에는 반드시 이유가 필요합니다. (감사 로그 기록용)")
        return 1

    # 상대/절대경로 혼용으로 인한 중복 등록 및 비교 불일치를 방지하기 위해 정규화
    norm_file = _normalize_path(root, impl_file)

    state = _load_state(root)
    norm_allowed = [_normalize_path(root, f) for f in state["allowed_impl_files"]]
    if norm_file not in norm_allowed:
        state["allowed_impl_files"].append(norm_file)
        _save_state(root, state)

    _append_allow_log(root, norm_file, reason)
    print(f"[PIPELINE] ⚠️  예외 허용: {norm_file}")
    print(f"           이유: {reason}")
    print(f"           감사 로그: {_allow_log_path(root).relative_to(root)}")
    return 0

def cmd_status(root: Path) -> int:
    """현재 세션 파이프라인 상태 출력."""
    state = _load_state(root)
    contract_ok = state.get("contract_confirmed", False)
    contract_icon = "✅" if contract_ok else "❌"
    print(f"[PIPELINE] 세션 상태")
    print(f"  CONTRACT 확인: {contract_icon} {'완료' if contract_ok else '미완료'}")
    print(f"  RED 완료 테스트: {len(state['red_done_tests'])}개")
    for t in state["red_done_tests"]:
        print(f"    ✅ {t}")
    print(f"  예외 허용: {len(state['allowed_impl_files'])}개")
    for f in state["allowed_impl_files"]:
        print(f"    ⚠️  {f}")
    print(f"  차단 횟수: {state.get('blocked_count', 0)}회")

    # allow 로그 표시
    log_path = _allow_log_path(root)
    if log_path.exists():
        entries = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if entries:
            print(f"\n  allow 로그 (총 {len(entries)}건):")
            for e in entries[-5:]:  # 최근 5개만
                print(f"    {e['ts']}  {e['file']}  [{e['reason']}]")
            if len(entries) > 5:
                print(f"    ... 외 {len(entries)-5}건 → {log_path.relative_to(root)}")
    return 0

def _unstaged_impl_fingerprints(root: Path) -> dict | None:
    """Snapshot only unstaged implementation content; never modify the checkout."""
    try:
        dirty = subprocess.run(["git", "diff", "--name-only", "-z"], cwd=root,
                               capture_output=True, text=True, check=True).stdout
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                                   cwd=root, capture_output=True, text=True, check=True).stdout
        result = {}
        for name in set((dirty + untracked).split("\0")):
            if not name or not _is_impl_file(name):
                continue
            path = root / name
            if path.is_symlink():
                import os
                payload = ("symlink:" + os.readlink(path)).encode("utf-8")
                result[name] = hashlib.sha256(payload).hexdigest()
            elif path.exists():
                mode = str(path.stat().st_mode & 0o777).encode("ascii")
                result[name] = hashlib.sha256(b"file:" + mode + b":" + path.read_bytes()).hexdigest()
            else:
                result[name] = "deleted"
        return result
    except (OSError, subprocess.CalledProcessError):
        if (root / ".git").exists():
            raise ValueError("변경 범위를 확인할 수 없습니다")
        return None


def _basis_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _check_digest(check: dict) -> str:
    return _basis_digest(check)


def _snapshot_resolve(root: Path, path: Path) -> Path:
    """Resolve inside the root at each hop, including external links returning inside."""
    import os
    root = root.resolve()
    pending = list(path.absolute().relative_to(root).parts)
    current, hops = root, 0
    while pending:
        part = pending.pop(0)
        if part == ".":
            continue
        if part == "..":
            current = current.parent
            current.relative_to(root)
            continue
        current = current / part
        if current.is_symlink():
            hops += 1
            if hops > 64:
                raise ValueError("코드 스냅샷 링크 순환 또는 과도한 링크 깊이")
            dest = Path(os.readlink(current))
            if dest.is_absolute():
                dest = dest.relative_to(root)
                current = root
            else:
                current = current.parent
            pending = list(dest.parts) + pending
    current.relative_to(root)
    return current


def _code_snapshot_digest(root: Path, verification_file: Path | None = None) -> str:
    """Hash candidate bytes and modes, independently of staged/unstaged location.

    Git candidates include tracked and non-ignored untracked files without
    extension guessing. Runtime .omc records and the verification record itself
    are excluded. Non-Git fixtures exclude dependency/cache directories only.
    This is content binding, not proof
    that the caller actually ran the declared verification.
    """
    root = root.resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                             capture_output=True, text=True)
        git_candidate = top.returncode == 0 and Path(top.stdout.strip()).resolve() == root.resolve()
        if git_candidate:
            tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                                     capture_output=True, text=True, check=True).stdout
            others = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                                    cwd=root, capture_output=True, text=True, check=True).stdout
            names = set(filter(None, tracked.split("\0")))
            names.update(filter(None, others.split("\0")))
        elif (root / ".git").exists():
            raise ValueError("코드 스냅샷의 Git 범위를 확인할 수 없습니다")
        else:
            names = {str(p.relative_to(root)) for p in root.rglob("*")
                     if (p.is_file() or p.is_symlink())
                     and not set(p.relative_to(root).parts) & {".omc", ".git", "node_modules", "__pycache__", ".pytest_cache"}}
        names = {name for name in names if Path(name).parts[0] not in {".omc", ".git"}
                 and (verification_file is None or (root / name).absolute() != verification_file.absolute())}

        def payload_for(path: Path, active: frozenset) -> bytes:
            if path.is_symlink():
                import os
                payload = b"symlink:" + os.fsencode(os.readlink(path))
                target = _snapshot_resolve(root, path)
                relative = target.relative_to(root.resolve())
                if relative.parts and relative.parts[0] in {".omc", ".git"}:
                    raise ValueError("코드 스냅샷 검사 제외 경로를 링크로 참조할 수 없습니다")
                if git_candidate:
                    ignored = subprocess.run(["git", "check-ignore", "-q", "--", str(relative)], cwd=root)
                    if ignored.returncode not in {0, 1}:
                        raise ValueError("코드 스냅샷 링크 대상의 Git 범위를 확인할 수 없습니다")
                    if ignored.returncode == 0:
                        raise ValueError("코드 스냅샷 링크 대상이 Git 검사 제외 경로입니다")
                elif set(relative.parts) & {"node_modules", "__pycache__", ".pytest_cache", ".omc", ".git"}:
                    raise ValueError("코드 스냅샷 링크 대상이 검사 제외 경로입니다")
                if target.is_dir():
                    if target in active:
                        raise ValueError("코드 스냅샷 폴더 링크 순환")
                    children = {}
                    for child_name in sorted(names):
                        child = root / child_name
                        if child != target and target in child.parents:
                            children[child_name] = hashlib.sha256(payload_for(child, active | {target})).hexdigest()
                    return payload + b":dir:" + str(relative).encode() + b":" + str(target.stat().st_mode & 0o777).encode() + b":" + _basis_digest(children).encode()
                if not target.is_file():
                    raise ValueError(f"코드 스냅샷 링크 대상이 없습니다: {path.relative_to(root)}")
                return payload + b":" + str(target.stat().st_mode & 0o777).encode() + b":" + target.read_bytes()
            else:
                if not path.is_file():
                    raise ValueError(f"코드 스냅샷 파일이 아닙니다: {path.relative_to(root)}")
                target = _snapshot_resolve(root, path)
                return str(target.stat().st_mode & 0o777).encode() + b":" + target.read_bytes()

        entries = {}
        for name in sorted(names):
            path = root / name
            if not path.exists() and not path.is_symlink():
                continue
            entries[name] = hashlib.sha256(payload_for(path, frozenset())).hexdigest()
        return _basis_digest(entries)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("코드 스냅샷을 읽을 수 없습니다") from exc


def _basis_path(root: Path, sid: str) -> Path:
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        raise ValueError("현재 작업 식별자를 확인할 수 없습니다")
    path = root / ".omc" / "verification-bases" / sid
    path.resolve().relative_to(root.resolve())
    return path


def _validate_basis(value: dict, sid: str) -> dict:
    if not isinstance(value, dict) or value.get("session_id") != sid:
        raise ValueError("승인된 검증 기준의 세션이 다릅니다")
    if type(value.get("revision")) is not int or value["revision"] < 1:
        raise ValueError("검증 기준 개정 번호가 올바르지 않습니다")
    for key in ("request", "approval_reference"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError("요청 원문과 사용자 승인 참조가 필요합니다")
    checks = value.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("승인된 필수 검증 항목이 필요합니다")
    ids = []
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("검증 항목 형식이 올바르지 않습니다")
        for key in ("id", "target", "method", "pass_condition", "target_version"):
            if not isinstance(check.get(key), str) or not check[key].strip():
                raise ValueError(f"검증 항목에 {key}가 필요합니다")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", check["id"]):
            raise ValueError("검증 ID가 올바르지 않습니다")
        ids.append(check["id"])
    if len(set(ids)) != len(ids):
        raise ValueError("검증 ID가 중복됐습니다")
    return value


def _load_approved_basis(root: Path, sid: str) -> dict:
    directory = _basis_path(root, sid)
    revisions = sorted(int(p.stem) for p in directory.glob("*.json") if p.stem.isdigit())
    if not revisions:
        raise ValueError("승인된 검증 기준이 없습니다. 기준을 제시하고 승인을 기록하세요")
    path = directory / f"{revisions[-1]}.json"
    path.resolve().relative_to(root.resolve())
    value = json.loads(path.read_text(encoding="utf-8"))
    value = _validate_basis(value, sid)
    if value["revision"] != revisions[-1]:
        raise ValueError("검증 기준 개정 번호가 일치하지 않습니다")
    return value


def cmd_approve_checks(root: Path, proposal: Path) -> int:
    """Record an already obtained human approval, not authenticate its author.

    Local files and approval references are caller-controlled. This prevents
    accidental scope loss; it is not an adversarial tamper-proof trust boundary.
    """
    try:
        latest = json.loads((root / ".omc/state/latest.json").read_text(encoding="utf-8"))
        sid = latest.get("latest_confirmed_session_id")
        if latest.get("latest_session_id") != sid:
            raise ValueError("현재 작업 승인을 먼저 확인하세요")
        value = _validate_basis(json.loads(proposal.read_text(encoding="utf-8")), sid)
        directory = _basis_path(root, sid)
        revisions = [int(p.stem) for p in directory.glob("*.json") if p.stem.isdigit()]
        if value["revision"] != max(revisions, default=0) + 1:
            raise ValueError("승인된 새 개정만 추가할 수 있습니다. 기존 기록은 보존합니다")
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{value['revision']}.json").open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        print(f"[PIPELINE] 사용자 승인 기준 기록: 개정 {value['revision']} / {_basis_digest(value)}")
        return 0
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"[PIPELINE] {exc}")
        return 1


def approved_verification_problems(root: Path, sid: str, state: dict, record: dict,
                                   verification_file: Path | None = None) -> list:
    """Shared completion/collector check, deliberately independent of final prose."""
    try:
        basis = _load_approved_basis(root, sid)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return [str(exc)]
    problems = []
    digest = _basis_digest(basis)
    if state.get("basis_sha256") != digest or record.get("basis_sha256") != digest:
        problems.append("승인된 검증 기준 개정과 CONTRACT 또는 증거 불일치")
    expected = {c["id"] for c in basis["checks"]}
    declared = state.get("required_checks", [])
    if not isinstance(declared, list) or any(not isinstance(x, str) for x in declared) or set(declared) != expected:
        problems.append("승인된 필수 검증 목록과 CONTRACT 불일치")
    checks = record.get("checks", {})
    if not isinstance(checks, dict):
        checks = {}
    try:
        current_snapshot = _code_snapshot_digest(root, verification_file)
    except ValueError as exc:
        problems.append(str(exc))
        current_snapshot = None
    for check in basis["checks"]:
        item = checks.get(check["id"], {})
        if not isinstance(item, dict):
            item = {}
        if item and item.get("definition_sha256") != _check_digest(check):
            problems.append(f"검증 대상·방법·버전 증거 불일치: {check['id']}")
        if item.get("status") == "passed" and (current_snapshot is None or item.get("code_snapshot_sha256") != current_snapshot):
            problems.append(f"검증 후 코드 스냅샷 증거 불일치: {check['id']}")
        if item.get("status") != "passed" or not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
            problems.append(f"승인된 필수 검증 미충족: {check['id']}")
    return problems


def cmd_verification_template(root: Path) -> int:
    """Emit the exact approved scope; never default an observation to success."""
    try:
        latest = json.loads((root / ".omc/state/latest.json").read_text(encoding="utf-8"))
        sid = latest.get("latest_confirmed_session_id")
        if latest.get("latest_session_id", sid) != sid:
            raise ValueError("현재 작업 승인을 먼저 확인하세요")
        basis = _load_approved_basis(root, sid)
        snapshot = _code_snapshot_digest(root)
        record = {"session_id": sid, "basis_sha256": _basis_digest(basis),
                  "implementation": {"status": "unverified", "evidence": ""},
                  "checks": {check["id"]: {"status": "unrun", "evidence": "",
                                           "definition_sha256": _check_digest(check),
                                           "code_snapshot_sha256": snapshot}
                             for check in basis["checks"]}}
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"[PIPELINE] {exc}")
        return 1


def cmd_contract_done(root: Path, content: str = "", required_checks: str = "") -> int:
    """CONTRACT 양식 작성 + 사용자 컨펌 완료 등록.

    content가 주어지면 SHA-256 해시를 저장해 우발적 위조를 방지한다.
    content가 없으면 타임스탬프 기반 해시를 사용한다.
    이후 edit_file(기존 파일 수정) 호출이 허용된다.
    세션 TTL 내에서 유효하며, TTL 만료 시 자동 초기화된다.

    latest.json에서 latest_confirmed_session_id를 읽어 pipeline_session.json에
    session_id로 기록한다 — 훅이 세션 간 오염 여부를 판별하는 데 사용한다.
    """
    state = _load_state(root)
    previous_sid = state.get("session_id", "")
    state["contract_confirmed"] = True
    requested_checks = list(dict.fromkeys(c.strip() for c in required_checks.split(",") if c.strip()))
    hash_source = content if content else f"confirmed_at:{time.time()}"
    state["contract_hash"] = _make_contract_hash(hash_source)
    state["session_id"] = ""

    # latest.json에서 session_id 읽어 pipeline_session에 기록
    latest_path = root / ".omc" / "state" / "latest.json"
    if latest_path.exists():
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            sid = latest.get("latest_confirmed_session_id") or latest.get("latest_session_id", "")
            if sid:
                state["session_id"] = sid
        except Exception:
            pass

    try:
        directory = _basis_path(root, state.get("session_id"))
        if directory.exists():
            basis = _load_approved_basis(root, state["session_id"])
            expected = {check["id"] for check in basis["checks"]}
            if set(requested_checks) != expected:
                raise ValueError("승인된 필수 검증 목록을 사용하세요: " + ",".join(sorted(expected)))
            state["basis_sha256"] = _basis_digest(basis)
            state["red_evidence_required"] = True
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        # Legacy sessions without a session id keep edit compatibility only.
        if state.get("session_id"):
            print(f"[PIPELINE] {exc}")
            return 1
    if (previous_sid and previous_sid == state.get("session_id")
            and "required_checks" in state
            and set(state["required_checks"]) != set(requested_checks)
            and state.get("basis_sha256") == _load_state(root).get("basis_sha256")):
        print("[PIPELINE] 현재 작업의 필수 검증 목록은 변경할 수 없습니다.")
        return 1
    state["required_checks"] = requested_checks
    if previous_sid != state.get("session_id") or "worktree_baseline" not in state:
        try:
            state["worktree_baseline"] = _unstaged_impl_fingerprints(root)
        except ValueError as exc:
            print(f"[PIPELINE] {exc}")
            return 1
    _save_state(root, state)
    print("[PIPELINE] ✅ CONTRACT 확인 완료 — 기존 파일 수정이 허용됩니다.")
    return 0

def _internal_verification_path(root: Path, path: Path) -> Path:
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError("검증 경로가 작업 폴더 밖을 가리킵니다")
    return resolved


def cmd_verification_path(root: Path) -> int:
    """Provide a safe per-session path without creating the verification record."""
    try:
        latest = json.loads((root / ".omc/state/latest.json").read_text(encoding="utf-8"))
        sid = latest.get("latest_confirmed_session_id", "")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
            raise ValueError("현재 작업 식별자를 확인할 수 없습니다")
        if latest.get("latest_session_id", sid) != sid:
            raise ValueError("현재 작업 식별자가 일치하지 않습니다")
        path = _internal_verification_path(root, Path(".omc") / "verification" / (sid + ".json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        print(path)
        return 0
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(str(exc))
        return 1


def evaluate_task_completion(root: Path, verification_file: Path, expected_session_id: str | None = None) -> dict:
    """One read-only decision used by both the completion CLI and collector.

    Evidence is supplied by the caller, not independently attested by this command.
    This gate cannot prevent an agent from writing an unrelated free-form response.
    """
    state = _load_state(root)
    problems = []
    failed = False
    mismatch = False
    try:
        latest = json.loads((root / ".omc/state/latest.json").read_text(encoding="utf-8"))
        if not isinstance(latest, dict):
            raise ValueError("session object required")
        sid = latest.get("latest_confirmed_session_id", "")
        if latest.get("latest_session_id", sid) != sid:
            sid = ""
    except (OSError, ValueError, TypeError):
        sid = ""
        problems.append("현재 세션 상태를 읽을 수 없습니다")
    if expected_session_id is not None and sid != expected_session_id:
        problems.append("수집 대상과 현재 작업 식별자가 다릅니다")
        mismatch = True
    record = {}
    record_read = False
    try:
        verification_file = _internal_verification_path(root, verification_file)
    except (OSError, ValueError) as exc:
        problems.append(str(exc))
    else:
        try:
            record = json.loads(verification_file.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("verification object required")
            record_read = True
        except (OSError, ValueError, TypeError):
            record = {}
            problems.append("검증 기록을 읽을 수 없습니다")
    if not sid or not state.get("contract_confirmed") or state.get("session_id") != sid:
        problems.append("현재 작업의 CONTRACT 등록이 없습니다")
    if not state.get("red_done_tests") or state.get("red_session_id") != sid or not sid:
        problems.append("현재 작업의 RED 등록이 없습니다")
    red_problems = _red_evidence_problems(root, state)
    problems.extend(red_problems)
    mismatch = mismatch or any("불일치" in problem for problem in red_problems)
    if record_read and (record.get("session_id") != sid or not sid):
        problems.append("검증 기록의 작업 식별자가 다릅니다")
        mismatch = True
    required = state.get("required_checks", [])
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        required = []
        problems.append("필수 검증 목록 형식이 올바르지 않습니다")
    basis_problems = approved_verification_problems(root, sid, state, record, verification_file)
    problems.extend(basis_problems)
    mismatch = mismatch or any("불일치" in problem for problem in basis_problems)
    if not required:
        problems.append("CONTRACT에 필수 검증 목록이 없습니다")
    checks = record.get("checks", {})
    if not isinstance(checks, dict):
        checks = {}
    for name in required:
        item = checks.get(name, {})
        if not isinstance(item, dict):
            item = {}
        status = item.get("status")
        failed = failed or status == "failed"
        mismatch = mismatch or status == "mismatch"
        evidence = item.get("evidence")
        if status != "passed" or not isinstance(evidence, str) or not evidence.strip():
            problems.append(f"필수 검증 미충족: {name}")
    implementation = record.get("implementation", {})
    if not isinstance(implementation, dict):
        implementation = {}
    implementation_evidence = implementation.get("evidence")
    applied = (bool(sid) and record.get("session_id") == sid
               and implementation.get("status") == "applied"
               and isinstance(implementation_evidence, str) and bool(implementation_evidence.strip()))
    if not applied:
        problems.append("구현 반영을 확인할 근거가 없습니다")
    # Existing unrelated changes are preserved, but further edits to them still block.
    try:
        current_changes = _unstaged_impl_fingerprints(root)
        baseline = state.get("worktree_baseline")
        if current_changes is not None:
            if not isinstance(baseline, dict):
                problems.append("작업 시작 시점의 변경 기준이 없습니다. CONTRACT를 확인하세요")
            elif any(baseline.get(name) != fingerprint for name, fingerprint in current_changes.items()):
                problems.append("현재 작업의 미스테이징 구현 변경이 남아 있어 staged 검사로 완료를 확정할 수 없습니다")
    except ValueError as exc:
        problems.append(str(exc))
    blocked = bool(problems)
    first = ("검증 실패·수정 필요" if failed else "증거 불일치·재검증 필요" if mismatch
             else "구현됨·검증 미완료" if applied else "구현 여부 확인 불가·검증 미완료") if blocked else "필수 검증 통과·작업 완료"
    registered = (bool(sid) and state.get("contract_confirmed") is True and state.get("session_id") == sid
                  and state.get("red_session_id") == sid and bool(state.get("red_done_tests")) and not red_problems)
    return {"outcome": "blocked" if blocked else "done", "first": first,
            "problems": problems, "registered": registered}


def cmd_complete_task(root: Path, verification_file: Path) -> int:
    evaluation = evaluate_task_completion(root, verification_file)
    blocked = evaluation["outcome"] == "blocked"
    print(evaluation["first"])
    for problem in evaluation["problems"]:
        print(f"- {problem}")
    print("다음 행동: 미충족 항목을 확인한 뒤 다시 검증합니다." if blocked
          else "다음 행동: omc-review로 변경사항을 검토합니다.")
    envelope = {"schema_version": "omc-output/v1", "stage": "task",
                "outcome": "blocked" if blocked else "done", "risk": "medium" if blocked else "low",
                "next_skill": None if blocked else "omc-review", "user_selection_needed": blocked,
                "reason_code": "verification_incomplete" if blocked else None}
    print("<!-- OMC_OUTPUT: " + json.dumps(envelope, ensure_ascii=False) + " -->")
    print("VERDICT: " + ("BLOCK" if blocked else "PROCEED"))
    return 1 if blocked else 0


def cmd_check_edit(root: Path, file_path: str, bypass: bool = False) -> int:
    """기존 파일 수정(edit_file) 전 CONTRACT 확인 여부 체크. 0=허용, 1=차단.

    - 구현 파일(_IMPL_EXTENSIONS)이 아니면 통과
    - 예외 파일(_BYPASS_PATTERNS)이면 통과
    - contract_confirmed=True 이면 통과
    - bypass=True (--autopilot) 이면 무조건 통과
    - 그 외 차단
    """
    if bypass:
        return 0  # autopilot 모드 → 통과

    if not _is_impl_file(file_path):
        return 0  # 구현 파일 아님 → 통과

    state = _load_state(root)

    if state.get("contract_confirmed", False):
        return 0  # CONTRACT 확인됨 → 통과

    # 차단
    state["blocked_count"] = state.get("blocked_count", 0) + 1
    _save_state(root, state)

    print()
    print("===========================================================")
    print(" CONTRACT GATE: 계획 미확인 — 기존 파일 수정 차단")
    print(f" 대상 파일: {file_path}")
    print()
    print(" Tier 1 파일 (*.ts·*.tsx·*.py 신규·동작변경) — CONTRACT 필요.")
    print(" Tier 0 통과 예시: *.styled.ts  /constants/  *.constants.ts  index.ts  *.config.ts")
    print()
    print(" ─── 복구 절차 ─────────────────────────────────────────")
    print(" 1. CONTRACT 양식 작성 (목표/범위/DoD/제약 확인)")
    print(" 2. 사용자 컨펌 완료 후:")
    print("      python3 scripts/omc_pipeline_guard.py contract-done")
    print(" 3. 이후 파일 수정 재시도")
    print()
    print(" ─── 예외 허용 (사용자 명시 필요) ──────────────────────")
    print(f"      python3 scripts/omc_pipeline_guard.py allow {file_path} --reason \"이유\"")
    print()
    print(" 현재 세션 상태 확인:")
    print("      python3 scripts/omc_pipeline_guard.py status")
    print("===========================================================")
    return 1

# ---------------------------------------------------------------------------
# 핵심 파일 패턴 (변경 시 scope 경고 트리거)
# ---------------------------------------------------------------------------
_CORE_FILE_PATTERNS = [
    r"/types\.ts$",
    r"/types/",
    r"/api\.ts$",
    r"/api/",
    r"\.types\.(ts|tsx)$",
    r"/schema\.(ts|tsx)$",
]

_SCOPE_FILE_THRESHOLD = 10    # staged 파일 수 경고 임계값
_SCOPE_LINE_THRESHOLD = 100   # staged 변경 라인 수 경고 임계값


def _is_core_file(path: str) -> bool:
    return any(re.search(p, path) for p in _CORE_FILE_PATTERNS)


def cmd_scope_check(root: Path) -> int:
    """staged 변경 범위를 분석하고 경고만 출력한다 (차단하지 않음).

    신호:
      - staged 파일 수 >= _SCOPE_FILE_THRESHOLD
      - staged 변경 라인 수 >= _SCOPE_LINE_THRESHOLD
      - 핵심 파일(types/api) 포함 여부

    항상 exit 0 — 경고만, 차단 없음.
    """
    import subprocess

    warnings: list[str] = []

    # staged 파일 목록
    try:
        files_output = subprocess.check_output(
            ["git", "diff", "--staged", "--name-only"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return 0  # git 없으면 조용히 통과

    staged_files = [f for f in files_output.splitlines() if f.strip()]
    file_count = len(staged_files)

    if file_count >= _SCOPE_FILE_THRESHOLD:
        warnings.append(f"  📦 staged 파일 {file_count}개 — 범위가 큽니다. CONTRACT가 있나요?")

    # staged 변경 라인 수
    try:
        stat_output = subprocess.check_output(
            ["git", "diff", "--staged", "--shortstat"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        import re as _re
        nums = _re.findall(r"(\d+) insertion|(\d+) deletion", stat_output)
        total_lines = sum(int(a or b) for a, b in nums)
        if total_lines >= _SCOPE_LINE_THRESHOLD:
            warnings.append(f"  📝 변경 라인 {total_lines}줄 — 큰 변경입니다. CONTRACT가 있나요?")
    except Exception:
        pass

    # 핵심 파일 포함 여부
    core_files = [f for f in staged_files if _is_core_file(f)]
    if core_files:
        files_str = ", ".join(core_files[:3])
        if len(core_files) > 3:
            files_str += f" 외 {len(core_files) - 3}개"
        warnings.append(f"  🔑 핵심 파일 수정: {files_str} — 영향 범위를 확인하세요.")

    if warnings:
        print()
        print("⚠️  SCOPE WARNING (차단 아님 — 정보 제공)")
        print("=" * 55)
        for w in warnings:
            print(w)
        print()
        print("  CONTRACT가 없다면 지금 작성하세요:")
        print("    python3 scripts/omc.py \"작업 내용\"")
        print("=" * 55)

    return 0  # 항상 통과


def cmd_reset(root: Path) -> int:
    """세션 파이프라인 상태 초기화."""
    _save_state(root, _empty_state())
    print("[PIPELINE] 세션 상태 초기화 완료")
    return 0

def cmd_session_start(root: Path) -> int:
    """새 세션 시작 시 contract_confirmed와 contract_hash를 초기화한다.

    red_done_files·allowed_impl_files 등 작업 진행 상태는 유지하되,
    CONTRACT 확인 플래그와 해시만 리셋해 세션마다 CONTRACT를 새로 받도록 강제한다.
    TTL 기반 자동 만료와 달리 명시적 세션 경계를 선언한다.
    """
    state = _load_state(root)
    state["contract_confirmed"] = False
    state["contract_hash"] = ""
    state["session_id"] = ""
    _save_state(root, state)
    print("[PIPELINE] 🔄 새 세션 시작 — CONTRACT 플래그 초기화")
    return 0

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="OMC 파이프라인 가드 — TDD RED 단계 추적")
    ap.add_argument("--target", type=Path, default=Path.cwd())
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="구현 파일 편집 전 RED 완료 여부 확인")
    p_check.add_argument("file", help="편집하려는 파일 경로")
    p_check.add_argument(
        "--autopilot",
        action="store_true",
        default=False,
        help="자동화 파이프라인 모드 — 신규 파일 생성 차단 우회 (opt-in)",
    )

    p_red = sub.add_parser("red-done", help="RED 단계 완료 등록")
    p_red.add_argument("test_file", help="RED 완료된 테스트 파일 경로")
    p_red.add_argument("--evidence-file", type=Path, help="승인 항목·새 실패 케이스·기존 실패·실제 출력의 RED JSON")

    p_allow = sub.add_parser("allow", help="특정 파일 RED 없이 예외 허용 (사용자 명시 + 이유 기록)")
    p_allow.add_argument("file", help="예외 허용할 구현 파일 경로")
    p_allow.add_argument("--reason", "-r", default="", help="예외 허용 이유 (감사 로그에 기록됩니다)")

    p_contract = sub.add_parser("contract-done", help="CONTRACT 양식 작성 + 사용자 컨펌 완료 등록 (이후 edit_file 허용)")
    p_contract.add_argument("--required-checks", default="", help="고정할 필수 검증 ID 목록 (쉼표 구분)")
    p_approval = sub.add_parser("approve-checks", help="이미 받은 사용자 검증 기준 승인을 개정별 기록 (진위 인증 아님)")
    p_approval.add_argument("--file", type=Path, required=True)
    p_complete = sub.add_parser("complete-task", help="현재 작업 등록과 필수 검증에 따른 최종 보고")
    p_complete.add_argument("--verification-file", type=Path, required=True)
    p_contract.add_argument("--content", default="", help="CONTRACT 내용 (해시 저장용, 우발적 위조 방지)")

    p_check_edit = sub.add_parser("check-edit", help="기존 파일 수정 전 CONTRACT 확인 여부 체크")
    p_check_edit.add_argument("file", help="수정하려는 파일 경로")
    p_check_edit.add_argument(
        "--autopilot",
        action="store_true",
        default=False,
        help="자동화 파이프라인 모드 — CONTRACT 차단 우회 (opt-in)",
    )

    sub.add_parser("verification-path", help="현재 작업의 내부 검증 기록 경로 제공")
    sub.add_parser("verification-template", help="승인된 검증 ID·개정·대상 해시를 담은 미실행 기록 출력")
    sub.add_parser("status", help="현재 세션 파이프라인 상태 출력")
    sub.add_parser("reset", help="세션 파이프라인 상태 초기화")
    sub.add_parser("session-start", help="새 세션 시작 — contract_confirmed 초기화 (red_done_files 보존)")
    sub.add_parser("scope-check", help="staged 변경 범위 경고 출력 (차단 없음)")

    args = ap.parse_args()
    root = Path(args.target).resolve()

    if args.cmd == "check":
        return cmd_check(root, args.file, bypass=args.autopilot)
    if args.cmd == "red-done":
        return cmd_red_done(root, args.test_file, args.evidence_file)
    if args.cmd == "allow":
        reason = getattr(args, "reason", "")
        return cmd_allow(root, args.file, reason)
    if args.cmd == "contract-done":
        return cmd_contract_done(root, getattr(args, "content", ""), args.required_checks)
    if args.cmd == "approve-checks":
        return cmd_approve_checks(root, args.file)
    if args.cmd == "complete-task":
        return cmd_complete_task(root, args.verification_file)
    if args.cmd == "check-edit":
        return cmd_check_edit(root, args.file, bypass=getattr(args, "autopilot", False))
    if args.cmd == "verification-path":
        return cmd_verification_path(root)
    if args.cmd == "verification-template":
        return cmd_verification_template(root)
    if args.cmd == "status":
        return cmd_status(root)
    if args.cmd == "reset":
        return cmd_reset(root)
    if args.cmd == "session-start":
        return cmd_session_start(root)
    if args.cmd == "scope-check":
        return cmd_scope_check(root)
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
