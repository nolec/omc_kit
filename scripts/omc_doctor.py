#!/usr/bin/env python3
"""
omc_doctor.py — OMC 설치 상태 진단 + 자동 수정

사용:
  python3 scripts/omc_doctor.py                  # 진단만 (기본)
  python3 scripts/omc_doctor.py --fix            # 진단 + 자동 수정
  python3 scripts/omc_doctor.py --target /path  # 다른 프로젝트 경로
"""
from __future__ import annotations

import argparse
import json
import shutil
import stat
import subprocess
from pathlib import Path

import omc_install_audit
from omc_hook_contract import codex_hooks_disabled


AGENTS_OMC_BEGIN = "<!-- OMC:BEGIN -->"
AGENTS_OMC_END = "<!-- OMC:END -->"
AGENTS_OMC_VERSION = "<!-- OMC:AGENTS:V1 -->"


# ---------------------------------------------------------------------------
# 출력 헬퍼
# ---------------------------------------------------------------------------

def _ok(label: str, detail: str = "") -> str:
    suffix = f" — {detail}" if detail else ""
    return f"  [OK]   {label}{suffix}"


def _warn(label: str, detail: str = "", fix: str = "") -> str:
    suffix = f" — {detail}" if detail else ""
    fix_hint = f"\n           FIX: {fix}" if fix else ""
    return f"  [WARN] {label}{suffix}{fix_hint}"


def _fixed(label: str) -> str:
    return f"  [FIX]  {label} ← 자동 수정 완료"


# ---------------------------------------------------------------------------
# Git Hook 유틸
# ---------------------------------------------------------------------------

def _ensure_executable(path: Path) -> None:
    if path.exists():
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# 체크 항목
# ---------------------------------------------------------------------------

class Check:
    def __init__(self, label: str, ok: bool, detail: str = "", fix_cmd: str = "", fix_fn=None):
        self.label = label
        self.ok = ok
        self.detail = detail
        self.fix_cmd = fix_cmd   # 사용자에게 보여줄 수동 수정 커맨드
        self.fix_fn = fix_fn     # --fix 시 실행할 함수 (callable or None)


def _run_status(project_root: Path) -> tuple[bool, str]:
    """Observe existing state only; status CLI also leaves absent state untouched."""
    path = project_root / ".omc/state/latest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "not initialized; automatic hooks are disabled"
    except (OSError, ValueError):
        return False, "existing state is unreadable or invalid"
    if not isinstance(data, dict):
        return False, "existing state is invalid"
    return True, "\n".join(f"{key}: {data.get(key)}" for key in (
        "latest_confirmed_session_id", "latest_pending_session_id"))


def _installation_readiness(project_root: Path) -> dict[str, object]:
    """Return the same readiness report used by the public version command."""
    try:
        audit = omc_install_audit.audit_target(
            project_root,
            trusted_source_root=Path(__file__).resolve().parent.parent,
        )
        report = audit.get("version_readiness")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "overall_status": "invalid",
            "release_status": "invalid",
            "source_status": "invalid",
            "install_integrity": "invalid",
            "error": str(exc),
        }
    if not isinstance(report, dict):
        return {
            "overall_status": "invalid",
            "release_status": "invalid",
            "source_status": "invalid",
            "install_integrity": "invalid",
            "error": "missing version_readiness report",
        }
    return report


_FALLBACK_DEPLOYED_SCRIPTS = {
    "install.py",
    "omc.py",
    "compose_prompt.py",
    "omc_chat.py",
    "omc_exec.py",
    "omc_guard.py",
    "omc_state.py",
    "omc_hooks.py",
    "omc_role_suggest.py",
    "omc_tdd_check.py",
    "omc_pipeline_guard.py",
    "omc_context.py",
    "omc_lesson.py",
    "omc_cost.py",
    "omc_run.py",
    "omc_domain.py",
    "omc_utils.py",
    "omc_peer_review.py",
    "omc_autopilot.py",
}


def _load_deployed_script_names(root: Path) -> set[str]:
    import importlib.util

    install_path = root / "scripts" / "install.py"
    if not install_path.exists():
        return set(_FALLBACK_DEPLOYED_SCRIPTS)
    try:
        spec = importlib.util.spec_from_file_location("omc_install_contract", install_path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        helper = getattr(module, "_deployed_script_names", None)
        if helper is None:
            return set(_FALLBACK_DEPLOYED_SCRIPTS)
        return set(helper(root))
    except Exception:
        return set(_FALLBACK_DEPLOYED_SCRIPTS)


def _build_checks(root: Path) -> list[Check]:
    checks: list[Check] = []

    # ── 배포 스크립트 계약 (install.py와 공유) ───────────────────────────────
    scripts = sorted(_load_deployed_script_names(root))
    for s in scripts:
        p = root / "scripts" / s
        checks.append(Check(
            f"scripts/{s}", p.exists(),
            fix_cmd=f"python3 scripts/install.py --target . --force",
        ))

    # ── .omc 상태 디렉토리 ──────────────────────────────────────────────────
    omc_dir = root / ".omc"
    policy = omc_dir / "policy.json"

    def _fix_omc_init():
        omc_dir.mkdir(exist_ok=True)
        if not policy.exists():
            policy.write_text(json.dumps({"enforce_confirm": True}, indent=2) + "\n", encoding="utf-8")
        summary = omc_dir / "summary.md"
        if not summary.exists():
            summary.write_text("# OMC Session Summary\n", encoding="utf-8")

    checks.append(Check(
        ".omc/policy.json", policy.exists(),
        fix_cmd="python3 scripts/omc.py state init --target .",
        fix_fn=_fix_omc_init,
    ))
    checks.append(Check(
        ".omc/summary.md", (omc_dir / "summary.md").exists(),
        fix_cmd="python3 scripts/omc.py state init --target .",
        fix_fn=_fix_omc_init,
    ))

    # ── .omc/lessons/ — Compound Engineering ───────────────────────────────
    lessons_dir = omc_dir / "lessons"
    def _fix_lessons():
        lessons_dir.mkdir(parents=True, exist_ok=True)
        gk = lessons_dir / ".gitkeep"
        if not gk.exists():
            gk.write_text("# Compound Engineering 교훈 디렉토리\n", encoding="utf-8")
    checks.append(Check(
        ".omc/lessons/", lessons_dir.exists(),
        fix_cmd="python3 scripts/install.py --target . --force",
        fix_fn=_fix_lessons,
    ))

    # ── .omc/cost_log.jsonl — 비용/작업 규모 추적 ──────────────────────────
    cost_log = omc_dir / "cost_log.jsonl"
    cost_entries = 0
    if cost_log.exists():
        with open(cost_log, encoding="utf-8") as f:
            cost_entries = sum(1 for l in f if l.strip())
    checks.append(Check(
        ".omc/cost_log.jsonl",
        cost_log.exists(),
        detail=f"{cost_entries}개 기록됨" if cost_log.exists() else "",
        fix_cmd="python3 scripts/omc_cost.py record --task '첫 기록'",
    ))

    # ── agent-hooks ──────────────────────────────────────────────────────────
    hooks_dir = root / ".agent-hooks"
    hooks_ok = (hooks_dir / "omc-session-start.sh").exists()
    checks.append(Check(
        ".agent-hooks/omc-session-start.sh", hooks_ok,
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── .agent-hooks/omc-pipeline-check.sh 존재 + exit 2 차단 코드 ─────────
    agent_pipeline_check = hooks_dir / "omc-pipeline-check.sh"
    _agent_hook_content = agent_pipeline_check.read_text(encoding="utf-8") if agent_pipeline_check.exists() else ""
    agent_hook_ok = agent_pipeline_check.exists() and (
        "exit 2" in _agent_hook_content or "omc_pipeline_guard" in _agent_hook_content
    )
    checks.append(Check(
        ".agent-hooks/omc-pipeline-check.sh (hook_block 차단코드)",
        agent_hook_ok,
        detail="" if agent_hook_ok else "파일 없음 또는 차단 코드(exit 2 / omc_pipeline_guard) 미포함 — 명시적 파이프라인 검증 스크립트 확인 필요",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── 훅 파일 실행권한 검사 ─────────────────────────────────────────────────
    import stat as _stat
    hook_paths = [
        hooks_dir / "omc-session-start.sh",
        hooks_dir / "omc-session-end.sh",
        hooks_dir / "omc-pipeline-check.sh",
        hooks_dir / "omc-prompt-inject.sh",
        hooks_dir / "omc-before-agent.sh",
    ]
    non_exec = [
        p for p in hook_paths
        if p.exists() and not (p.stat().st_mode & _stat.S_IXUSR)
    ]
    exec_ok = len(non_exec) == 0
    checks.append(Check(
        "훅 파일 실행권한 (chmod +x)",
        exec_ok,
        detail="" if exec_ok else "실행권한 없음: " + ", ".join(p.name for p in non_exec),
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # Disabled automatic hooks are an installation policy, not work approval.
    codex_hooks = root / ".codex" / "hooks.json"
    disabled = False
    try:
        disabled = codex_hooks_disabled(json.loads(codex_hooks.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        pass
    checks.append(Check("Codex automatic hooks disabled", disabled,
                        detail="manual engine guards remain available" if disabled else "invalid or enabled hook configuration"))

    # ── Agent Skills (.agents/skills/ — Codex) ──────────────────────────────
    codex_skills_dir = root / ".agents" / "skills"
    codex_skills_ok = codex_skills_dir.exists() and any(codex_skills_dir.iterdir())
    checks.append(Check(
        ".agents/skills/ (Codex Agent Skills)",
        codex_skills_ok,
        detail="" if codex_skills_ok else "스킬 없음 — $omc-plan 등 명시적 호출 불가",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── Shared + personal overlays ───────────────────────────────────────────
    for rel_path, marker, label in [
        ("AGENTS.md", AGENTS_OMC_BEGIN, "AGENTS.md (최신 OMC 블록 포함)"),
    ]:
        p = root / rel_path
        if p.exists():
            text = p.read_text(encoding="utf-8")
            has_marker = marker in text
            if rel_path == "AGENTS.md":
                has_marker = has_marker and AGENTS_OMC_END in text and AGENTS_OMC_VERSION in text
        else:
            has_marker = False
        checks.append(Check(
            label,
            has_marker,
            fix_cmd=f"python3 scripts/install.py --target . --force",
        ))


    # ── 잔존 구버전 참조 체크 ───────────────────────────────────────────────
    # 이름 변경 후 스크립트/문서에 구버전 문자열이 남아 있는지 검사
    # noqa: 분리된 문자열로 작성 — 자가 매칭 방지 (doctor 자신도 이 파일을 스캔하므로)
    _STALE_PATTERNS = [
        "multi" "_assistant_kit",
        "Multi-" "Assistant Kit",
        "Multi-" "assistant Kit",
        "Opinionated Multi-" "assistant",
        "Opinionated Multi-" "Assistant",
    ]
    _STALE_SCAN_GLOBS = [
        "scripts/*.py",
        ".agent-hooks/*.sh",
        "*.md",          # 루트 .md (AGENTS.md, CLAUDE.md 등)
    ]
    _STALE_IGNORE_DIRS = {".omc", ".git", "node_modules", ".stylelintcache"}

    stale_files: list[str] = []
    for glob_pat in _STALE_SCAN_GLOBS:
        for fpath in root.glob(glob_pat):
            # .omc/ 및 .git/ 제외
            parts = set(fpath.parts)
            if parts & _STALE_IGNORE_DIRS:
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for pat in _STALE_PATTERNS:
                if pat in text:
                    stale_files.append(f"{fpath.relative_to(root)} ('{pat}')")
                    break

    checks.append(Check(
        "구버전 참조 없음 (omc_kit 등)",
        len(stale_files) == 0,
        detail=", ".join(stale_files) if stale_files else "이상 없음",
        fix_cmd=(
            "find scripts .agent-hooks -type f "
            r"\( -name '*.py' -o -name '*.sh' -o -name '*.md' -o -name '*.mdc' \) "
            "| xargs sed -i '' 's|omc_kit|omc_kit|g; "
            "s|Orchestrated Multi-agent Craft|Orchestrated Multi-agent Craft|g'"
        ) if stale_files else None,
    ))


    # ── 필수 문서 존재 ────────────────────────────────────────────────────────
    for doc in ["ETHOS.md", "CODEX.md", "CONVENTIONS.md"]:
        p = root / doc
        checks.append(Check(
            f"{doc} 존재",
            p.exists(),
            fix_cmd=f"python3 scripts/install.py --target . --force  # {doc} 재배포",
        ))

    # ── ETHOS.md 섹션 5 플레이스홀더 여부 ────────────────────────────────────
    ethos_path = root / "ETHOS.md"
    if ethos_path.exists():
        ethos_text = ethos_path.read_text(encoding="utf-8")
        placeholder_filled = "설치 후 이 섹션을 프로젝트에 맞게 채우세요" not in ethos_text
        checks.append(Check(
            "ETHOS.md 섹션 5 (프로젝트 맥락) 작성 완료",
            placeholder_filled,
            detail="플레이스홀더가 남아 있습니다. ETHOS.md 섹션 5를 프로젝트에 맞게 채우세요." if not placeholder_filled else "작성 완료",
        ))


    # ── Python 버전 (>= 3.8) ─────────────────────────────────────────────────
    import sys as _sys
    py_ok = _sys.version_info >= (3, 8)
    checks.append(Check(
        f"Python >= 3.8 ({_sys.version.split()[0]})",
        py_ok,
        detail=f"현재: {_sys.version.split()[0]}",
    ))

    # ── 외부 CLI 도구 ───────────────────────────────────────────────────────
    for cli in ["codex", "gemini"]:
        path = shutil.which(cli)
        checks.append(Check(f"{cli} CLI", bool(path), detail=path or "not found in PATH"))

    # ── macOS 호환성: mktemp --suffix 감지 ─────────────────────────────────
    sh_scan_dirs = [
        root / ".agent-hooks",
        root / "templates" / ".agent-hooks",
    ]
    bad_sh: list[str] = []
    for d in sh_scan_dirs:
        if not d.is_dir():
            continue
        for sh in d.rglob("*.sh"):
            try:
                sh_text = sh.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "mktemp --suffix" in sh_text:
                bad_sh.append(str(sh.relative_to(root)))
    checks.append(Check(
        "macOS 호환성: mktemp --suffix 없음",
        len(bad_sh) == 0,
        detail=", ".join(bad_sh) if bad_sh else None,
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    return checks


def run_checks(root: Path) -> list[Check]:
    """공개 API — 프로그래매틱 사용 및 테스트용. _build_checks()의 외부 진입점."""
    return _build_checks(root)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="OMC 설치 상태 진단 + 자동 수정")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="프로젝트 루트")
    ap.add_argument("--fix", action="store_true", help="수정 가능한 문제를 자동으로 수정")
    args = ap.parse_args()

    root = args.target.resolve()
    print(f"\n OMC Doctor — {root}\n{'='*55}")

    checks = _build_checks(root)
    warnings: list[Check] = []

    for c in checks:
        if c.ok:
            print(_ok(c.label))
        else:
            warnings.append(c)
            print(_warn(c.label, c.detail, c.fix_cmd if not args.fix else ""))

    # ── 상태 요약 ──────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    status_ok, status_text = _run_status(root)
    if status_ok:
        print(_ok("OMC 상태"))
        for line in status_text.splitlines():
            if any(k in line for k in ["latest_confirmed", "latest_pending", "enforce_confirm"]):
                print(f"   {line}")
    else:
        print(_warn("OMC 상태", status_text or "failed",
                    fix="python3 scripts/omc.py state init --target ."))

    readiness = _installation_readiness(root)
    readiness_status = str(readiness.get("overall_status", "invalid"))
    readiness_ok = readiness_status in {"up_to_date", "development_source"}
    readiness_detail = (
        f"release={readiness.get('release_status', 'invalid')}, "
        f"source={readiness.get('source_status', 'invalid')}, "
        f"integrity={readiness.get('install_integrity', 'invalid')}"
    )
    if readiness_ok:
        print(_ok(f"OMC readiness: {readiness_status}", readiness_detail))
    else:
        print(_warn(f"OMC readiness: {readiness_status}", readiness_detail))

    # ── 자동 수정 ──────────────────────────────────────────────────────────
    if args.fix and warnings:
        fixable = [c for c in warnings if c.fix_fn]
        unfixable = [c for c in warnings if not c.fix_fn]

        if fixable:
            print(f"\n[자동 수정 시작] {len(fixable)}개 항목")
            for c in fixable:
                try:
                    c.fix_fn()
                    print(_fixed(c.label))
                except Exception as e:
                    print(f"  [ERR]  {c.label} — {e}")

        if unfixable:
            print(f"\n[수동 수정 필요] {len(unfixable)}개 항목")
            for c in unfixable:
                fix_hint = f"\n   → {c.fix_cmd}" if c.fix_cmd else ""
                print(f"  [WARN] {c.label}{fix_hint}")
    elif warnings:
        print(f"\n총 {len(warnings)}개 경고 — 자동 수정하려면: python3 scripts/omc_doctor.py --fix")

    if not warnings and status_ok and readiness_ok:
        print("\n OMC 설치 상태 이상 없음")
        return 0

    return 1 if [c for c in checks if not c.ok and c.label.startswith("scripts/")] else 0


if __name__ == "__main__":
    raise SystemExit(main())
