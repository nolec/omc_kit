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

def cmd_red_done(root: Path, test_file: str) -> int:
    """RED 단계 완료 등록."""
    if not _is_test_file(test_file):
        # 테스트 파일이 아니어도 수동 등록 허용 (사용자가 직접 호출)
        print(f"[PIPELINE] ⚠️  테스트 파일 패턴 아님: {test_file} — 그래도 등록합니다.")

    state = _load_state(root)
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

def cmd_contract_done(root: Path, content: str = "") -> int:
    """CONTRACT 양식 작성 + 사용자 컨펌 완료 등록.

    content가 주어지면 SHA-256 해시를 저장해 우발적 위조를 방지한다.
    content가 없으면 타임스탬프 기반 해시를 사용한다.
    이후 edit_file(기존 파일 수정) 호출이 허용된다.
    세션 TTL 내에서 유효하며, TTL 만료 시 자동 초기화된다.

    latest.json에서 latest_confirmed_session_id를 읽어 pipeline_session.json에
    session_id로 기록한다 — 훅이 세션 간 오염 여부를 판별하는 데 사용한다.
    """
    state = _load_state(root)
    state["contract_confirmed"] = True
    hash_source = content if content else f"confirmed_at:{time.time()}"
    state["contract_hash"] = _make_contract_hash(hash_source)
    state["session_id"] = ""  # 먼저 초기화 — 읽기 실패 시 stale id 잔류 방지

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

    _save_state(root, state)
    print("[PIPELINE] ✅ CONTRACT 확인 완료 — 기존 파일 수정이 허용됩니다.")
    return 0

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
    state["session_id"] = ""  # 새 세션 시작 시 이전 session_id 클리어
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

    p_allow = sub.add_parser("allow", help="특정 파일 RED 없이 예외 허용 (사용자 명시 + 이유 기록)")
    p_allow.add_argument("file", help="예외 허용할 구현 파일 경로")
    p_allow.add_argument("--reason", "-r", default="", help="예외 허용 이유 (감사 로그에 기록됩니다)")

    p_contract = sub.add_parser("contract-done", help="CONTRACT 양식 작성 + 사용자 컨펌 완료 등록 (이후 edit_file 허용)")
    p_contract.add_argument("--content", default="", help="CONTRACT 내용 (해시 저장용, 우발적 위조 방지)")

    p_check_edit = sub.add_parser("check-edit", help="기존 파일 수정 전 CONTRACT 확인 여부 체크")
    p_check_edit.add_argument("file", help="수정하려는 파일 경로")
    p_check_edit.add_argument(
        "--autopilot",
        action="store_true",
        default=False,
        help="자동화 파이프라인 모드 — CONTRACT 차단 우회 (opt-in)",
    )

    sub.add_parser("status", help="현재 세션 파이프라인 상태 출력")
    sub.add_parser("reset", help="세션 파이프라인 상태 초기화")
    sub.add_parser("session-start", help="새 세션 시작 — contract_confirmed 초기화 (red_done_files 보존)")
    sub.add_parser("scope-check", help="staged 변경 범위 경고 출력 (차단 없음)")

    args = ap.parse_args()
    root = Path(args.target).resolve()

    if args.cmd == "check":
        return cmd_check(root, args.file, bypass=args.autopilot)
    if args.cmd == "red-done":
        return cmd_red_done(root, args.test_file)
    if args.cmd == "allow":
        reason = getattr(args, "reason", "")
        return cmd_allow(root, args.file, reason)
    if args.cmd == "contract-done":
        return cmd_contract_done(root, getattr(args, "content", ""))
    if args.cmd == "check-edit":
        return cmd_check_edit(root, args.file, bypass=getattr(args, "autopilot", False))
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
