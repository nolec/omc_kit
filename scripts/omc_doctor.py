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

from omc_hook_contract import (
    CLAUDE_HOOK_CONTRACT,
    CODEX_HOOK_CONTRACT,
    GEMINI_HOOK_CONTRACT,
    claude_has_pre_mutate_guard,
    claude_has_session_context_hooks,
    codex_has_posttooluse_soft_guard,
    codex_has_session_context_hooks,
    gemini_has_pre_mutate_guard,
    gemini_has_session_context_hooks,
)

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
    script = project_root / "scripts" / "omc.py"
    if not script.exists():
        return False, "scripts/omc.py not found"
    try:
        proc = subprocess.run(
            ["python3", str(script), "state", "status", "--target", str(project_root)],
            cwd=str(project_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, proc.stdout.strip()


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

    # ── auto_compact 설정 — policy.json 안에 threshold가 있어야 자동 compact 작동 ──
    def _auto_compact_configured() -> bool:
        if not policy.exists():
            return False
        try:
            data = json.loads(policy.read_text(encoding="utf-8"))
            return int(data.get("auto_compact_threshold_count", 0)) > 0
        except Exception:
            return False

    def _fix_auto_compact():
        if not policy.exists():
            return
        try:
            data = json.loads(policy.read_text(encoding="utf-8"))
            data.setdefault("auto_compact_threshold_count", 50)
            data.setdefault("auto_compact_keep_entries", 25)
            policy.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    checks.append(Check(
        ".omc/policy.json (auto_compact)",
        _auto_compact_configured(),
        fix_cmd="python3 scripts/install.py --target . --force",
        fix_fn=_fix_auto_compact,
        detail="auto_compact_threshold_count 미설정 — 세션이 쌓여도 자동 정리가 작동하지 않습니다.",
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

    # ── pre-commit hook ──────────────────────────────────────────────────────
    pre_commit = root / ".git" / "hooks" / "pre-commit"
    tdd_in_hook = pre_commit.exists() and "omc_tdd_check" in pre_commit.read_text(encoding="utf-8") if pre_commit.exists() else False

    def _fix_pre_commit():
        sample = root / "scripts" / "pre-commit.sample"
        src = sample if sample.exists() else None
        if not src:
            return
        git_hooks_dir = root / ".git" / "hooks"
        if not git_hooks_dir.exists():
            print(f"  [INFO] .git/hooks/ 없음 — git init 후 다시 시도하세요.")
            return
        dst = git_hooks_dir / "pre-commit"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        _ensure_executable(dst)

    checks.append(Check(
        ".git/hooks/pre-commit (TDD 체크 포함)",
        tdd_in_hook,
        detail="" if tdd_in_hook else "TDD 체크 미포함 또는 hook 없음",
        fix_cmd="python3 scripts/install.py --target . --force",
        fix_fn=_fix_pre_commit,
    ))

    # ── pre-commit pipeline_guard CONTRACT 차단 검사 ─────────────────────
    pipeline_guard_in_hook = (
        pre_commit.exists()
        and "omc_pipeline_guard" in pre_commit.read_text(encoding="utf-8")
    ) if pre_commit.exists() else False
    checks.append(Check(
        ".git/hooks/pre-commit (pipeline_guard CONTRACT 검증)",
        pipeline_guard_in_hook,
        detail="" if pipeline_guard_in_hook else "pipeline_guard CONTRACT 검사 미포함 — Codex/Gemini 차단 불가",
        fix_cmd="python3 scripts/install.py --target . --force",
        fix_fn=_fix_pre_commit,
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
        detail="" if agent_hook_ok else "파일 없음 또는 차단 코드(exit 2 / omc_pipeline_guard) 미포함 — Claude Code PreToolUse 차단 불가",
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
        root / ".cursor" / "hooks" / "omc-pipeline-check.sh",
        root / ".git" / "hooks" / "pre-commit",
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

    # ── Claude Code 훅 ───────────────────────────────────────────────────────
    claude_settings = root / ".claude" / "settings.json"
    claude_ok = False
    if claude_settings.exists():
        try:
            data = json.loads(claude_settings.read_text(encoding="utf-8"))
            claude_ok = claude_has_session_context_hooks(data)
        except Exception:
            pass
    checks.append(Check(
        CLAUDE_HOOK_CONTRACT["session_context"]["label"],
        claude_ok,
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    claude_premutate_ok = False
    if claude_settings.exists():
        try:
            data = json.loads(claude_settings.read_text(encoding="utf-8"))
            claude_premutate_ok = claude_has_pre_mutate_guard(data)
        except Exception:
            pass
    checks.append(Check(
        CLAUDE_HOOK_CONTRACT["pre_mutate_guard"]["label"],
        claude_premutate_ok,
        detail="" if claude_premutate_ok else "Claude Code PreToolUse 가드가 비활성 상태입니다",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── Gemini CLI 훅 ────────────────────────────────────────────────────────
    gemini_settings = root / ".gemini" / "settings.json"
    gemini_session_ok = False
    if gemini_settings.exists():
        try:
            data = json.loads(gemini_settings.read_text(encoding="utf-8"))
            gemini_session_ok = gemini_has_session_context_hooks(data)
        except Exception:
            pass
    checks.append(Check(
        GEMINI_HOOK_CONTRACT["session_context"]["label"],
        gemini_session_ok,
        detail="" if gemini_session_ok else "Gemini 세션/BM25 컨텍스트 주입이 비활성 상태입니다",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    gemini_premutate_ok = False
    if gemini_settings.exists():
        try:
            data = json.loads(gemini_settings.read_text(encoding="utf-8"))
            gemini_premutate_ok = gemini_has_pre_mutate_guard(data)
        except Exception:
            pass
    checks.append(Check(
        GEMINI_HOOK_CONTRACT["pre_mutate_guard"]["label"],
        gemini_premutate_ok,
        detail="" if gemini_premutate_ok else "Gemini BeforeTool 가드가 비활성 상태입니다",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── Codex CLI 훅 ─────────────────────────────────────────────────────────
    codex_hooks = root / ".codex" / "hooks.json"
    codex_hooks_ok = False
    if codex_hooks.exists():
        try:
            data = json.loads(codex_hooks.read_text(encoding="utf-8"))
            codex_hooks_ok = codex_has_session_context_hooks(data)
        except Exception:
            pass
    checks.append(Check(
        CODEX_HOOK_CONTRACT["session_context"]["label"],
        codex_hooks_ok,
        detail="" if codex_hooks_ok else "Codex BM25 컨텍스트 주입이 비활성 상태입니다",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    codex_posttooluse_ok = False
    if codex_hooks.exists():
        try:
            data = json.loads(codex_hooks.read_text(encoding="utf-8"))
            codex_posttooluse_ok = codex_has_posttooluse_soft_guard(data)
        except Exception:
            pass
    checks.append(Check(
        CODEX_HOOK_CONTRACT["post_mutate_soft_guard"]["label"],
        codex_posttooluse_ok,
        detail="" if codex_posttooluse_ok else "Codex PostToolUse 소프트 가드가 비활성 상태입니다",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── Cursor rules ─────────────────────────────────────────────────────────
    omc_rule = root / ".cursor" / "rules" / "omc-role-suggest.mdc"
    checks.append(Check(
        ".cursor/rules/omc-role-suggest.mdc", omc_rule.exists(),
        fix_cmd="python3 scripts/install.py --target . --force",
    ))
    tdd_rule = root / ".cursor" / "rules" / "omc-tdd.mdc"
    checks.append(Check(
        ".cursor/rules/omc-tdd.mdc", tdd_rule.exists(),
        fix_cmd="python3 scripts/install.py --target . --force",
    ))
    for rule_file in ["omc-pipeline.mdc", "omc-roles.mdc", "omc-commands.mdc"]:
        rule_path = root / ".cursor" / "rules" / rule_file
        checks.append(Check(
            f".cursor/rules/{rule_file}", rule_path.exists(),
            fix_cmd="python3 scripts/install.py --target . --force",
        ))

    # ── Agent Skills (.agents/skills/ — Codex) ──────────────────────────────
    codex_skills_dir = root / ".agents" / "skills"
    codex_skills_ok = codex_skills_dir.exists() and any(codex_skills_dir.iterdir())
    checks.append(Check(
        ".agents/skills/ (Codex Agent Skills)",
        codex_skills_ok,
        detail="" if codex_skills_ok else "스킬 없음 — $omc-plan 등 명시적 호출 불가",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── Agent Skills (.agent/skills/ — Antigravity) ──────────────────────────
    antigrav_skills_dir = root / ".agent" / "skills"
    antigrav_skills_ok = antigrav_skills_dir.exists() and any(antigrav_skills_dir.iterdir())
    checks.append(Check(
        ".agent/skills/ (Antigravity Agent Skills)",
        antigrav_skills_ok,
        detail="" if antigrav_skills_ok else "스킬 없음 — Antigravity 암묵적 트리거 불가",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── .agent/skills/ ↔ .agents/skills/ 동기화 검사 ────────────────────────
    # install.py는 .agents/skills/를 SSOT로 두고 .agent/skills/에 미러링합니다.
    # 누군가 한쪽만 수정하면 Antigravity/Codex 간 스킬 내용이 달라집니다.
    if codex_skills_dir.exists() and antigrav_skills_dir.exists():
        codex_names = {p.name for p in codex_skills_dir.iterdir() if p.is_dir()}
        antigrav_names = {p.name for p in antigrav_skills_dir.iterdir() if p.is_dir()}
        only_codex = codex_names - antigrav_names
        only_antigrav = antigrav_names - codex_names
        skills_in_sync = not only_codex and not only_antigrav
        detail_parts = []
        if only_codex:
            detail_parts.append(f".agents/skills/에만 있음: {', '.join(sorted(only_codex))}")
        if only_antigrav:
            detail_parts.append(f".agent/skills/에만 있음: {', '.join(sorted(only_antigrav))}")
        checks.append(Check(
            ".agent/skills/ ↔ .agents/skills/ 동기화",
            skills_in_sync,
            detail=" | ".join(detail_parts) if detail_parts else "",
            fix_cmd="python3 scripts/install.py --target . --force",
        ))

    # ── Antigravity Workflows (.agent/workflows/) ─────────────────────────────
    antigrav_workflows_dir = root / ".agent" / "workflows"
    antigrav_workflows_ok = antigrav_workflows_dir.exists() and any(antigrav_workflows_dir.iterdir())
    checks.append(Check(
        ".agent/workflows/ (Antigravity Workflows)",
        antigrav_workflows_ok,
        detail="" if antigrav_workflows_ok else "워크플로우 없음 — Antigravity /plan 등 명시적 호출 불가",
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── Antigravity Rules (.agent/rules/) ────────────────────────────────────
    antigrav_rules_file = root / ".agent" / "rules" / "omc-always.md"
    checks.append(Check(
        ".agent/rules/omc-always.md (Antigravity Rules)",
        antigrav_rules_file.exists(),
        fix_cmd="python3 scripts/install.py --target . --force",
    ))

    # ── Shared + personal overlays ───────────────────────────────────────────
    for rel_path, marker, label in [
        ("AGENTS.md", AGENTS_OMC_BEGIN, "AGENTS.md (최신 OMC 블록 포함)"),
        (".claude/CLAUDE.md", "OMC Overlay", ".claude/CLAUDE.md (OMC 오버레이)"),
        (".gemini/GEMINI.md", "OMC Overlay", ".gemini/GEMINI.md (OMC 오버레이)"),
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
        ".cursor/hooks/*.sh",
        ".cursor/rules/*.mdc",
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
            "find scripts .agent-hooks .cursor -type f "
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
        root / ".cursor" / "hooks",
        root / "templates" / ".agent-hooks",
        root / "templates" / ".cursor" / "hooks",
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

    if not warnings and status_ok:
        print("\n OMC 설치 상태 이상 없음")
        return 0

    return 1 if [c for c in checks if not c.ok and c.label.startswith("scripts/")] else 0


if __name__ == "__main__":
    raise SystemExit(main())
