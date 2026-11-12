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
import sys
import hashlib
import json
import math
import tempfile
import os
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
    from omc_edit_resume import ENV, pipeline_path
    if os.environ.get(ENV):
        return pipeline_path(root)
    return root / ".omc" / "pipeline_session.json"

def _read_pipeline(root: Path) -> tuple[dict, dict | None]:
    """Read once; expiry is not deletion or evidence-content mismatch."""
    from omc_edit_resume import ENV
    if os.environ.get(ENV):
        # Invalid context must never become the empty-state/default-path fallback.
        path = _state_path(root)
        return json.loads(path.read_bytes()), None
    try:
        raw = _state_path(root).read_bytes()
        data = json.loads(raw)
        age = time.time() - data.get("started_at", 0)
        if age > _SESSION_TTL_SECONDS:
            return data, {"reason_code": "pipeline_ttl_expired",
                          "started_at": data.get("started_at"),
                          "ttl_seconds": _SESSION_TTL_SECONDS,
                          "state_sha256": hashlib.sha256(raw).hexdigest()}
        return data, None
    except (OSError, ValueError, TypeError, AttributeError):
        return _empty_state(), None


def _load_state(root: Path) -> dict:
    state, expiration = _read_pipeline(root)
    return _empty_state() if expiration is not None else state


def _archive_pipeline(root: Path, raw: bytes) -> None:
    directory = root / ".omc/state/pipeline-history"
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError("pipeline_history_symlink")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(raw).hexdigest() + ".json")
    try:
        with path.open("xb") as stream:
            stream.write(raw)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("pipeline_history_conflict")


def begin_work(root: Path, session: dict) -> None:
    """Only a confirmed independent natural work starts a new pipeline clock.

    Called by the state lifecycle, not by arbitrary CONTRACT re-registration.
    Same work confirmation/role switches never renew its eight-hour lifetime.
    """
    confirmation = session.get("confirmation")
    if (session.get("completion_action") != "start"
            or not isinstance(session.get("role_ids"), list)
            or "senior_coding" not in session["role_ids"]
            or not isinstance(confirmation, dict)
            or confirmation.get("status") != "confirmed"):
        return
    sid, work = session.get("session_id"), session.get("work_id")
    if not isinstance(sid, str) or not sid or not isinstance(work, str) or not work:
        return  # Legacy sessions are not implicitly migrated or re-authorized.
    path = _state_path(root)
    raw = path.read_bytes() if path.exists() else None
    previous, _ = _read_pipeline(root)
    if previous.get("owner_work_id") == work:
        if previous.get("owner_session_id") != sid:
            raise ValueError("pipeline_work_owner_mismatch")
        return
    if sid in (previous.get("session_id"), previous.get("owner_session_id")) and not previous.get("owner_work_id"):
        return  # Upgrading code cannot revive this already-existing approval.
    if raw is not None:
        _archive_pipeline(root, raw)
    state = _empty_state()
    state.update(session_id=sid, owner_session_id=sid, owner_work_id=work)
    _save_state(root, state)

def _save_state(root: Path, state: dict) -> None:
    from omc_edit_resume import ENV, frozen
    if frozen(root) and not os.environ.get(ENV):
        raise ValueError('explicit_resume_context_required')
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

def _is_tier0_file(path_str: str) -> bool:
    """Share the existing non-test file exemptions with the pre-edit hook."""
    return (Path(path_str).suffix in _IMPL_EXTENSIONS
            and not _is_impl_file(path_str) and not _is_test_file(path_str))


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
    # Current-session checks also cover existing app files and test edits.
    latest_path = root / '.omc/state/latest.json'
    try:
        latest_record = json.loads(latest_path.read_bytes()) if latest_path.exists() else {}
        latest = latest_record.get('latest_confirmed_session_id') or latest_record.get('latest_session_id')
        if latest_record.get('latest_session_id', latest) != latest:
            print('[PIPELINE] 현재 세션이 확정되지 않았습니다')
            return 1
    except (OSError, ValueError, TypeError, AttributeError):
        return 1
    state, expiration = _read_pipeline(root)
    if expiration is not None:
        print("[PIPELINE] pipeline_ttl_expired: 승인 유효기간이 만료됐습니다. 원본 기록을 유지합니다.")
        return 1
    try:
        normalized = Path(_normalize_path(root, file_path))
        file_path = str(normalized.relative_to(root.resolve()))
    except (ValueError, OSError):
        print('[PIPELINE] 외부 또는 해석 불가 편집 경로')
        return 1
    if latest and (normalized.suffix in _IMPL_EXTENSIONS):
        from omc_native_red_evidence import policy
        try:
            from omc_edit_resume import ENV as resume_env, load as load_resume
            resumed = bool(os.environ.get(resume_env))
            if resumed:
                latest = load_resume(root)['binding']['session_id']
            basis = _load_approved_basis(root,latest) if resumed else None
            pin = policy(root,basis=basis) if state.get('basis_sha256') or _basis_path(root, latest).exists() else None
            tier1 = _is_impl_file(file_path) or _is_test_file(file_path) or pin is not None
            if tier1 and (not state.get('contract_confirmed') or state.get('session_id') != latest):
                print('[PIPELINE] 현재 세션 CONTRACT가 필요합니다')
                return 1
            if pin and not _is_test_file(file_path):
                relative = str(normalized.relative_to(root.resolve()))
                if not any(relative == name or relative.startswith(name.rstrip('/') + '/') for name in pin['product_paths']):
                    raise ValueError('native_red_product_scope_mismatch')
                if _red_evidence_problems(root, state) or not state.get('red_evidence'):
                    raise ValueError('native_red_registration_required')
                return 0
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            print(f'[PIPELINE] native RED 편집 차단: {exc}')
            return 1
    if bypass:
        return 0  # legacy mode only; cannot bypass current CONTRACT/native RED

    if not _is_impl_file(file_path):
        return 0  # 구현 파일 아님 → 통과

    state, expiration = _read_pipeline(root)
    if expiration is not None:
        print("[PIPELINE] pipeline_ttl_expired: 승인 유효기간이 만료됐습니다. 원본 기록을 유지합니다.")
        return 1

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
    """Legacy declarations; opt-in native RED also requires runner/stream custody.

    Neither route authenticates semantic test relevance or hostile local edits.
    """
    evidence_file = evidence_file.resolve()
    evidence_file.relative_to(root.resolve())
    raw = evidence_file.read_bytes()
    doc = json.loads(raw)
    from omc_edit_resume import ENV, load
    if os.environ.get(ENV):
        value = load(root)
        marker = {'grant_id':value['grant_id'], 'generation':value['generation']}
        if doc.get('edit_resume') != marker:
            raise ValueError('new_generation_red_required')
        evidence_file.relative_to(root / value['generation'])
        (root / doc['output_file']).resolve().relative_to(root / value['generation'])
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
    if any('native_red' in c for c in basis['checks']):
        from omc_native_red_evidence import validate_evidence
        validate_evidence(root, doc, historical=historical)
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

    state, expiration = _read_pipeline(root)
    if expiration is not None:
        print("[PIPELINE] pipeline_ttl_expired: 승인 유효기간이 만료됐습니다. 원본 기록을 유지합니다.")
        return 1
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

    state, expiration = _read_pipeline(root)
    if expiration is not None:
        print("[PIPELINE] pipeline_ttl_expired: 승인 유효기간이 만료됐습니다. 원본 기록을 유지합니다.")
        return 1
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
    state, expiration = _read_pipeline(root)
    if expiration is not None:
        print("[PIPELINE] pipeline_ttl_expired: 승인 유효기간이 만료됐습니다. 원본 기록을 유지합니다.")
        return 1
    from omc_edit_resume import ENV, load
    if os.environ.get(ENV):
        value = load(root)
        print('[PIPELINE] 과거 원장: 만료 유지 / 편집 재개 세대: ' + value['generation'])
        print('  고정 재개 만료값: ' + str(value['deadline']))
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
            # Match the code snapshot boundary: OMC records and preserved
            # source copies are evidence, not unstaged product implementation.
            if not name or name.startswith('.omc/') or not _is_impl_file(name):
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
    kind = _completion_kind(value)
    if kind == MAINTENANCE_KIND:
        _validate_maintenance_policy(value)
    if kind != MAINTENANCE_KIND and 'maintenance' in value:
        raise ValueError('maintenance_policy_on_other_kind')
    if kind == TRANSFER_KIND:
        _validate_transfer_policy(value)
    elif 'transfer' in value:
        raise ValueError('transfer_policy_on_implementation')
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
        # Same-work kind cannot be downgraded through a preserving new session.
        session_file=root/'.omc/state/sessions'/sid/'session.json'
        if session_file.is_file():
            session=json.loads(session_file.read_text());work=session.get('work_id')
            pending_file=root/'.omc/state/pending-completion.json'
            if work and pending_file.is_file():
                pending=json.loads(pending_file.read_text())
                if pending.get('work_id')==work:
                    for member in pending.get('session_ids',[]):
                        if member!=sid and _basis_path(root,member).exists():
                            older=_load_approved_basis(root,member)
                            if (_completion_kind(older)!=_completion_kind(value)
                                    or older.get('transfer')!=value.get('transfer')
                                    or older.get('maintenance')!=value.get('maintenance')):
                                raise ValueError('completion_kind_immutable')
        from omc_visual_check import AD_PROFILE
        if _completion_kind(value) == 'implementation':
            from omc_visual_check import enforce_requirement
            enforce_requirement(root, value)
            from omc_home_observation import enforce_requirement as enforce_home
            enforce_home(root, value)
        directory = _basis_path(root, sid)
        revisions = [int(p.stem) for p in directory.glob("*.json") if p.stem.isdigit()]
        if revisions:
            previous = _load_approved_basis(root, sid)
            if (_completion_kind(previous) != _completion_kind(value)
                    or previous.get('transfer') != value.get('transfer')
                    or previous.get('maintenance') != value.get('maintenance')):
                raise ValueError('completion_kind_immutable')
            old_native = next((c for c in previous['checks'] if 'native_red' in c), None)
            if old_native is not None:
                new_native = next((c for c in value['checks'] if c['id'] == old_native['id']), None)
                if new_native != old_native or value.get('run_id') != previous.get('run_id'):
                    raise ValueError('native_red_approved_definition_immutable')
            old_home = next((c for c in previous['checks'] if 'home_motion_sha256' in c), None)
            if old_home is not None:
                new_home = next((c for c in value['checks'] if c['id'] == old_home['id']), None)
                if new_home != old_home or value.get('run_id') != previous.get('run_id'):
                    raise ValueError('home_approved_definition_immutable')
            if previous.get('task_profile') == AD_PROFILE:
                for key in ('task_profile', 'run_id'):
                    if value.get(key) != previous.get(key):
                        raise ValueError('visual_requirement_downgrade_or_policy_mismatch')
                old = next(c for c in previous['checks'] if c['id'] == 'browser-visual-controlled')
                new = next((c for c in value['checks'] if c['id'] == old['id']), {})
                if new != old:
                    raise ValueError('visual_approved_definition_immutable')
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
    from omc_edit_resume import ENV, load
    if os.environ.get(ENV):
        value = load(root)
        if record.get('edit_resume') != {'grant_id':value['grant_id'], 'generation':value['generation']}:
            problems.append('new_generation_verification_required')
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
        from omc_edit_resume import ENV, load
        if os.environ.get(ENV):
            sid = load(root)['binding']['session_id']
        basis = _load_approved_basis(root, sid)
        snapshot = _code_snapshot_digest(root)
        record = {"session_id": sid, "basis_sha256": _basis_digest(basis),
                  "implementation": {"status": "unverified", "evidence": ""},
                  "checks": {check["id"]: {"status": "unrun", "evidence": "",
                                           "definition_sha256": _check_digest(check),
                                           "code_snapshot_sha256": snapshot}
                             for check in basis["checks"]}}
        from omc_edit_resume import ENV, load
        if os.environ.get(ENV):
            value = load(root)
            record['edit_resume'] = {'grant_id':value['grant_id'], 'generation':value['generation']}
        if 'validation_commands_policy_sha256' in basis and 'tests' in record['checks']:
            record['checks']['tests']['validation_receipt'] = None
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"[PIPELINE] {exc}")
        return 1


def _legacy_contract_done(root: Path, content: str = "", required_checks: str = "") -> int:
    """CONTRACT 양식 작성 + 사용자 컨펌 완료 등록.

    content가 주어지면 SHA-256 해시를 저장해 우발적 위조를 방지한다.
    content가 없으면 타임스탬프 기반 해시를 사용한다.
    이후 edit_file(기존 파일 수정) 호출이 허용된다.
    세션 TTL 내에서 유효하며, TTL 만료 시 자동 초기화된다.

    latest.json에서 latest_confirmed_session_id를 읽어 pipeline_session.json에
    session_id로 기록한다 — 훅이 세션 간 오염 여부를 판별하는 데 사용한다.
    """
    state, expiration = _read_pipeline(root)
    if expiration is not None:
        print("[PIPELINE] pipeline_ttl_expired: 승인 유효기간(8시간)이 만료됐습니다. 원본 기록을 유지합니다.")
        return 1
    basis=None
    previous_sid = state.get("session_id", "")
    state["contract_confirmed"] = True
    state.pop("contract_reset_reason", None)
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

    # A bounded resume keeps the implementation owner even when the current
    # confirmed session is a read-only reviewer. The grant validates both.
    from omc_edit_resume import ENV as resume_env, load as load_resume
    if os.environ.get(resume_env):
        state['session_id'] = load_resume(root)['binding']['session_id']

    try:
        directory = _basis_path(root, state.get("session_id"))
        if directory.exists():
            basis = _load_approved_basis(root, state["session_id"])
            expected = {check["id"] for check in basis["checks"]}
            if set(requested_checks) != expected:
                raise ValueError("승인된 필수 검증 목록을 사용하세요: " + ",".join(sorted(expected)))
            state["basis_sha256"] = _basis_digest(basis)
            state["red_evidence_required"] = _completion_kind(basis) == "implementation"
            if _completion_kind(basis)==TRANSFER_KIND:
                state.setdefault('transfer_operator_snapshot',_code_snapshot_digest(root))
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
    maintenance_receipt=None
    try:
        if basis is not None and _completion_kind(basis)==MAINTENANCE_KIND:
            maintenance_receipt=_maintenance_register(root,state,basis)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,subprocess.SubprocessError) as exc:
        print('[PIPELINE] '+str(exc));return 1
    _save_state(root, state)
    print("[PIPELINE] ✅ CONTRACT 확인 완료 — 기존 파일 수정이 허용됩니다.")
    if maintenance_receipt:print(json.dumps(maintenance_receipt,ensure_ascii=False))
    return 0

def _internal_verification_path(root: Path, path: Path) -> Path:
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError("검증 경로가 작업 폴더 밖을 가리킵니다")
    from omc_edit_resume import ENV, load
    if os.environ.get(ENV):
        resolved.relative_to(root.resolve() / load(root)['generation'])
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
        from omc_edit_resume import ENV, load
        if os.environ.get(ENV):
            path = _internal_verification_path(root, Path(load(root)['generation']) / 'verification.json')
        else:
            path = _internal_verification_path(root, Path(".omc") / "verification" / (sid + ".json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        print(path)
        return 0
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(str(exc))
        return 1


def _completion_owner(root: Path, latest: dict, state: dict) -> str:
    """Resolve only explicitly preserved pending work, never an arbitrary old SID."""
    sid = latest.get("latest_confirmed_session_id", "")
    if not sid or latest.get("latest_session_id", sid) != sid:
        return ""
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        raise ValueError("completion_owner_session_invalid")
    if not state.get("session_id") or state.get("session_id") == sid:
        return sid
    # Legacy same-session verification remains unchanged. Cross-session reuse
    # requires the actual session ledger and validated v3 pending lineage.
    from omc_state import _pending_completion_matches_session
    observer_path = root / ".omc/state/sessions" / sid / "session.json"
    observer = json.loads(observer_path.read_text(encoding="utf-8"))
    pending = json.loads((root / ".omc/state/pending-completion.json").read_text(encoding="utf-8"))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if (
        observer.get("session_id") != sid
        or observer.get("confirmation", {}).get("status") != "confirmed"
        or observer.get("completion_action") not in {"preserve", "preserve-if-present"}
        or not isinstance(observer.get("role_ids"), list)
        or observer.get("git", {}).get("head") != head
        or pending.get("schema_version") != 3
        or not observer.get("work_id")
        or observer.get("work_id") != pending.get("work_id")
        or state.get("session_id") != pending.get("session_id")
        or not _pending_completion_matches_session(root, pending, current_head=head)
    ):
        raise ValueError("completion_owner_binding_invalid")
    if "senior_coding" in observer["role_ids"]:
        raise ValueError("completion_owner_role_invalid")
    for index, member in enumerate(pending["session_ids"]):
        entry = json.loads((root / ".omc/state/sessions" / member / "session.json").read_text(encoding="utf-8"))
        if (
            entry.get("lineage_root_session_id") != pending["root_session_id"]
            or entry.get("lineage_index") != index
            or entry.get("lineage_previous_session_id") != (pending["session_ids"][index-1] if index else None)
            or entry.get("confirmation", {}).get("status") != "confirmed"
            or "senior_coding" not in entry.get("role_ids", [])
            or entry.get("git", {}).get("head") != head
        ):
            raise ValueError("completion_owner_lineage_invalid")
    return pending["session_id"]


_REAPPROVAL_TTL = 8 * 60 * 60


def _reapproval_local(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("reapproval_relative_path_required")
    if ".." in Path(relative).parts:
        raise ValueError("reapproval_path_escape")
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("reapproval_symlink_unsupported")
    path.resolve().relative_to(root.resolve())
    return path


def _reapproval_identity(root: Path, state: dict) -> tuple[str, str]:
    from omc_state import _pending_completion_matches_session
    latest = json.loads((root / '.omc/state/latest.json').read_bytes())
    sid = _completion_owner(root, latest, state)
    pending = json.loads((root / '.omc/state/pending-completion.json').read_bytes())
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if (pending.get('schema_version') != 3 or pending.get('session_id') != sid
            or not isinstance(pending.get('work_id'), str) or not pending['work_id']
            or not _pending_completion_matches_session(root, pending, current_head=head)):
        raise ValueError('reapproval_work_binding_invalid')
    return sid, pending['work_id']


def _reapproval_path(root: Path, work_id: str) -> Path:
    # Session, HEAD and mutable pipeline hashes never determine the issuance slot.
    key = hashlib.sha256(work_id.encode('utf-8')).hexdigest()
    return _reapproval_local(root, '.omc/completion-reapprovals/' + key + '.json')


def _reapproval_binding(root: Path, state: dict, verification: Path) -> dict:
    sid, work_id = _reapproval_identity(root, state)
    verification = _internal_verification_path(root, verification)
    relative = str(verification.relative_to(root))
    verification = _reapproval_local(root, relative)
    raw = verification.read_bytes()
    record = json.loads(raw)
    basis = _load_approved_basis(root, sid)
    artifacts = {}
    queue = []

    unbound = object()  # Only internal inventory entries may omit a prior digest.

    def add(relative, expected=unbound):
        if expected is not unbound and (not isinstance(expected, str)
                                       or not re.fullmatch(r'[0-9a-f]{64}', expected)):
            raise ValueError('reapproval_evidence_digest_invalid')
        path = _reapproval_local(root, relative)
        value = path.read_bytes()
        digest = hashlib.sha256(value).hexdigest()
        if expected is not unbound and digest != expected:
            raise ValueError('reapproval_evidence_digest_mismatch')
        if relative not in artifacts:
            if len(artifacts) >= 1000:
                raise ValueError('reapproval_evidence_inventory_too_large')
            artifacts[relative] = digest
            if path.suffix == '.json':
                queue.append(value)

    # Bind the original ledger as well as evidence; no ledger is rewritten.
    add('.omc/pipeline_session.json')
    add('.omc/state/pending-completion.json')
    pending = json.loads((root / '.omc/state/pending-completion.json').read_bytes())
    for member in pending['session_ids']:
        add('.omc/state/sessions/' + member + '/session.json')
    add('.omc/verification-bases/' + sid + '/' + str(basis['revision']) + '.json')
    add(relative)
    checks = record.get('checks', {})
    for check in basis['checks']:
        item = checks.get(check['id'], {})
        refs = item.get('evidence_files')
        if not isinstance(refs, list) or not refs:
            raise ValueError('reapproval_explicit_evidence_files_required:' + check['id'])
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
                raise ValueError('reapproval_evidence_reference_invalid')
            add(ref['path'], ref['sha256'])
    for ref in state.get('red_evidence', {}).values():
        add(ref['file'], ref['sha256'])

    def references(value):
        # Only explicit typed references are followed; prose is never a path.
        if isinstance(value, dict):
            for pk, hk in [('path', 'sha256'), ('file', 'sha256'),
                           ('output_file', 'output_sha256'), ('report_file', 'report_sha256')]:
                if pk in value and hk in value:
                    add(value[pk], value[hk])
            for child in value.values():
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)
    while queue:
        references(json.loads(queue.pop()))
    return {'target': str(root.resolve()), 'session_id': sid, 'work_id': work_id,
            'pipeline_sha256': artifacts['.omc/pipeline_session.json'],
            'basis_sha256': _basis_digest(basis),
            'code_snapshot_sha256': _code_snapshot_digest(root, verification),
            'verification_file': relative, 'verification_sha256': hashlib.sha256(raw).hexdigest(),
            'artifacts': artifacts}


def _valid_completion_reapproval(root: Path, state: dict, verification: Path) -> bool:
    try:
        _, work = _reapproval_identity(root, state)
        record = json.loads(_reapproval_path(root, work).read_bytes())
        if (set(record) != {'schema_version', 'approval_reference', 'issued_at', 'expires_at', 'binding'}
                or record['schema_version'] != 'omc-completion-reapproval/v1'
                or not isinstance(record['approval_reference'], str) or not record['approval_reference'].strip()):
            return False
        issued, expires, now = record['issued_at'], record['expires_at'], time.time()
        if (type(issued) not in (int, float) or type(expires) not in (int, float)
                or not all(math.isfinite(t) for t in (issued, expires, now))
                or issued < 0 or expires != issued + _REAPPROVAL_TTL
                or not issued <= now < expires):
            return False
        bound = record['binding'] == _reapproval_binding(root, state, verification)
        # Evidence traversal can cross the deadline even when entry was valid.
        finished = time.time()
        return bound and math.isfinite(finished) and issued <= finished < expires
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, subprocess.SubprocessError):
        return False


def cmd_reapprove_completion(root: Path, proposal: Path) -> int:
    """Record an explicit approval for completion only; not authenticate a person."""
    temporary = None
    try:
        state, expiration = _read_pipeline(root)
        if expiration is None:
            raise ValueError('reapproval_requires_expired_work')
        relative = str(proposal.relative_to(root)) if proposal.is_absolute() else str(proposal)
        value = json.loads(_reapproval_local(root, relative).read_bytes())
        required = {'schema_version', 'session_id', 'work_id', 'approval_reference',
                    'pipeline_sha256', 'basis_sha256', 'verification_file',
                    'verification_sha256', 'code_snapshot_sha256'}
        if (not isinstance(value, dict) or set(value) != required
                or value['schema_version'] != 'omc-completion-reapproval-request/v1'
                or not isinstance(value['approval_reference'], str) or not value['approval_reference'].strip()):
            raise ValueError('reapproval_request_invalid')
        verification = _reapproval_local(root, value['verification_file'])
        binding = _reapproval_binding(root, state, verification)
        if any(value[k] != binding[k] for k in required - {'schema_version', 'approval_reference'}):
            raise ValueError('reapproval_request_binding_mismatch')
        path = _reapproval_path(root, binding['work_id'])
        # Read-only ordinary completion checks still apply; do not make TTL fresh.
        evaluation = _evaluate_completion_checks(root, verification, state)
        if evaluation['outcome'] != 'done':
            raise ValueError('reapproval_completion_checks_blocked:' + ';'.join(evaluation['problems']))
        path.parent.mkdir(parents=True, exist_ok=True)
        issued = time.time()
        if type(issued) not in (int, float) or not math.isfinite(issued) or issued < 0:
            raise ValueError('reapproval_clock_invalid')
        record = {'schema_version': 'omc-completion-reapproval/v1',
                  'approval_reference': value['approval_reference'], 'issued_at': issued,
                  'expires_at': issued + _REAPPROVAL_TTL, 'binding': binding}
        # Publish a complete, fsynced file atomically and never replace an issuance.
        fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'wb') as stream:
            stream.write((json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        current, expired = _read_pipeline(root)
        if (expired is None or _reapproval_binding(root, current, verification) != binding
                or _evaluate_completion_checks(root, verification, current)['outcome'] != 'done'
                or not issued <= time.time() < issued + _REAPPROVAL_TTL):
            raise ValueError('reapproval_changed_before_publish')
        os.link(temporary, path)  # EEXIST leaves the first record intact.
        print('[PIPELINE] 완료 판정 전용 재승인 기록: ' + str(path))
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, subprocess.SubprocessError) as exc:
        print('[PIPELINE] 재승인 차단: ' + str(exc))
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _expired_completion_evaluation(root: Path, verification_file: Path, state: dict,
                                  expiration: dict, expected_session_id: str | None) -> dict:
    applied = False
    try:
        path = _internal_verification_path(root, verification_file)
        record = json.loads(path.read_bytes())
        applied = (record.get("session_id") == state.get("session_id")
                   and record.get("implementation", {}).get("status") == "applied"
                   and expected_session_id in (None, state.get("session_id")))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {"outcome": "blocked",
            "first": "구현됨·검증 미완료" if applied else "구현 여부 확인 불가·검증 미완료",
            "problems": ["pipeline_ttl_expired: 파이프라인 승인 유효기간(8시간)이 만료됐습니다. "
                         "등록 누락 판정과 구분하며 원본 기록을 삭제하거나 재사용하지 않습니다."],
            "reason_code": "pipeline_ttl_expired", "expiration": expiration,
            "registered": None, "test_assessment": None,
            "visual_assessment": None, "home_assessment": None}


# A separate completion route; legacy implementation never enters this path.
TRANSFER_KIND = 'verified_commit_transfer'

# Maintenance evidence is local custody, not authentication of a local writer.
MAINTENANCE_KIND = 'verified_nonimplementation'
MAINTENANCE_POSTCHECK = '''import os,sys
def missing(p):
    try:
        os.lstat(p)
    except FileNotFoundError:
        return True
    return False
try:
    absent=all(missing(p) for p in sys.argv[1:])
except OSError:
    raise SystemExit(2)
raise SystemExit(0 if absent else 1)'''


def _validate_maintenance_policy(basis):
    policy=basis.get('maintenance')
    if not isinstance(policy,dict) or set(policy)!={'profile','allowed_paths','protected_files','evidence_root'}:
        raise ValueError('maintenance_policy_invalid')
    if policy['profile']!='cache_cleanup':raise ValueError('maintenance_profile_unsupported')
    paths=policy['allowed_paths']
    if (not isinstance(paths,list) or not paths or any(p not in ('.pytest_cache','scripts/__pycache__') for p in paths)
            or len(set(paths))!=len(paths)):
        raise ValueError('maintenance_paths_invalid')
    protected=policy['protected_files']
    if not isinstance(protected,list) or any(not isinstance(p,str) or not p for p in protected):
        raise ValueError('maintenance_protected_invalid')
    for name in [*protected,policy['evidence_root']]:
        if (not isinstance(name,str) or not name or Path(name).is_absolute()
                or '..' in Path(name).parts or str(Path(name))!=name):
            raise ValueError('maintenance_path_not_canonical')
    if not policy['evidence_root'].startswith('.omc/'):
        raise ValueError('maintenance_evidence_root_invalid')
    if any(p==a or p.startswith(a+'/') for p in protected for a in paths):
        raise ValueError('maintenance_protected_overlap')
    return policy


def _maintenance_path(root,name):
    if '..' in Path(name).parts:raise ValueError('maintenance_path_alias_unsupported')
    path=root/name
    if not path.resolve().is_relative_to(root):raise ValueError('maintenance_path_escape')
    for part in (path,*path.parents):
        if part==root:break
        if part.is_symlink():raise ValueError('maintenance_path_linked')
    return path


def _maintenance_runtime():
    import shutil
    python=shutil.which('python3')
    if python is None:raise ValueError('maintenance_python_missing')
    runtime={name:{'path':str(Path(path).resolve(strict=True)),
                  'sha256':hashlib.sha256(Path(path).resolve(strict=True).read_bytes()).hexdigest()}
            for name,path in [('rm','/bin/rm'),('python3',python)]}
    runtime['path_environment']=os.environ.get('PATH','')
    return runtime


def _maintenance_snapshot(root,policy,status_exclusions=()):
    identity=_transfer_identity(root)
    _transfer_index(root)  # reject split/sparse/unmerged index representations
    for name in policy['allowed_paths']:
        path=_maintenance_path(root,name)
        if path.exists() and not path.is_dir():raise ValueError('maintenance_target_not_directory')
        if _transfer_git(root,'ls-files','-z','--',name):raise ValueError('maintenance_tracked_cache')
        ignored=subprocess.run(['git','--no-optional-locks','check-ignore','-q','--',name+'/'],cwd=root,
            env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
        if ignored.returncode!=0:raise ValueError('maintenance_cache_not_ignored')
        # Later cache regeneration is allowed, links and special files are not.
        if path.exists():
            for parent,dirs,files in os.walk(path,followlinks=False,onerror=lambda e: (_ for _ in ()).throw(e)):
                for item in dirs+files:
                    child=Path(parent)/item
                    if child.is_symlink() or not (child.is_file() or child.is_dir()):
                        raise ValueError('maintenance_cache_type_unsupported')
    protected={name:_transfer_file_row(_maintenance_path(root,name)) for name in policy['protected_files']}
    _maintenance_path(root,policy['evidence_root'])
    return {'identity':identity,'head':_transfer_git(root,'rev-parse','HEAD').decode().strip(),
        'index_sha256':hashlib.sha256(_transfer_read(Path(identity['index']))).hexdigest(),
        'source_sha256':_code_snapshot_digest(root),'status':_transfer_git(root,'status','--porcelain=v1','-z','--untracked-files=all',
            '--','.',*(':(literal,exclude)'+name for name in sorted(status_exclusions))).hex(),
        'protected':protected,'runtime':_maintenance_runtime()}


def _maintenance_inventory(root,policy):
    files={}
    for name in policy['allowed_paths']:
        path=_maintenance_path(root,name)
        if not path.exists():continue
        for parent,dirs,names in os.walk(path,followlinks=False,onerror=lambda e: (_ for _ in ()).throw(e)):
            for item in names:
                child=Path(parent)/item;row=_transfer_file_row(child)
                row['allocated_bytes']=child.stat().st_blocks*512
                files[str(child.relative_to(root))]=row
                if len(files)>100000:raise ValueError('maintenance_inventory_budget')
    return files


def _maintenance_evidence_inventory(root,policy):
    directory=_maintenance_path(root,policy['evidence_root']);rows={}
    if not directory.exists():return rows
    if not directory.is_dir():raise ValueError('maintenance_evidence_not_directory')
    for parent,dirs,files in os.walk(directory,followlinks=False,onerror=lambda e: (_ for _ in ()).throw(e)):
        folder=Path(parent)
        rows[str(folder.relative_to(root))]={'kind':'directory','mode':folder.stat().st_mode&0o777}
        for name in dirs+files:
            path=_maintenance_path(root,str((folder/name).relative_to(root)))
            if name in files:rows[str(path.relative_to(root))]=_transfer_file_row(path)
        if len(rows)>100000:raise ValueError('maintenance_evidence_inventory_budget')
    return rows


def _maintenance_preserved_snapshot(root,policy,baseline,record,operation):
    before=baseline.get('evidence_inventory')
    if not isinstance(before,dict):raise ValueError('maintenance_evidence_baseline_missing')
    current=_maintenance_evidence_inventory(root,policy)
    if any(current.get(name)!=row for name,row in before.items()):
        raise ValueError('maintenance_existing_evidence_changed')
    refs=[record['maintenance_operation_ref'],operation['native_prefix_ref']]
    for check in record['checks'].values():refs.extend(check.get('evidence_files',[]))
    referenced=set()
    for ref in refs:
        _maintenance_read_ref(root,ref)
        path=_maintenance_path(root,ref['path'])
        if path.is_relative_to(root/policy['evidence_root']):referenced.add(ref['path'])
    additions=set(current)-set(before)
    allowed=set(referenced)
    for name in referenced:
        path=root/name
        for parent in path.parents:
            if not parent.is_relative_to(root/policy['evidence_root']):break
            allowed.add(str(parent.relative_to(root)))
    if additions-allowed:raise ValueError('maintenance_unbound_evidence_added')
    new_files={name for name in additions if current[name]['kind']=='file'}
    return _maintenance_snapshot(root,policy,status_exclusions=new_files)


def _maintenance_context(root):
    from omc_edit_resume import ENV, frozen, load
    if os.environ.get(ENV):load(root)
    elif frozen(root):raise ValueError('expired_work_lifecycle_frozen')
    state,expiration=_read_pipeline(root)
    if expiration is not None:raise ValueError('pipeline_ttl_expired')
    latest=json.loads((root/'.omc/state/latest.json').read_bytes())
    sid=_completion_owner(root,latest,state);basis=_load_approved_basis(root,sid)
    if _completion_kind(basis)!=MAINTENANCE_KIND:raise ValueError('completion_kind_entry_mismatch')
    if state.get('session_id')!=sid or not state.get('contract_confirmed'):raise ValueError('maintenance_contract_missing')
    session=json.loads(_transfer_local(root,'.omc/state/sessions/'+sid+'/session.json').read_bytes())
    work=session.get('work_id')
    if (session.get('session_id')!=sid or session.get('confirmation',{}).get('status')!='confirmed'
            or not isinstance(work,str) or not work):raise ValueError('maintenance_work_binding_invalid')
    if state.get('basis_sha256')!=_basis_digest(basis):raise ValueError('maintenance_contract_binding_mismatch')
    return state,sid,basis,work


def _maintenance_register(root,state,basis,origin_raw=None,control=None):
    sid=state['session_id'];policy=_validate_maintenance_policy(basis)
    path=_maintenance_path(root,'.omc/state/maintenance-baselines/'+sid+'.json')
    if path.exists() or state.get('maintenance_baseline_ref'):
        raise ValueError('maintenance_baseline_already_registered')
    session=json.loads(_transfer_local(root,'.omc/state/sessions/'+sid+'/session.json').read_bytes())
    if (session.get('session_id')!=sid or session.get('confirmation',{}).get('status')!='confirmed'
            or not isinstance(session.get('work_id'),str) or not session['work_id']):
        raise ValueError('maintenance_work_binding_invalid')
    for old in path.parent.glob('*.json'):
        if json.loads(_transfer_read(old)).get('work_id')==session['work_id']:
            raise ValueError('maintenance_work_baseline_already_registered')
    before=_maintenance_snapshot(root,policy);inventory=_maintenance_inventory(root,policy)
    evidence_inventory=_maintenance_evidence_inventory(root,policy)
    value={'schema_version':'omc-maintenance-baseline/v2','session_id':sid,'work_id':session['work_id'],
           'basis_sha256':_basis_digest(basis),'policy':policy,'snapshot':before,'inventory':inventory,
           'evidence_inventory':evidence_inventory,'origin_state_sha256':hashlib.sha256(origin_raw or b'').hexdigest(),
           'origin_started_at':state['started_at'],'contract_hash':state['contract_hash'],'control':control}
    if (before!=_maintenance_snapshot(root,policy) or inventory!=_maintenance_inventory(root,policy)
            or evidence_inventory!=_maintenance_evidence_inventory(root,policy)):
        raise ValueError('maintenance_changed_during_registration')
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=_maintenance_baseline_bytes(value)
    _maintenance_atomic(path,raw,exclusive=True)
    state['maintenance_baseline_ref']={'path':str(path.relative_to(root)),'sha256':hashlib.sha256(raw).hexdigest()}
    return {'status':'maintenance_baseline_registered','work_id':session['work_id'],
            'basis_sha256':_basis_digest(basis),'baseline_file':str(path),'baseline_sha256':hashlib.sha256(raw).hexdigest()}


def _maintenance_read_ref(root,ref):
    if not isinstance(ref,dict) or set(ref)!={'path','sha256'}:raise ValueError('maintenance_ref_invalid')
    raw=_transfer_read(_maintenance_path(root,ref['path']))
    if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('maintenance_ref_hash_mismatch')
    return raw


# The failed attempt is evidence, never a new approval or a renewed clock.
EVIDENCE_READ_LIMIT = 8 * 1024 * 1024
MAINTENANCE_LIMIT = EVIDENCE_READ_LIMIT
MAINTENANCE_READY_ENV = 'OMC_MAINTENANCE_READY_FILE'


def _maintenance_baseline_bytes(value):
    raw=json.dumps(value,ensure_ascii=False,indent=2).encode()
    if len(raw)>MAINTENANCE_LIMIT:raise ValueError('maintenance_baseline_size_unsupported')
    return raw


def _maintenance_atomic(path, raw, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if exclusive: os.link(temporary, path)
        else: os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _maintenance_approved(root):
    latest = json.loads(_transfer_read(root/'.omc/state/latest.json'))
    sid = latest['latest_confirmed_session_id']
    if latest.get('latest_session_id', sid) != sid: raise ValueError('maintenance_session_unconfirmed')
    basis = _load_approved_basis(root, sid)
    session = json.loads(_transfer_read(root/'.omc/state/sessions'/sid/'session.json'))
    if session.get('confirmation', {}).get('status') != 'confirmed' or not session.get('work_id'):
        raise ValueError('maintenance_work_binding_invalid')
    return sid, basis, session['work_id']


def _maintenance_origin(root):
    raw = _transfer_read(_state_path(root))
    state = json.loads(raw)
    started = state.get('started_at')
    if type(started) not in (int, float) or not math.isfinite(started) or started > time.time():
        raise ValueError('maintenance_clock_invalid')
    if time.time()-started >= _SESSION_TTL_SECONDS: raise ValueError('pipeline_ttl_expired')
    from omc_edit_resume import frozen
    if frozen(root): raise ValueError('expired_work_lifecycle_frozen')
    return raw, state


def maintenance_launch(root):
    """Reserve a control directory before a selected native child starts."""
    import uuid
    root = root.resolve()
    try: sid, basis, work = _maintenance_approved(root)
    except (OSError, ValueError, KeyError): return None
    if _completion_kind(basis) != MAINTENANCE_KIND: return None
    _maintenance_origin(root)
    ident = uuid.uuid4().hex
    folder = _maintenance_path(root, '.omc/state/maintenance-control/'+ident)
    ignored = subprocess.run(['git','check-ignore','-q','--',str(folder)], cwd=root)
    if ignored.returncode != 0: raise ValueError('maintenance_control_not_ignored')
    folder.mkdir(parents=True, exist_ok=False)
    control = {'schema_version':'omc-maintenance-control/v1','root':str(root),
               'launch_id':ident,'ready_file':str(folder/'ready.json'),
               'session_id':sid,'work_id':work,'basis_sha256':_basis_digest(basis)}
    _maintenance_atomic(folder/'launch.json', json.dumps(control).encode(), exclusive=True)
    return control


def _maintenance_control(root, ready, sid, basis, work):
    path = _maintenance_path(root, ready)
    if path.name != 'ready.json' or path.parent.parent != root/'.omc/state/maintenance-control':
        raise ValueError('maintenance_control_scope_mismatch')
    if not re.fullmatch('[0-9a-f]{32}', path.parent.name): raise ValueError('maintenance_launch_invalid')
    control = json.loads(_transfer_read(path.parent/'launch.json'))
    expected = {'schema_version':'omc-maintenance-control/v1','root':str(root),
                'launch_id':path.parent.name,'ready_file':str(path),'session_id':sid,
                'work_id':work,'basis_sha256':_basis_digest(basis)}
    if control != expected: raise ValueError('maintenance_control_binding_mismatch')
    return control


def _maintenance_native(raw):
    """Parse complete native rows without manufacturing tool returns."""
    if not raw.endswith(b'\n'): raise ValueError('maintenance_native_prefix_incomplete')
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    calls, returns = {}, {}
    for i, row in enumerate(rows):
        content = row.get('message', {}).get('content', [])
        if not isinstance(content, list): raise ValueError('maintenance_native_provider_unsupported')
        for block in content:
            if block.get('type') == 'tool_use':
                ident = block.get('id')
                if row.get('type') != 'assistant' or not isinstance(ident, str) or ident in calls:
                    raise ValueError('maintenance_native_duplicate_call')
                calls[ident] = (i, block)
            elif block.get('type') == 'tool_result':
                ident = block.get('tool_use_id')
                if row.get('type') != 'user' or ident not in calls or ident in returns:
                    raise ValueError('maintenance_native_return_mismatch')
                returns[ident] = (i, block)
    return rows, calls, returns


def maintenance_recovery(root, ref, basis, baseline, origin_raw=None):
    """Shared immutable predicate for retry CLI, info CLI and final collector."""
    import shlex
    raw = _maintenance_read_ref(root, ref)
    rows, calls, returns = _maintenance_native(raw)
    last = [(ident, block) for ident, (i, block) in returns.items() if i == len(rows)-1]
    if len(last) != 1: raise ValueError('maintenance_recovery_return_missing')
    ident, result = last[0]
    _, call = calls[ident]
    args = ['python3','scripts/omc_pipeline_guard.py','contract-done','--required-checks',
            ','.join(c['id'] for c in basis['checks'])]
    inp = call.get('input', {})
    cwd = inp.get('cwd') or next((r.get('cwd') for r in rows if r.get('subtype')=='init'), None)
    if (call.get('name') != 'Bash' or inp.get('command') != shlex.join(args) or cwd != str(root)
            or any(k in inp for k in ('env','shell','executable')) or inp.get('run_in_background')
            or type(result.get('exit_code')) is not int or result['exit_code'] == 0 or result.get('is_error') is not True):
        raise ValueError('maintenance_recovery_call_invalid')
    failures = []
    for line in result.get('content', '').splitlines():
        try: value = json.loads(line)
        except ValueError: continue
        if isinstance(value, dict) and value.get('status') == 'maintenance_registration_failed': failures.append(value)
    if len(failures) != 1: raise ValueError('maintenance_recovery_failure_missing')
    # Permit the same bounded pre-CONTRACT preparation as the final collector.
    from omc_native_command import read_command_reason
    approvals = {}
    for other, (index, block) in calls.items():
        if other == ident: continue
        if other not in returns: raise ValueError('maintenance_recovery_return_missing')
        j, returned = returns[other]
        command = block.get('input', {}).get('command', '')
        argv = shlex.split(command) if isinstance(command, str) else []
        if (block.get('name') == 'Bash' and len(argv) == 5
                and argv[:4] == ['python3','scripts/omc_pipeline_guard.py','approve-checks','--file']):
            proposal = _maintenance_path(root, argv[4])
            expected = f"[PIPELINE] 사용자 승인 기준 기록: 개정 {basis['revision']} / {_basis_digest(basis)}"
            if (not proposal.is_relative_to(root/'.omc') or json.loads(_transfer_read(proposal)) != basis
                    or returned.get('exit_code') != 0 or returned.get('is_error') is not False
                    or returned.get('content', '').strip() != expected or not index < j < calls[ident][0]):
                raise ValueError('maintenance_recovery_approval_invalid')
            approvals[other] = (index, proposal)
    for other, (index, block) in calls.items():
        if other == ident or other in approvals: continue
        name = block.get('name'); inp = block.get('input', {})
        if name in {'Read','Glob','Grep'}: continue
        if name == 'Bash' and read_command_reason(root, inp.get('command', '')) is None: continue
        if name == 'Write' and other in returns:
            j, returned = returns[other]
            path = _maintenance_path(root, inp.get('file_path', ''))
            if (returned.get('is_error') is False and index < j
                    and any(path == proposal and j < ai for ai, proposal in approvals.values())
                    and json.loads(inp.get('content', '')) == basis): continue
        raise ValueError('maintenance_recovery_operation_unsupported')
    failure = failures[0]
    control = baseline.get('control')
    if baseline.get('schema_version') != 'omc-maintenance-baseline/v2' or not control:
        raise ValueError('maintenance_recovery_baseline_unsupported')
    sid, work = baseline['session_id'], baseline['work_id']
    bp = root/'.omc/state/maintenance-baselines'/ (sid+'.json')
    digest = hashlib.sha256(_transfer_read(bp)).hexdigest()
    expected = {'status':'maintenance_registration_failed','failure_stage':'state_save',
                'session_id':sid,'work_id':work,'basis_sha256':_basis_digest(basis),
                'baseline_sha256':digest,'origin_state_sha256':baseline['origin_state_sha256'],
                'origin_started_at':baseline['origin_started_at'],
                'launch_id':control['launch_id'],'ready_file':control['ready_file']}
    if failure != expected: raise ValueError('maintenance_recovery_binding_mismatch')
    name = str(Path(basis['maintenance']['evidence_root'])/'recovery'/(hashlib.sha256(ident.encode()).hexdigest()+'.jsonl'))
    if ref['path'] != name: raise ValueError('maintenance_recovery_scope_mismatch')
    if origin_raw is not None:
        if hashlib.sha256(origin_raw).hexdigest() != baseline['origin_state_sha256']:
            raise ValueError('maintenance_recovery_origin_changed')
    return {'prefix_ref':ref,'failed_call_id':ident,'origin_state_sha256':baseline['origin_state_sha256'],
            'baseline_sha256':digest,'launch_id':control['launch_id']}


def maintenance_publisher(root, events, control):
    """Publish prefix first, ready second; never rewrite native event bytes."""
    published = False
    def publish():
        nonlocal published
        if published: return
        raw = events.read_bytes()
        offset = 0
        for line in raw.splitlines(keepends=True):
            offset += len(line)
            if not line.endswith(b'\n'): break
            row = json.loads(line)
            for block in row.get('message', {}).get('content', []):
                if block.get('type') != 'tool_result': continue
                for output_line in block.get('content', '').splitlines():
                    try: failure = json.loads(output_line)
                    except ValueError: continue
                    if not isinstance(failure, dict) or failure.get('status') != 'maintenance_registration_failed': continue
                    if failure.get('launch_id') != control['launch_id']: continue
                    sid, basis, _ = _maintenance_approved(root)
                    baseline = json.loads(_transfer_read(root/'.omc/state/maintenance-baselines'/(sid+'.json')))
                    ident = block['tool_use_id']
                    name = str(Path(basis['maintenance']['evidence_root'])/'recovery'/(hashlib.sha256(ident.encode()).hexdigest()+'.jsonl'))
                    prefix = raw[:offset]
                    if len(prefix) > MAINTENANCE_LIMIT: raise ValueError('maintenance_prefix_size_unsupported')
                    path = _maintenance_path(root, name)
                    if path.exists():
                        if _transfer_read(path) != prefix: raise ValueError('maintenance_prefix_conflict')
                    else: _maintenance_atomic(path, prefix, exclusive=True)
                    ref = {'path':name,'sha256':hashlib.sha256(prefix).hexdigest()}
                    desc = maintenance_recovery(root, ref, basis, baseline)
                    ready = {'schema_version':'omc-maintenance-ready/v1','session_id':sid,
                             'work_id':baseline['work_id'],'basis_sha256':_basis_digest(basis),
                             'baseline_sha256':desc['baseline_sha256'],'launch_id':control['launch_id'],
                             'failed_call_id':ident,'prefix_ref':ref}
                    target = _maintenance_path(root, control['ready_file'])
                    value = json.dumps(ready).encode()
                    if target.exists():
                        if _transfer_read(target) != value: raise ValueError('maintenance_ready_conflict')
                    else: _maintenance_atomic(target, value, exclusive=True)
                    published = True
                    return
    return publish


def maintenance_recovery_info(root, ready_file):
    sid, basis, work = _maintenance_approved(root)
    if _completion_kind(basis) != MAINTENANCE_KIND: raise ValueError('completion_kind_entry_mismatch')
    origin, state = _maintenance_origin(root)
    if state.get('maintenance_baseline_ref'): raise ValueError('maintenance_baseline_already_registered')
    baseline = json.loads(_transfer_read(root/'.omc/state/maintenance-baselines'/(sid+'.json')))
    control = _maintenance_control(root, ready_file, sid, basis, work)
    if baseline.get('control') != control: raise ValueError('maintenance_control_binding_mismatch')
    deadline = time.monotonic()+min(10, _SESSION_TTL_SECONDS-(time.time()-state['started_at']))
    path = _maintenance_path(root, ready_file)
    while not path.exists():
        if time.monotonic() >= deadline: raise ValueError('maintenance_ready_timeout')
        time.sleep(0.05)
    raw = _transfer_read(path)
    if len(raw)>16384: raise ValueError('maintenance_ready_size_unsupported')
    ready = json.loads(raw)
    desc = maintenance_recovery(root, ready['prefix_ref'], basis, baseline, origin)
    expected = {'schema_version':'omc-maintenance-ready/v1','session_id':sid,'work_id':work,
                'basis_sha256':_basis_digest(basis),'baseline_sha256':desc['baseline_sha256'],
                'launch_id':control['launch_id'],'failed_call_id':desc['failed_call_id'],'prefix_ref':desc['prefix_ref']}
    if ready != expected: raise ValueError('maintenance_ready_binding_mismatch')
    current, _ = _maintenance_origin(root)
    if current != origin or _maintenance_approved(root) != (sid,basis,work):
        raise ValueError('maintenance_changed_during_validation')
    return {'schema_version':'omc-maintenance-recovery-info/v1','launch_id':desc['launch_id'],
            'failed_call_id':desc['failed_call_id'],'prefix_ref':desc['prefix_ref']}


def cmd_contract_done(root, content='', required_checks='', recovery_file=None, recovery_sha256=None):
    try:
        sid, basis, work = _maintenance_approved(root)
    except (OSError, ValueError, KeyError, TypeError):
        if recovery_file or recovery_sha256:
            print('[PIPELINE] maintenance_recovery_basis_missing'); return 1
        return _legacy_contract_done(root, content, required_checks)
    if _completion_kind(basis) != MAINTENANCE_KIND:
        if recovery_file or recovery_sha256:
            print('[PIPELINE] completion_kind_entry_mismatch'); return 1
        return _legacy_contract_done(root, content, required_checks)
    from omc_state import _omc_lock
    from omc_edit_resume import lock
    try:
        with _omc_lock(root), lock(root, work):
            if _maintenance_approved(root) != (sid,basis,work): raise ValueError('maintenance_work_changed')
            origin, state = _maintenance_origin(root)
            if state.get('owner_work_id',work)!=work or state.get('owner_session_id',sid)!=sid:
                raise ValueError('maintenance_work_changed')
            if state.get('maintenance_baseline_ref'): raise ValueError('maintenance_baseline_already_registered')
            if set(required_checks.split(',')) != {c['id'] for c in basis['checks']}:
                raise ValueError('maintenance_required_checks_mismatch')
            state.update(session_id=sid, contract_confirmed=True, basis_sha256=_basis_digest(basis),
                         required_checks=required_checks.split(','), red_evidence_required=False)
            state.setdefault('worktree_baseline', _unstaged_impl_fingerprints(root))
            bp = root/'.omc/state/maintenance-baselines'/(sid+'.json')
            if recovery_file is not None or recovery_sha256 is not None:
                if not recovery_file or not recovery_sha256: raise ValueError('maintenance_recovery_pair_required')
                baseline = json.loads(_transfer_read(bp))
                contract_hash = baseline.get('contract_hash')
                if not isinstance(contract_hash,str) or not re.fullmatch('[0-9a-f]{64}',contract_hash):
                    raise ValueError('maintenance_contract_hash_missing')
                if content and _make_contract_hash(content)!=contract_hash:
                    raise ValueError('maintenance_contract_content_mismatch')
                state['contract_hash']=contract_hash
                ref = {'path':recovery_file,'sha256':recovery_sha256}
                desc = maintenance_recovery(root, ref, basis, baseline, origin)
                if baseline['policy'] != basis['maintenance'] or baseline['session_id'] != sid or baseline['work_id'] != work:
                    raise ValueError('maintenance_baseline_binding_mismatch')
                current = _maintenance_preserved_snapshot(root, basis['maintenance'], baseline,
                    {'maintenance_operation_ref':ref,'checks':{}}, {'native_prefix_ref':ref})
                if current != baseline['snapshot'] or _maintenance_inventory(root, basis['maintenance']) != baseline['inventory']:
                    raise ValueError('maintenance_preservation_changed')
                state['maintenance_baseline_ref']={'path':str(bp.relative_to(root)),'sha256':desc['baseline_sha256']}
                state['maintenance_recovery']=desc
                receipt={'status':'maintenance_baseline_registered','work_id':work,'basis_sha256':_basis_digest(basis),
                         'baseline_file':str(bp),'baseline_sha256':desc['baseline_sha256'],'recovery':desc}
            else:
                ready = os.environ.get(MAINTENANCE_READY_ENV)
                control = _maintenance_control(root,ready,sid,basis,work) if ready else None
                state['contract_hash']=_make_contract_hash(content if content else f'confirmed_at:{time.time()}')
                receipt = _maintenance_register(root,state,basis,origin_raw=origin,control=control)
            if _maintenance_origin(root)[0] != origin or _maintenance_approved(root) != (sid,basis,work):
                raise ValueError('maintenance_changed_before_save')
            try:
                _maintenance_atomic(_state_path(root), (json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode())
            except OSError:
                baseline = json.loads(_transfer_read(bp)); control = baseline.get('control')
                if control:
                    failure={'status':'maintenance_registration_failed','failure_stage':'state_save',
                             'session_id':sid,'work_id':work,'basis_sha256':_basis_digest(basis),
                             'baseline_sha256':hashlib.sha256(_transfer_read(bp)).hexdigest(),
                             'origin_state_sha256':baseline['origin_state_sha256'],'origin_started_at':baseline['origin_started_at'],
                             'launch_id':control['launch_id'],'ready_file':control['ready_file']}
                    print(json.dumps(failure,ensure_ascii=False))
                    return 1
                raise
            print('[PIPELINE] ✅ CONTRACT 확인 완료 — 기존 파일 수정이 허용됩니다.')
            print(json.dumps(receipt,ensure_ascii=False)); return 0
    except (OSError,ValueError,TypeError,KeyError,AttributeError,subprocess.SubprocessError) as exc:
        print('[PIPELINE] '+str(exc)); return 1


def maintenance_operation(root,record,full_native=None):
    """One read-only predicate for CLI and collector, exact supported Bash protocol."""
    import shlex
    state,sid,basis,work=_maintenance_context(root);policy=_validate_maintenance_policy(basis)
    baseline_ref=state.get('maintenance_baseline_ref');baseline=json.loads(_maintenance_read_ref(root,baseline_ref))
    if (baseline.get('schema_version') not in {'omc-maintenance-baseline/v1','omc-maintenance-baseline/v2'} or baseline.get('session_id')!=sid
            or baseline.get('work_id')!=work or baseline.get('basis_sha256')!=_basis_digest(basis)
            or baseline.get('policy')!=policy):raise ValueError('maintenance_baseline_binding_mismatch')
    opref=record.get('maintenance_operation_ref')
    if not isinstance(opref,dict):raise ValueError('maintenance_operation_missing')
    for ref in [opref]:
        if not _maintenance_path(root,ref['path']).is_relative_to(root/policy['evidence_root']):
            raise ValueError('maintenance_evidence_scope_mismatch')
    operation=json.loads(_maintenance_read_ref(root,opref))
    required={'schema_version','operation_id','session_id','work_id','basis_sha256','baseline_ref',
              'native_prefix_ref','delete_call_id','postcheck_call_id'}
    recovery=state.get('maintenance_recovery')
    version='omc-maintenance-operation/v2' if recovery else 'omc-maintenance-operation/v1'
    if recovery:required|={'recovery','recovered_contract_call_id'}
    if set(operation)!=required or operation.get('schema_version')!=version:
        raise ValueError('maintenance_operation_schema_invalid')
    if not isinstance(operation['operation_id'],str) or not operation['operation_id']:
        raise ValueError('maintenance_operation_id_missing')
    if (operation['session_id']!=sid or operation['work_id']!=work or operation['basis_sha256']!=_basis_digest(basis)
            or operation['baseline_ref']!=baseline_ref):raise ValueError('maintenance_operation_binding_mismatch')
    if not _maintenance_path(root,operation['native_prefix_ref']['path']).is_relative_to(root/policy['evidence_root']):
        raise ValueError('maintenance_evidence_scope_mismatch')
    prefix=_maintenance_read_ref(root,operation['native_prefix_ref'])
    if recovery:
        if operation['recovery']!=recovery or maintenance_recovery(root,recovery['prefix_ref'],basis,baseline)!=recovery:
            raise ValueError('maintenance_recovery_binding_mismatch')
        if not prefix.startswith(_maintenance_read_ref(root,recovery['prefix_ref'])):
            raise ValueError('maintenance_recovery_prefix_mismatch')
        refs=[ref for check in record['checks'].values() for ref in check.get('evidence_files',[])]
        if recovery['prefix_ref'] not in refs:raise ValueError('maintenance_recovery_check_ref_missing')
    if not prefix.endswith(b'\n'):raise ValueError('maintenance_native_prefix_incomplete')
    if full_native is not None and not full_native.startswith(prefix):raise ValueError('maintenance_native_prefix_mismatch')
    rows=[json.loads(line) for line in prefix.decode().splitlines() if line.strip()]
    calls={};returns={};cwd=None
    for index,row in enumerate(rows):
        if row.get('type')=='system' and row.get('subtype')=='init':
            if cwd is not None and cwd!=row.get('cwd'):raise ValueError('maintenance_native_cwd_mismatch')
            cwd=row.get('cwd')
        for block in row.get('message',{}).get('content',[]):
            kind=block.get('type')
            if kind=='tool_use':
                if row.get('type')!='assistant':raise ValueError('maintenance_native_provider_unsupported')
                call_id=block.get('id')
                if not isinstance(call_id,str) or call_id in calls:raise ValueError('maintenance_native_duplicate_call')
                calls[call_id]=(index,block)
            elif kind=='tool_result':
                if row.get('type')!='user':raise ValueError('maintenance_native_provider_unsupported')
                call_id=block.get('tool_use_id')
                if call_id not in calls or call_id in returns:raise ValueError('maintenance_native_return_mismatch')
                returns[call_id]=(index,block)
    def call(call_id):
        if call_id not in calls or call_id not in returns:raise ValueError('maintenance_native_return_unknown')
        i,b=calls[call_id];j,result=returns[call_id];inp=b.get('input',{})
        if b.get('name')!='Bash' or not isinstance(inp.get('command'),str):raise ValueError('maintenance_native_provider_unsupported')
        if any(key in inp for key in ('env','shell','executable')) or inp.get('run_in_background'):
            raise ValueError('maintenance_native_environment_unsupported')
        actual_cwd=inp.get('cwd',cwd)
        if actual_cwd!=str(root) or (cwd is not None and cwd!=actual_cwd):raise ValueError('maintenance_native_cwd_mismatch')
        # shlex is not a shell parser: explicitly reject control operators and substitutions.
        command=inp['command'];args=shlex.split(command)
        if any(a in {';','&&','||','|','>','2>&1'} for a in args) or any(t in command for t in ('`','$(')):
            raise ValueError('maintenance_native_command_unsupported')
        if type(result.get('exit_code')) is not int or type(result.get('is_error')) is not bool:
            raise ValueError('maintenance_native_exit_unknown')
        if j<=i:raise ValueError('maintenance_native_order_mismatch')
        return i,j,args,result
    di,dr,delete,dresult=call(operation['delete_call_id']);pi,pr,post,presult=call(operation['postcheck_call_id'])
    paths=[str(root/name) for name in sorted(policy['allowed_paths'])]
    if (calls[operation['delete_call_id']][1]['input']['command']!=shlex.join(delete)
            or calls[operation['postcheck_call_id']][1]['input']['command']!=shlex.join(post)):
        raise ValueError('maintenance_native_command_unsupported')
    if delete!=['/bin/rm','-rf','--',*paths] or post!=['python3','-c',MAINTENANCE_POSTCHECK,*paths]:
        raise ValueError('maintenance_native_argv_mismatch')
    if not di<dr<pi<pr:raise ValueError('maintenance_native_order_mismatch')
    contracts=[]
    for call_id in calls:
        if call_id not in returns:continue
        try:
            ci,cr,args,res=call(call_id)
        except ValueError:continue
        if (len(args)>=3 and args[:3]==['python3','scripts/omc_pipeline_guard.py','contract-done']
                and calls[call_id][1]['input']['command']==shlex.join(args)
                and args[3:]==['--required-checks',','.join(c['id'] for c in basis['checks']),
                    *(['--maintenance-recovery-file',recovery['prefix_ref']['path'],'--maintenance-recovery-sha256',recovery['prefix_ref']['sha256']] if recovery else [])]
                and cr<di and res['exit_code']==0 and not res['is_error']):
            text=res.get('content','')
            if isinstance(text,str):
                for line in text.splitlines():
                    try:receipt=json.loads(line)
                    except ValueError:continue
                    expected={'status':'maintenance_baseline_registered','work_id':work,
                        'basis_sha256':_basis_digest(basis),'baseline_file':str(root/baseline_ref['path']),
                        'baseline_sha256':baseline_ref['sha256']}
                    if recovery:expected['recovery']=recovery
                    if receipt==expected:contracts.append(ci)
    if len(contracts)!=1:raise ValueError('maintenance_native_contract_mismatch')
    recovery_calls=set()
    if recovery:
        failed=recovery['failed_call_id'];fi,fr,_,_=call(failed)
        if fr>=contracts[0]:raise ValueError('maintenance_recovery_order_mismatch')
        infos=[]
        control=baseline['control']
        for ident in calls:
            if ident not in returns:continue
            try:ii,ir,args,res=call(ident)
            except ValueError:continue
            if args==['python3','scripts/omc_pipeline_guard.py','maintenance-recovery-info','--ready-file',control['ready_file']]:
                expected={'schema_version':'omc-maintenance-recovery-info/v1','launch_id':recovery['launch_id'],
                    'failed_call_id':failed,'prefix_ref':recovery['prefix_ref']}
                if (not fr<ii<ir<contracts[0] or res['exit_code']!=0 or res['is_error']
                        or json.loads(res.get('content',''))!=expected):raise ValueError('maintenance_recovery_info_mismatch')
                infos.append(ident)
        chosen=operation['recovered_contract_call_id']
        if len(infos)!=1 or chosen not in calls or calls[chosen][0]!=contracts[0]:
            raise ValueError('maintenance_recovery_info_missing')
        recovery_calls={failed,infos[0]}
    if pr!=len(rows)-1:raise ValueError('maintenance_prefix_must_end_at_postcheck')
    # Accept only successful pre-CONTRACT approval of this exact immutable basis.
    approvals={}
    for ident in calls:
        if ident not in returns:continue
        try:ai,ar,args,res=call(ident)
        except ValueError:continue
        if (len(args)!=5 or args[:4]!=['python3','scripts/omc_pipeline_guard.py','approve-checks','--file']
                or ar>=contracts[0] or res['exit_code']!=0 or res['is_error']
                or calls[ident][1]['input']['command']!=shlex.join(args)):
            continue
        proposal=_maintenance_path(root,args[4])
        # Policy files belong to the internal records, never arbitrary source paths.
        if not proposal.is_relative_to(root/'.omc'):raise ValueError('maintenance_approval_scope_mismatch')
        if json.loads(_transfer_read(proposal))!=basis:raise ValueError('maintenance_approval_basis_mismatch')
        expected=f"[PIPELINE] 사용자 승인 기준 기록: 개정 {basis['revision']} / {_basis_digest(basis)}"
        if not isinstance(res.get('content'),str) or res['content'].strip()!=expected:
            raise ValueError('maintenance_approval_return_mismatch')
        approvals[ident]=(ai,proposal)
    from omc_native_command import read_command_reason
    for ident,(index,block) in calls.items():
        if ident in approvals or ident in recovery_calls:continue
        if block.get('name')=='Write' and ident in returns:
            inp=block.get('input',{});j,result=returns[ident]
            if (index<j<contracts[0] and result.get('is_error') is False
                    and isinstance(inp.get('file_path'),str) and isinstance(inp.get('content'),str)):
                path=_maintenance_path(root,inp['file_path'])
                if any(path==proposal and j<ai for ai,proposal in approvals.values()):
                    if json.loads(inp['content'])==basis:continue
            raise ValueError('maintenance_native_operation_unsupported')
        if ident in (operation['delete_call_id'],operation['postcheck_call_id']) or index==contracts[0]:continue
        if block.get('name') in {'Read','Glob','Grep'}:continue
        if block.get('name')=='Bash' and read_command_reason(root,block.get('input',{}).get('command','')) is None:continue
        raise ValueError('maintenance_native_operation_unsupported')
    if dresult['exit_code']!=0 or dresult['is_error']:raise ValueError('FAILED_OR_PARTIAL')
    if presult['exit_code']!=0 or presult['is_error']:raise ValueError('UNKNOWN_POST_STATE')
    if baseline['snapshot']!=_maintenance_preserved_snapshot(root,policy,baseline,record,operation):
        raise ValueError('maintenance_preservation_changed')
    inventory=baseline['inventory'];later=any((root/name).exists() for name in policy['allowed_paths'])
    return {'state':'LATER_REGENERATION' if later else 'VERIFIED_REMOVAL' if inventory else 'VERIFIED_NO_OP',
            'removed_files':len(inventory),'candidate_allocated_bytes':sum(v['allocated_bytes'] for v in inventory.values()),
            'contract_index':contracts[0],'delete_index':di,'postcheck_index':pi,'postcheck_return_index':pr}


def evaluate_maintenance_completion(root,verification_file,expected_session_id=None):
    try:
        state,sid,basis,work=_maintenance_context(root)
        if expected_session_id not in (None,sid):raise ValueError('maintenance_session_mismatch')
        record=json.loads(_transfer_read(_internal_verification_path(root,verification_file)))
        if record.get('session_id')!=sid:raise ValueError('maintenance_verification_session_mismatch')
        problems=approved_verification_problems(root,sid,state,record,verification_file)
        if problems:
            statuses=[row.get('status') for row in record.get('checks',{}).values() if isinstance(row,dict)]
            status='failed' if 'failed' in statuses else 'mismatch' if 'mismatch' in statuses or any('불일치' in p for p in problems) else 'unrun'
            result=_transfer_block('; '.join(problems),status)
            result['completion_kind']=MAINTENANCE_KIND
            return result
        for check in basis['checks']:
            refs=record['checks'][check['id']].get('evidence_files')
            if not isinstance(refs,list) or not refs:raise ValueError('maintenance_check_file_evidence_missing')
            for ref in refs:_maintenance_read_ref(root,ref)
        operation=maintenance_operation(root,record)
        end,owner,end_basis,end_work=_maintenance_context(root)
        if (owner!=sid or end_work!=work or _basis_digest(end_basis)!=_basis_digest(basis)
                or end.get('maintenance_baseline_ref')!=state.get('maintenance_baseline_ref')):
            raise ValueError('maintenance_changed_during_validation')
        return {'outcome':'done','first':'필수 검증 통과·작업 완료','registered':True,'problems':[],
                'completion_kind':MAINTENANCE_KIND,'maintenance':operation}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,subprocess.SubprocessError) as exc:
        result=_transfer_block(str(exc),'failed' if str(exc)=='FAILED_OR_PARTIAL' else 'unrun')
        result['completion_kind']=MAINTENANCE_KIND
        return result





def _completion_kind(basis: dict) -> str:
    kind = basis.get('completion_kind', 'implementation')
    if kind not in ('implementation', TRANSFER_KIND, MAINTENANCE_KIND):
        raise ValueError('completion_kind_invalid')
    return kind


def _validate_transfer_policy(value: dict) -> dict:
    policy = value.get('transfer')
    keys = {'source_root','target_root','base_commit','target_commit','expected_paths',
            'source_session_id','source_verification','protected_paths','allow_review_pointer_update'}
    if not isinstance(policy, dict) or set(policy) != keys:
        raise ValueError('transfer_policy_invalid')
    for key in ['source_root','target_root','source_session_id','source_verification']:
        if not isinstance(policy[key], str) or not policy[key].strip():
            raise ValueError('transfer_policy_invalid:'+key)
    for key in ['source_root','target_root']:
        if not Path(policy[key]).is_absolute():
            raise ValueError('transfer_root_must_be_absolute')
    for key in ['base_commit','target_commit']:
        if not isinstance(policy[key],str) or not re.fullmatch('[0-9a-f]{40}',policy[key]):
            raise ValueError('transfer_commit_id_invalid')
    if policy['base_commit']==policy['target_commit']:
        raise ValueError('transfer_commit_must_change')
    paths=policy['expected_paths']
    if (not isinstance(paths,list) or not paths or len(paths)!=len(set(paths))
            or any(not isinstance(p,str) or not p or Path(p).is_absolute()
                   or '..' in Path(p).parts or p.startswith(('.git/','.omc/')) for p in paths)):
        raise ValueError('transfer_expected_paths_invalid')
    protected=policy['protected_paths']
    if not isinstance(protected,list) or any(not isinstance(p,str) or not Path(p).is_absolute() for p in protected):
        raise ValueError('transfer_protected_paths_invalid')
    if type(policy['allow_review_pointer_update']) is not bool:
        raise ValueError('transfer_pointer_policy_invalid')
    return policy


def _transfer_read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError('transfer_evidence_missing_or_linked:'+str(path))
    with path.open('rb') as stream:
        raw=stream.read(EVIDENCE_READ_LIMIT+1)
    if len(raw)>EVIDENCE_READ_LIMIT:
        raise ValueError('transfer_evidence_size_unsupported')
    return raw


def _transfer_local(root: Path, name: str) -> Path:
    path=root/name
    if not path.resolve().is_relative_to(root):
        raise ValueError('transfer_evidence_outside_root')
    for part in (path,*path.parents):
        if part==root:break
        if part.is_symlink():raise ValueError('transfer_evidence_linked')
    _transfer_read(path)
    return path


def _transfer_git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git','--no-optional-locks',*args],cwd=root,
                                   env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'},stderr=subprocess.PIPE)


def _transfer_identity(root: Path) -> dict:
    root=root.resolve(strict=True)
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():
        raise ValueError('transfer_independent_git_required')
    common=Path(_transfer_git(root,'rev-parse','--path-format=absolute','--git-common-dir').decode().strip()).resolve()
    index=Path(_transfer_git(root,'rev-parse','--path-format=absolute','--git-path','index').decode().strip()).resolve()
    if common!=root/'.git' or index!=root/'.git/index':
        raise ValueError('transfer_git_identity_unsupported')
    if list(common.glob('sharedindex.*')):
        raise ValueError('transfer_split_index_unsupported')
    return {'root':str(root),'common':str(common),'index':str(index)}


def _transfer_index(root: Path) -> dict:
    entries={}
    for row in _transfer_git(root,'ls-files','--stage','-z').split(b'\0'):
        if not row:continue
        header,path=row.split(b'\t',1);mode,oid,stage=header.decode().split()
        if stage!='0' or mode=='040000':raise ValueError('transfer_index_unsupported')
        entries[os.fsdecode(path)]={'mode':mode,'oid':oid,'stage':stage}
    raw=_transfer_git(root,'ls-files','--debug','-z');pos=0
    while pos<len(raw):
        end=raw.index(b'\0',pos);name=os.fsdecode(raw[pos:end])
        match=re.match(br'  ctime: [^\n]*\n  mtime: [^\n]*\n  dev: [^\n]*\n  uid: [^\n]*\n  size: [^\n]*\tflags: ([0-9a-fA-F]+)\n',raw[end+1:])
        if match is None:raise ValueError('transfer_index_flags_unsupported')
        entries[name]['flags']=int(match.group(1),16);pos=end+1+match.end()
    return entries


def _transfer_file_row(path: Path) -> dict:
    data=_transfer_read(path)
    return {'kind':'file','sha256':hashlib.sha256(data).hexdigest(),'mode':path.stat().st_mode&0o777,'size':len(data)}


def _transfer_inventory(root: Path) -> dict:
    rows={};total=0
    def failed_walk(error):raise error
    for folder,dirs,files in os.walk(root,followlinks=False,onerror=failed_walk):
        rows[str(Path(folder).relative_to(root))]={'kind':'directory','mode':Path(folder).stat().st_mode&0o777}
        dirs[:]=[d for d in dirs if not (Path(folder)==root and d=='.git')]
        for name in dirs:
            if (Path(folder)/name).is_symlink():raise ValueError('transfer_directory_link_unsupported')
        for name in files:
            path=Path(folder)/name;row=_transfer_file_row(path);total+=row['size']
            if len(rows)>=20000 or total>256*1024*1024:raise ValueError('transfer_inventory_budget_exceeded')
            rows[str(path.relative_to(root))]=row
    return rows


def _transfer_review(root: Path, policy: dict, *, preserved_target: bool = False) -> dict:
    # Import only inside the explicitly registered transfer path.
    from omc_review_snapshot import (validate_ship_candidate, load_review_receipt,
                                     _current_receipt, load_peer_snapshot, build_commit_candidate)
    if preserved_target:
        # Generic ship deliberately rejects unrelated staged work. This route
        # binds the immutable reviewed commit; the caller checks every preserved
        # index entry, flag, working byte and mode against the pre-transfer record.
        receipt=_current_receipt(root)
        snapshot=load_peer_snapshot(root/'.omc/state/review-snapshots'/receipt['review_snapshot_name'],
                                    expected_sha256=receipt['review_snapshot_sha256'])
        if (_transfer_git(root,'rev-parse','HEAD').decode().strip()!=policy['target_commit']
                or snapshot.get('candidate_commit')!=policy['target_commit']
                or build_commit_candidate(root,base_commit=policy['base_commit'],
                    candidate_commit=policy['target_commit'])['candidate_scope_sha256']!=receipt['candidate_scope_sha256']):
            raise ValueError('transfer_review_commit_mismatch')
        ready={'status':'READY','receipt_sha256':receipt['receipt_sha256'],
               'claim_boundary':'TRANSFER_COMMIT_ONLY'}
    else:
        ready=validate_ship_candidate(root)
        if ready.get('status')!='READY':raise ValueError('transfer_review_not_ready')
    pointer=_transfer_local(root,'.omc/state/review-snapshots/current.json')
    obj=json.loads(_transfer_read(pointer));name=obj['receipt_name']
    if not isinstance(name,str) or Path(name).name!=name:raise ValueError('transfer_receipt_name_invalid')
    path=pointer.parent/name;receipt=load_review_receipt(root,path)
    if (receipt['base_commit']!=policy['base_commit']
            or sorted(x['path'] for x in receipt['candidate_scope'])!=sorted(policy['expected_paths'])):
        raise ValueError('transfer_review_scope_mismatch')
    return {'ready':ready,'receipt':receipt,'pointer':str(pointer),'receipt_path':str(path)}


def _transfer_tree_matches(root: Path,commit: str) -> None:
    for row in _transfer_git(root,'ls-tree','-r','-z',commit).split(b'\0'):
        if not row:continue
        header,name=row.split(b'\t',1);mode,kind,oid=header.decode().split()
        if kind!='blob' or mode not in ('100644','100755'):raise ValueError('transfer_source_tree_unsupported')
        path=_transfer_local(root,os.fsdecode(name));data=_transfer_read(path)
        if (hashlib.sha1(('blob '+str(len(data))+'\0').encode()+data).hexdigest()!=oid
                or path.stat().st_mode&0o777!=(0o755 if mode=='100755' else 0o644)):
            raise ValueError('transfer_source_tree_mismatch')


def _transfer_source_proof(policy: dict) -> dict:
    source=Path(policy['source_root']).resolve(strict=True);identity=_transfer_identity(source)
    if _transfer_git(source,'rev-parse','HEAD').decode().strip()!=policy['target_commit']:
        raise ValueError('transfer_source_commit_mismatch')
    if _transfer_git(source,'status','--porcelain','--untracked-files=all').strip():
        raise ValueError('transfer_source_code_changed')
    _transfer_tree_matches(source,policy['target_commit'])
    sid=policy['source_session_id'];basis=_load_approved_basis(source,sid)
    session_file=_transfer_local(source,f'.omc/state/sessions/{sid}/session.json')
    session=json.loads(_transfer_read(session_file))
    if (session.get('session_id')!=sid or not session.get('work_id')
            or session.get('confirmation',{}).get('status')!='confirmed'):
        raise ValueError('transfer_source_work_invalid')
    if _completion_kind(basis)!='implementation':raise ValueError('transfer_source_must_be_implementation')
    verification=_transfer_local(source,policy['source_verification']);raw=_transfer_read(verification)
    state,expiration=_read_pipeline(source)
    if expiration is not None:raise ValueError('transfer_source_verification_expired')
    assessment=_evaluate_implementation_completion(source,verification,expected_session_id=sid)
    if assessment['outcome']!='done' or not assessment['registered']:
        raise ValueError('transfer_source_verification_not_passed')
    review=_transfer_review(source,policy);record=json.loads(raw)
    files={str(verification):_transfer_file_row(verification),str(session_file):_transfer_file_row(session_file)}
    for item in record['checks'].values():
        path=_transfer_local(source,item['evidence']);row=_transfer_file_row(path)
        if item.get('evidence_sha256')!=row['sha256']:
            raise ValueError('transfer_source_evidence_digest_missing_or_mismatch')
        files[str(path)]=row
    files[str(_basis_path(source,sid)/f"{basis['revision']}.json")]=_transfer_file_row(_basis_path(source,sid)/f"{basis['revision']}.json")
    for receipt in state.get('red_evidence',{}).values():
        path=_transfer_local(source,receipt['file']);files[str(path)]=_transfer_file_row(path)
        proof=json.loads(_transfer_read(path));output=_transfer_local(source,proof['output_file']);files[str(output)]=_transfer_file_row(output)
    prefix=source/'.omc/state/review-snapshots'
    for path in [Path(review['pointer']),Path(review['receipt_path']),
                 prefix/review['receipt']['review_snapshot_name'],prefix/review['receipt']['review_evidence_name']]:
        files[str(path)]=_transfer_file_row(path)
    if _transfer_read(verification)!=raw or _transfer_git(source,'status','--porcelain','--untracked-files=all').strip():
        raise ValueError('transfer_source_changed_during_capture')
    return {'identity':identity,'session_id':sid,'work_id':session['work_id'],'basis_sha256':_basis_digest(basis),
            'code_snapshot_sha256':_code_snapshot_digest(source,verification),'files':files,
            'review_receipt_sha256':review['ready']['receipt_sha256'],'captured_pass':True}


def _transfer_context(root: Path):
    state,expiration=_read_pipeline(root)
    if expiration is not None:raise ValueError('pipeline_ttl_expired')
    latest=json.loads((root/'.omc/state/latest.json').read_text());sid=_completion_owner(root,latest,state)
    basis=_load_approved_basis(root,sid)
    if _completion_kind(basis)!=TRANSFER_KIND:raise ValueError('completion_kind_entry_mismatch')
    session=json.loads((root/'.omc/state/sessions'/sid/'session.json').read_text())
    if (session.get('session_id')!=sid or session.get('confirmation',{}).get('status')!='confirmed'
            or not isinstance(session.get('work_id'),str) or not session['work_id']):
        raise ValueError('transfer_work_binding_invalid')
    if (not state.get('contract_confirmed') or state.get('session_id')!=sid
            or state.get('basis_sha256')!=_basis_digest(basis)):
        raise ValueError('transfer_contract_binding_invalid')
    if state.get('transfer_operator_snapshot')!=_code_snapshot_digest(root):
        raise ValueError('transfer_operator_code_changed')
    return state,sid,basis,session['work_id']


def _transfer_baseline_path(root: Path,sid: str) -> Path:
    path=root/'.omc/state/transfer-baselines'/f'{sid}.json'
    for part in (path,*path.parents):
        if part==root:break
        if part.is_symlink():raise ValueError('transfer_baseline_linked')
    return path


def cmd_register_transfer_baseline(root: Path) -> int:
    try:
        state,sid,basis,work=_transfer_context(root);policy=_validate_transfer_policy(basis)
        target=Path(policy['target_root']).resolve(strict=True);source=Path(policy['source_root']).resolve(strict=True)
        if any(a==b or a.is_relative_to(b) or b.is_relative_to(a)
               for a,b in [(target,source),(target,root),(source,root)]):
            raise ValueError('transfer_roots_not_independent')
        identity=_transfer_identity(target);head=_transfer_git(target,'rev-parse','HEAD').decode().strip()
        if head==policy['target_commit']:raise ValueError('transfer_already_applied')
        if head!=policy['base_commit']:raise ValueError('transfer_baseline_head_mismatch')
        parents=_transfer_git(target,'rev-list','--parents','-n','1',policy['target_commit']).decode().split()
        if parents!=[policy['target_commit'],policy['base_commit']]:raise ValueError('transfer_parent_mismatch')
        paths=_transfer_git(target,'diff-tree','--no-commit-id','--name-only','-r',policy['target_commit']).decode().splitlines()
        if sorted(paths)!=sorted(policy['expected_paths']):raise ValueError('transfer_commit_scope_mismatch')
        path=_transfer_baseline_path(root,sid)
        if path.exists():raise ValueError('transfer_baseline_write_once')
        index_path=Path(identity['index']);index_raw=_transfer_read(index_path);index=_transfer_index(target)
        before=_transfer_inventory(target)
        # Unrelated pre-existing staging/worktree changes are captured unchanged.
        # Only paths that this commit will overwrite must match the base tree.
        base_tree={}
        for row in _transfer_git(target,'ls-tree','-r','-z',policy['base_commit']).split(b'\0'):
            if not row:continue
            header,name=row.split(b'\t',1);mode,kind,oid=header.decode().split()
            base_tree[os.fsdecode(name)]={'mode':mode,'oid':oid,'stage':'0'}
        for name in policy['expected_paths']:
            entry=base_tree.get(name)
            if entry is None:
                if name in index or name in before:raise ValueError('transfer_scope_conflict:'+name)
            else:
                if (name not in index or any(index[name][k]!=v for k,v in entry.items())
                        or entry['mode'] not in ('100644','100755')):
                    raise ValueError('transfer_scope_index_conflict:'+name)
                data=_transfer_read(target/name)
                if (hashlib.sha1(('blob '+str(len(data))+'\0').encode()+data).hexdigest()!=entry['oid']
                        or before[name]['mode']!=(0o755 if entry['mode']=='100755' else 0o644)):
                    raise ValueError('transfer_scope_worktree_conflict:'+name)
        pointer=target/'.omc/state/review-snapshots/current.json'
        pointer_raw=_transfer_read(pointer) if pointer.exists() else None
        if pointer_raw is not None and hashlib.sha256(pointer_raw).hexdigest()!=before[str(pointer.relative_to(target))]['sha256']:
            raise ValueError('transfer_pointer_changed_during_capture')
        protected={str(Path(p).resolve()):_transfer_file_row(Path(p)) for p in policy['protected_paths']}
        for protected_path in protected:
            pp=Path(protected_path)
            if pp==index_path or (pp==pointer and policy['allow_review_pointer_update']) or (pp.is_relative_to(target) and str(pp.relative_to(target)) in policy['expected_paths']):
                raise ValueError('transfer_protection_policy_conflict')
        proof=_transfer_source_proof(policy)
        # No split-time baseline: register only if the observed target is stable.
        if (_transfer_git(target,'rev-parse','HEAD').decode().strip()!=head
                or _transfer_read(index_path)!=index_raw or _transfer_index(target)!=index
                or _transfer_inventory(target)!=before
                or any(_transfer_file_row(Path(p))!=row for p,row in protected.items())):
            raise ValueError('transfer_changed_during_registration')
        current,owner,new_basis,new_work=_transfer_context(root)
        if owner!=sid or new_work!=work or _basis_digest(new_basis)!=_basis_digest(basis):
            raise ValueError('transfer_approval_changed_during_registration')
        value={'schema_version':'omc-transfer-baseline/v1','session_id':sid,'work_id':work,
               'basis_sha256':_basis_digest(basis),'policy':policy,'identity':identity,'index':index,
               'index_raw_hex':index_raw.hex(),'files':before,'protected':protected,'source_proof':proof,
               'old_pointer_hex':None,'registered_at':time.time()}
        if pointer_raw is not None:value['old_pointer_hex']=pointer_raw.hex()
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
        current['transfer_baseline_sha256']=_basis_digest(value);_save_state(root,current)
        print(json.dumps({'status':'registered','baseline_file':str(path),'baseline_sha256':_basis_digest(value)},ensure_ascii=False))
        return 0
    except (OSError,ValueError,TypeError,KeyError,AttributeError,subprocess.SubprocessError) as exc:
        print('[PIPELINE] '+str(exc));return 1


def _transfer_current_check(baseline: dict,policy: dict):
    target=Path(policy['target_root']).resolve(strict=True)
    if _transfer_identity(target)!=baseline['identity']:raise ValueError('transfer_target_identity_mismatch')
    if _transfer_git(target,'rev-parse','HEAD').decode().strip()!=policy['target_commit']:
        raise ValueError('transfer_target_head_mismatch')
    before=baseline['files'];after=_transfer_inventory(target);expected=set(policy['expected_paths'])
    tree={}
    for row in _transfer_git(target,'ls-tree','-r','-z',policy['target_commit']).split(b'\0'):
        if not row:continue
        head,path=row.split(b'\t',1);mode,kind,oid=head.decode().split();tree[os.fsdecode(path)]={'mode':mode,'oid':oid,'stage':'0'}
    current=_transfer_index(target);old=baseline['index']
    for name in set(old)|set(current):
        if name in expected:
            want=tree.get(name)
            if want is None:
                if name in current:raise ValueError('transfer_index_deleted_entry_present')
            elif (name not in current or any(current[name][k]!=v for k,v in want.items())
                  or current[name]['flags']!=old.get(name,{'flags':0})['flags']):
                raise ValueError('transfer_index_expected_entry_mismatch')
        elif current.get(name)!=old.get(name):raise ValueError('transfer_index_preservation_mismatch')
    pointer='.omc/state/review-snapshots/current.json'
    review=_transfer_review(target,policy,preserved_target=True)
    required_parents={str(parent) for name in expected if name in tree
                      for parent in Path(name).parents if str(parent)!='.'}
    deleted_paths={name for name in expected if name in old and name not in tree}
    deletion_parents={str(parent) for name in deleted_paths
                      for parent in Path(name).parents if str(parent)!='.'}
    removable_parents=set(deletion_parents)
    # Git can remove parents emptied by an approved deletion. A pre-existing
    # unrelated file or even an empty directory prevents removing its ancestors.
    for name,row in before.items():
        if name in deleted_paths or (name in deletion_parents and row.get('kind')=='directory'):
            continue
        removable_parents.difference_update(str(parent) for parent in Path(name).parents
                                            if str(parent)!='.')
    for name in set(before)|set(after):
        if name in expected:
            if name not in tree:
                if name in after:raise ValueError('transfer_worktree_delete_mismatch')
            else:
                data=_transfer_read(target/name);row=after.get(name)
                if (row is None or hashlib.sha1(('blob '+str(len(data))+'\0').encode()+data).hexdigest()!=tree[name]['oid']
                        or row['mode']!=(0o755 if tree[name]['mode']=='100755' else 0o644)):
                    raise ValueError('transfer_worktree_blob_mode_mismatch')
        elif name==pointer and policy['allow_review_pointer_update']:
            if name not in after or (name in before and after[name]['mode']!=before[name]['mode']):
                raise ValueError('transfer_pointer_preservation_mismatch')
        elif name in before:
            if after.get(name)!=before[name]:
                if (name not in removable_parents or name in after
                        or before[name].get('kind')!='directory'):
                    raise ValueError('transfer_file_preservation_mismatch:'+name)
        elif name in required_parents and after[name].get('kind')=='directory':
            pass
        elif after[name].get('kind')=='directory' and name in {'.omc/state','.omc/state/review-snapshots','.omc/state/review-snapshots/peer-inputs','.omc/state/review-snapshots/review-evidence'}:
            pass
        elif (not name.startswith('.omc/state/review-snapshots/') or not name.endswith('.json')
              or Path(name).name=='current.json'):
            raise ValueError('transfer_unapproved_added_path:'+name)
    for p,row in baseline['protected'].items():
        if _transfer_file_row(Path(p))!=row:raise ValueError('transfer_protected_file_changed')
    proof=baseline['source_proof'];source=Path(policy['source_root']).resolve(strict=True)
    if (_transfer_identity(source)!=proof['identity'] or _transfer_git(source,'rev-parse','HEAD').decode().strip()!=policy['target_commit']
            or _transfer_git(source,'status','--porcelain','--untracked-files=all').strip()):
        raise ValueError('transfer_source_changed')
    _transfer_tree_matches(source,policy['target_commit'])
    if _code_snapshot_digest(source,source/policy['source_verification'])!=proof['code_snapshot_sha256']:
        raise ValueError('transfer_source_snapshot_changed')
    for p,row in proof['files'].items():
        if _transfer_file_row(Path(p))!=row:raise ValueError('transfer_source_proof_changed')
    return review


def _transfer_block(reason: str, status: str='unrun') -> dict:
    mismatch=status=='mismatch' or any(token in reason for token in ('mismatch','changed','stale'))
    first='검증 실패·수정 필요' if status=='failed' else '증거 불일치·재검증 필요' if mismatch else '구현됨·검증 미완료'
    return {'outcome':'blocked','first':first,'registered':False,
            'problems':[reason],'reason_code':'verification_incomplete','completion_kind':TRANSFER_KIND}


def evaluate_transfer_completion(root: Path,verification_file: Path,expected_session_id: str|None=None) -> dict:
    try:
        state,sid,basis,work=_transfer_context(root)
        if expected_session_id is not None and expected_session_id!=sid:raise ValueError('transfer_session_mismatch')
        policy=_validate_transfer_policy(basis);path=_transfer_baseline_path(root,sid)
        baseline=json.loads(_transfer_read(path))
        if (baseline.get('schema_version')!='omc-transfer-baseline/v1' or baseline.get('session_id')!=sid
                or baseline.get('work_id')!=work or baseline.get('basis_sha256')!=_basis_digest(basis)
                or baseline.get('policy')!=policy or state.get('transfer_baseline_sha256')!=_basis_digest(baseline)):
            raise ValueError('transfer_baseline_binding_mismatch')
        verification_file=_internal_verification_path(root,verification_file);record=json.loads(_transfer_read(verification_file))
        if record.get('session_id')!=sid:raise ValueError('transfer_verification_session_mismatch')
        problems=approved_verification_problems(root,sid,state,record,verification_file)
        if problems:
            statuses=[row.get('status') for row in record.get('checks',{}).values() if isinstance(row,dict)]
            return _transfer_block('; '.join(problems),'failed' if 'failed' in statuses else 'mismatch' if 'mismatch' in statuses else 'unrun')
        review=_transfer_current_check(baseline,policy)
        # Recheck after all evidence reads; validation cannot renew the work TTL.
        end,owner,end_basis,end_work=_transfer_context(root)
        if (owner!=sid or end_work!=work or _basis_digest(end_basis)!=_basis_digest(basis)
                or end.get('transfer_baseline_sha256')!=_basis_digest(baseline)):
            raise ValueError('transfer_changed_during_validation')
        return {'outcome':'done','first':'필수 검증 통과·작업 완료','registered':True,'problems':[],
                'completion_kind':TRANSFER_KIND,'transfer_review':review['ready']}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,subprocess.SubprocessError) as exc:
        return _transfer_block(str(exc))


def evaluate_task_completion(root: Path,verification_file: Path,expected_session_id: str|None=None) -> dict:
    # Legacy entry does no transfer capture, import or write.
    try:
        state,_=_read_pipeline(root);latest=json.loads((root/'.omc/state/latest.json').read_text())
        sid=_completion_owner(root,latest,state);basis=_load_approved_basis(root,sid)
        if _completion_kind(basis)==MAINTENANCE_KIND:
            return evaluate_maintenance_completion(root,verification_file,expected_session_id)
        if _completion_kind(basis)==TRANSFER_KIND:
            return evaluate_transfer_completion(root,verification_file,expected_session_id)
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        pass  # Existing implementation evaluator reports its original failures.
    return _evaluate_implementation_completion(root,verification_file,expected_session_id)


def completion_command(root: Path,sid: str) -> str:
    return {'implementation':'complete-task',TRANSFER_KIND:'complete-transfer',MAINTENANCE_KIND:'complete-maintenance'}[_completion_kind(_load_approved_basis(root,sid))]


def cmd_complete_transfer(root: Path,verification_file: Path) -> int:
    return _print_completion(evaluate_transfer_completion(root,verification_file))



def _evaluate_implementation_completion(root: Path, verification_file: Path, expected_session_id: str | None = None) -> dict:
    """Same shared completion decision for CLI and collectors, including resume."""
    from omc_edit_resume import ENV, load
    try:
        state, expiration = _read_pipeline(root)
        if expiration is not None and not _valid_completion_reapproval(root, state, verification_file):
            return _expired_completion_evaluation(root, verification_file, state, expiration, expected_session_id)
        evaluation = _evaluate_completion_checks(root, verification_file, state, expected_session_id)
        if os.environ.get(ENV):
            load(root)
        if (expiration is not None and evaluation['outcome'] == 'done'
                and not _valid_completion_reapproval(root, state, verification_file)):
            return _expired_completion_evaluation(root, verification_file, state, expiration, expected_session_id)
        return evaluation
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        return {'outcome':'blocked', 'first':'구현 여부 확인 불가·검증 미완료',
                'problems':['edit_resume_blocked: '+str(exc)], 'reason_code':'verification_incomplete',
                'registered':None, 'test_assessment':None, 'visual_assessment':None, 'home_assessment':None}



def _evaluate_completion_checks(root: Path, verification_file: Path, state: dict,
                                expected_session_id: str | None = None) -> dict:
    problems = []
    failed = False
    mismatch = False
    try:
        latest = json.loads((root / ".omc/state/latest.json").read_text(encoding="utf-8"))
        if not isinstance(latest, dict):
            raise ValueError("session object required")
        sid = _completion_owner(root, latest, state)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        sid = ""
        problems.append("완료 대상 작업의 세션 연결을 확인할 수 없습니다: " + str(exc))
        if str(exc) == "completion_owner_role_invalid":
            problems.append("완료 재개 역할 불일치: senior_coding + preserve는 완료 증거를 이어받을 수 없습니다. "
                            "코드 변경 없는 완료 재개는 비구현 역할과 명시적 preserve로 기존 작업 연결을 확인하세요. "
                            "승인·RED를 복사하거나 재등록하지 마세요")
    # No owner means there is no current contract to evaluate.  In particular,
    # a completed previous pipeline must not supply checks for a new note task.
    if not sid:
        if not problems:
            problems.append("완료 대상 작업의 세션 연결을 확인할 수 없습니다: confirmed session 불일치")
        return {"outcome": "blocked", "first": "구현 여부 확인 불가·검증 미완료",
                "problems": problems, "reason_code": "verification_incomplete",
                "registered": False, "test_assessment": None,
                "visual_assessment": None, "home_assessment": None}
    if (state.get("contract_reset_reason") == "session_start"
            and not state.get("contract_confirmed") and not state.get("session_id")):
        problems.append("시작 훅에서 현재 CONTRACT 연결이 초기화됐습니다. 원본 증거 소실을 뜻하지 않습니다. "
                        "승인된 인계 유무와 작업 연결을 확인하세요; 자동 복원하지 않습니다")
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
    test_assessment = None
    try:
        basis = _load_approved_basis(root, sid)
        if 'validation_commands_policy_sha256' in basis:
            from omc_validation_command import assess_completion_tests
            test_assessment = assess_completion_tests(root, basis, record, sid)
        if test_assessment and test_assessment['status'] != 'passed':
            problems.append('tests: ' + str(test_assessment['reason']))
            failed = failed or test_assessment['status'] == 'failed'
            mismatch = mismatch or test_assessment['status'] == 'mismatch'
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError) as exc:
        problems.append('tests: ' + str(exc))
    visual_assessment = None
    try:
        from omc_visual_check import assess_completion_visual
        visual_assessment = assess_completion_visual(root, _load_approved_basis(root, sid), record)
        if visual_assessment and visual_assessment['status'] != 'passed':
            problems.append('visual: ' + str(visual_assessment['reason']))
            failed = failed or visual_assessment['status'] == 'failed'
            mismatch = mismatch or visual_assessment['status'] == 'mismatch'
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        problems.append('visual: ' + str(exc))
    home_assessment = None
    try:
        from omc_home_observation import assess_completion_home
        home_assessment = assess_completion_home(root, _load_approved_basis(root, sid),
                                                 submission=record.get('home_motion_result'))
        if home_assessment and home_assessment['status'] != 'passed':
            problems.append('home: ' + str(home_assessment['reason']))
            mismatch = mismatch or home_assessment['status'] == 'mismatch'
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        problems.append('home: ' + str(exc))
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
            "problems": problems, "registered": registered, "test_assessment": test_assessment,
            "visual_assessment": visual_assessment, "home_assessment": home_assessment}


def cmd_complete_task(root: Path, verification_file: Path) -> int:
    try:
        latest=json.loads((root/'.omc/state/latest.json').read_text())
        state,_=_read_pipeline(root);sid=_completion_owner(root,latest,state)
        if _completion_kind(_load_approved_basis(root,sid))!='implementation':
            return _print_completion(_transfer_block('completion_kind_entry_mismatch'))
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        pass
    return _print_completion(_evaluate_implementation_completion(root, verification_file))


def _print_completion(evaluation: dict, output_context=None, *, root=None, verification_file=None) -> int:
    if output_context is not None:
        import contextlib, io
        from omc_output_contract import emit_record, _GATE_AUTHORITY
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer):
            _print_completion(evaluation)
        return emit_record(root, output_context[0], output_context[1], buffer.getvalue(),
                           source_kind="completion_gate", _authority=_GATE_AUTHORITY, _gate_verification=verification_file)
    blocked = evaluation["outcome"] == "blocked"
    print(evaluation["first"])
    if evaluation.get('completion_kind')==MAINTENANCE_KIND and not blocked:
        op=evaluation['maintenance']
        print(f"캐시 정리 범위 검증: {op['state']} / 제거 {op['removed_files']}개. 기존 변경의 구현·출하 승인은 별개입니다.")
    for problem in evaluation["problems"]:
        print(f"- {problem}")
    if evaluation.get("reason_code") == "pipeline_ttl_expired":
        print("다음 행동: 원문과 코드가 일치하는 경우에만 reapprove-completion의 명시적 재승인을 검토합니다. 자동 발급·재실행은 하지 않습니다.")
    else:
        print("다음 행동: 미충족 항목을 확인한 뒤 다시 검증합니다." if blocked
              else "다음 행동: omc-review로 변경사항을 검토합니다.")
    envelope = {"schema_version": "omc-output/v1", "stage": "task",
                "outcome": "blocked" if blocked else "done", "risk": "medium" if blocked else "low",
                "next_skill": None if blocked else "omc-review", "user_selection_needed": blocked,
                "reason_code": evaluation.get("reason_code", "verification_incomplete") if blocked else None}
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
    # Public edit CLI must not bypass opt-in native RED for existing files.
    if (root / '.omc/state/latest.json').is_file():
        return cmd_check(root, file_path, bypass=bypass)

    if bypass:
        return 0  # autopilot 모드 → 통과

    if not _is_impl_file(file_path):
        return 0  # 구현 파일 아님 → 통과

    state, expiration = _read_pipeline(root)
    if expiration is not None:
        print("[PIPELINE] pipeline_ttl_expired: 승인 유효기간이 만료됐습니다. 원본 기록을 유지합니다.")
        return 1

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
    state, _ = _read_pipeline(root)
    path = _state_path(root)
    if path.exists():
        _archive_pipeline(root, path.read_bytes())
    cleared = _empty_state()
    cleared["started_at"] = state.get("started_at", 0)
    # Reset revokes flags, but is neither an independent work nor a TTL renewal.
    cleared["owner_session_id"] = state.get("owner_session_id") or state.get("session_id")
    if state.get("owner_work_id"):
        cleared["owner_work_id"] = state["owner_work_id"]
    _save_state(root, cleared)
    print("[PIPELINE] 세션 상태 초기화 완료")
    return 0

SESSION_START_HANDOFF_ENV = 'OMC_SESSION_START_HANDOFF'
SESSION_START_INPUT_ENV = 'OMC_SESSION_START_INPUT'
_START_HANDOFF_TTL_SECONDS = 300


def _session_start_binding(root: Path, expected_context: dict) -> dict:
    """Validate current approval and work before preserving a process handoff.

    This is local custody, not authentication against a hostile local writer.
    Never restore a CONTRACT from the descriptor: it must still be valid now.
    """
    from omc_home_observation import local
    from omc_validation_command import load_policy, prebound_task_context
    from omc_state import _pending_completion_matches_session
    policy, pin = load_policy(root)
    context = prebound_task_context(root, policy, pin)
    if context != expected_context or not context.get('work_id'):
        raise ValueError('session_start_work_mismatch')
    state = _load_state(root)
    if (not state.get('contract_hash')
            or set(state.get('required_checks', [])) !=
            {c['id'] for c in _load_approved_basis(root, context['session_id'])['checks']}):
        raise ValueError('session_start_contract_invalid')
    session_path = local(root, '.omc/state/sessions/'+context['session_id']+'/session.json')
    session = json.loads(session_path.read_bytes())
    pending_path = local(root, '.omc/state/pending-completion.json')
    pending = json.loads(pending_path.read_bytes())
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root,
                                   text=True, stderr=subprocess.PIPE).strip()
    if (session.get('confirmation', {}).get('status') != 'confirmed'
            or session.get('lifecycle', {}).get('status') != 'active'
            or 'senior_coding' not in session.get('role_ids', [])
            or session.get('work_class') != 'implementation'
            or session.get('git', {}).get('head') != head
            or pending.get('session_id') != context['session_id']
            or pending.get('work_id') != context['work_id']
            or not _pending_completion_matches_session(root, pending, current_head=head)):
        raise ValueError('session_start_work_mismatch')
    approval = {key: state[key] for key in
        ('session_id', 'contract_hash', 'basis_sha256', 'started_at')}
    approval['required_checks'] = sorted(state['required_checks'])
    return {'target': str(root.resolve()), 'execution_context': context, 'head': head,
        'approval': approval,
        'code_snapshot_sha256': _code_snapshot_digest(root),
        'pipeline_sha256': hashlib.sha256(local(root, '.omc/pipeline_session.json').read_bytes()).hexdigest(),
        'session_sha256': hashlib.sha256(session_path.read_bytes()).hexdigest(),
        'pending_sha256': hashlib.sha256(pending_path.read_bytes()).hexdigest()}


def create_session_start_handoff(root: Path, context: dict) -> dict | None:
    """Freeze a single approved comparison launch; ordinary launches get none."""
    from omc_home_observation import local
    from omc_native_red_evidence import write_new
    import uuid
    path = local(root, '.omc/validation-commands/policy.json')
    if not path.exists() and not path.is_symlink():
        if '.omc/validation-commands/policy.json' in context.get('contracts', {}):
            raise ValueError('session_start_policy_missing')
        return None
    policy = json.loads(path.read_bytes())
    if policy.get('arm') == 'oh-my-claudecode':
        return None
    binding = _session_start_binding(root, context)
    return write_new(root, '.omc/session-start-handoffs/'+uuid.uuid4().hex+'.json', {
        'schema_version': 'omc-session-start-handoff/v1',
        'created_at': time.time(), 'binding': binding})


def _consume_session_start_handoff(root: Path, raw: str) -> None:
    from omc_home_observation import local
    from omc_native_red_evidence import selected, write_new
    ref = json.loads(raw)
    if (not isinstance(ref, dict) or not isinstance(ref.get('path'), str)
            or not re.fullmatch(r'\.omc/session-start-handoffs/[0-9a-f]{32}\.json', ref['path'])):
        raise ValueError('session_start_handoff_reference_invalid')
    record = selected(root, ref)
    now = time.time()
    if (not isinstance(record, dict) or record.get('schema_version') != 'omc-session-start-handoff/v1'
            or type(record.get('created_at')) not in (int, float)
            or not 0 <= record['created_at'] <= now):
        raise ValueError('session_start_handoff_expired_or_invalid')
    event = json.loads(os.environ.get(SESSION_START_INPUT_ENV, '{}'))
    if (not isinstance(event, dict) or not isinstance(event.get('session_id'), str)
            or not event['session_id'].strip() or event.get('source') not in {'startup', 'compact'}):
        raise ValueError('session_start_execution_identity_required')
    binding = record.get('binding')
    if not isinstance(binding, dict):
        raise ValueError('session_start_handoff_binding_mismatch')
    current = _session_start_binding(root, binding.get('execution_context'))
    consumed_path = ref['path'][:-5]+'.consumed.json'
    if event['source'] == 'compact':
        consumed = json.loads(local(root, consumed_path).read_bytes())
        if (not isinstance(consumed, dict)
                or consumed.get('schema_version') != 'omc-session-start-consumed/v2'
                or consumed.get('handoff') != ref
                or consumed.get('native_session_id') != event['session_id']):
            raise ValueError('session_start_active_execution_mismatch')
        # Compaction continues the already-bound work, not its initial snapshot.
        # Product edits and RED/progress registrations are expected in that work;
        # its approval, work ledger, HEAD and requirements must still be current.
        stable = ('target', 'execution_context', 'head', 'approval',
                  'session_sha256', 'pending_sha256')
        if any(binding[key] != current[key] for key in stable):
            raise ValueError('session_start_active_approval_mismatch')
        return
    if now-record['created_at'] > _START_HANDOFF_TTL_SECONDS or binding != current:
        raise ValueError('session_start_handoff_expired_or_changed')
    # Only the first startup consumes the descriptor. Another startup, clear,
    # resume or fork cannot reuse it even if its reported native ID is the same.
    write_new(root, consumed_path, {
        'schema_version': 'omc-session-start-consumed/v2', 'handoff': ref,
        'native_session_id': event['session_id'], 'consumed_at': now})


def cmd_session_start(root: Path) -> int:
    """검증된 일회용 인계만 유지하고 나머지 시작은 CONTRACT를 초기화한다.

    red_done_files·allowed_impl_files 등 작업 진행 상태는 유지하되,
    CONTRACT 확인 플래그·해시·세션 ID를 리셋해 새 작업의 확인을 강제한다.
    TTL 기반 자동 만료와 달리 명시적 세션 경계를 선언한다.
    """
    handoff = os.environ.get(SESSION_START_HANDOFF_ENV)
    rejected = False
    if handoff is not None:
        try:
            _consume_session_start_handoff(root, handoff)
            print('[PIPELINE] 승인된 동일 작업의 시작 인계 유지')
            return 0
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            rejected = True
            print('[PIPELINE] 시작 인계 불일치·재검증 필요: '+str(exc))
    # A process/role boundary is not permission to renew an expired work clock.
    state, _ = _read_pipeline(root)
    state["contract_confirmed"] = False
    state["contract_hash"] = ""
    state["session_id"] = ""
    state["contract_reset_reason"] = "session_start"
    _save_state(root, state)
    print("[PIPELINE] 🔄 새 세션 시작 — CONTRACT 플래그 초기화")
    return 1 if rejected else 0

def _resume_guard(function, *, lifecycle=False, edit=False):
    from functools import wraps
    @wraps(function)
    def guarded(root, *args, **kwargs):
        from omc_edit_resume import ENV, frozen, load, local, read
        try:
            if frozen(root) and not (function.__name__ == 'cmd_status' and not os.environ.get(ENV)):
                if lifecycle:
                    raise ValueError('expired_work_lifecycle_frozen')
                value = load(root)
                if edit:
                    name = str(local(root, args[0]).relative_to(Path(root).resolve()))
                    if name not in value['allowed_files']:
                        raise ValueError('edit_scope_not_approved')
                    state = read(root, Path(value['generation']) / 'pipeline.json')
                    if not state.get('contract_confirmed'):
                        raise ValueError('new_contract_required')
                    if not _is_test_file(name) and (_red_evidence_problems(root, state) or not state.get('red_evidence')):
                        raise ValueError('new_red_required')
            elif os.environ.get(ENV):
                load(root)
            return function(root, *args, **kwargs)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
            print('[PIPELINE] edit_resume_blocked: ' + str(exc))
            return 1
    return guarded


for _name in ('cmd_contract_done', 'cmd_red_done', 'cmd_verification_template', 'cmd_verification_path', 'cmd_status'):
    globals()[_name] = _resume_guard(globals()[_name])
for _name in ('cmd_check', 'cmd_check_edit'):
    globals()[_name] = _resume_guard(globals()[_name], edit=True)
for _name in ('cmd_reset', 'cmd_session_start', 'cmd_approve_checks', 'cmd_allow', 'cmd_register_transfer_baseline'):
    globals()[_name] = _resume_guard(globals()[_name], lifecycle=True)

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
    p_contract.add_argument("--maintenance-recovery-file")
    p_contract.add_argument("--maintenance-recovery-sha256")
    p_info=sub.add_parser("maintenance-recovery-info",help="등록 실패의 ready 참조 읽기")
    p_info.add_argument("--ready-file",required=True)
    p_approval = sub.add_parser("approve-checks", help="이미 받은 사용자 검증 기준 승인을 개정별 기록 (진위 인증 아님)")
    p_approval.add_argument("--file", type=Path, required=True)
    p_reapprove = sub.add_parser("reapprove-completion", help="명시적 재승인을 작업별 1회 기록; 완료 판정에만 적용")
    p_reapprove.add_argument("--file", type=Path, required=True)
    sub.add_parser("register-transfer-baseline", help="승인된 이관 전 기준 1회 등록")
    p_transfer = sub.add_parser("complete-transfer", help="검증된 동일 커밋 이관 완료 판정")
    p_transfer.add_argument("--verification-file", type=Path, required=True)
    p_maintenance=sub.add_parser("complete-maintenance",help="승인된 캐시 정리 전용 완료 판정")
    p_maintenance.add_argument("--verification-file",type=Path,required=True)
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

    for completion_parser in (p_complete,p_transfer,p_maintenance):
        completion_parser.add_argument("--output-mode", choices=["legacy_inline","detached_v1"], default="legacy_inline")
        completion_parser.add_argument("--output-context", type=Path)
        completion_parser.add_argument("--output-context-sha256")
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
        return cmd_contract_done(root, getattr(args, "content", ""), args.required_checks,
            args.maintenance_recovery_file,args.maintenance_recovery_sha256)
    if args.cmd=="maintenance-recovery-info":
        try:
            print(json.dumps(maintenance_recovery_info(root,args.ready_file),ensure_ascii=False));return 0
        except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
            print("[PIPELINE] "+str(exc));return 1
    if args.cmd == "approve-checks":
        return cmd_approve_checks(root, args.file)
    if args.cmd == "reapprove-completion":
        return cmd_reapprove_completion(root, args.file)
    if args.cmd == "register-transfer-baseline":
        return cmd_register_transfer_baseline(root)
    if args.cmd in {"complete-task","complete-transfer","complete-maintenance"} and args.output_mode=="detached_v1":
        if not args.output_context or not args.output_context_sha256:
            print("판정 기록의 연결 정보가 없습니다. 후속 진행을 보류합니다.",file=sys.stderr);return 2
        evaluation=({"complete-transfer":evaluate_transfer_completion,"complete-maintenance":evaluate_maintenance_completion,"complete-task":_evaluate_implementation_completion}[args.cmd])(root,args.verification_file)
        if args.cmd=="complete-task":
            try:
                latest=json.loads((root/".omc/state/latest.json").read_bytes());state,_=_read_pipeline(root);sid=_completion_owner(root,latest,state)
                if _completion_kind(_load_approved_basis(root,sid))!='implementation':evaluation=_transfer_block("completion_kind_entry_mismatch")
            except (OSError,ValueError,TypeError,KeyError,AttributeError):pass
        return _print_completion(evaluation,(args.output_context,args.output_context_sha256),root=root,verification_file=args.verification_file)
    if args.cmd == "complete-maintenance":
        print("판정 기록은 detached_v1 문맥이 필요합니다.",file=sys.stderr);return 2
    if args.cmd == "complete-transfer":
        return cmd_complete_transfer(root, args.verification_file)
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
