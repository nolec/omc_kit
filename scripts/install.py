#!/usr/bin/env python3
from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path


def _copy(src: Path, dst: Path, *, force: bool) -> None:
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists() and not force:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _write(dst: Path, content: str, *, force: bool) -> None:
    if dst.exists() and not force:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content.rstrip() + "\n", encoding="utf-8")


_SCRIPTS_EXTRA = {
    "install.py",        # 인스톨러 자체 — 타겟에서 재실행 가능하도록
    "omc.py",            # OMC 진입점
    "compose_prompt.py", # 프롬프트 조합 유틸
}

# Shared OMC hook contract markers used across install/doctor/tests:
# - session bootstrap
# - pre-mutate guard
# - post-mutate soft guard
# - install/doctor verification
HOOK_CONTRACT_MARKERS = (
    "session bootstrap",
    "pre-mutate guard",
    "post-mutate soft guard",
    "install/doctor verification",
)

HOOK_CONTRACT_SUMMARY = " / ".join(HOOK_CONTRACT_MARKERS)


def _deployed_script_names(kit_root: Path) -> set[str]:
    scripts_src = kit_root / "scripts"
    names: set[str] = set()
    if scripts_src.exists():
        for src in sorted(scripts_src.glob("*.py")):
            if src.name.startswith("omc_") or src.name in _SCRIPTS_EXTRA:
                names.add(src.name)
    return names


def _ensure_executable(path: Path) -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode
    try:
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except PermissionError:
        print(
            f"[WARN] 실행 권한 설정 실패: {path}\n"
            "       현재 환경 권한(샌드박스/OS 정책)으로 chmod가 차단되었습니다.\n"
            "       권한 허용 후 아래 명령으로 재실행하세요:\n"
            "       python3 scripts/omc.py setup --target ."
        )


def _install_claude_settings(settings_path: Path, *, force: bool) -> None:
    """Create or merge .claude/settings.json with OMC SessionStart/End + PreToolUse + UserPromptSubmit hooks.

    Shared hook contract: HOOK_CONTRACT_SUMMARY
    """
    import json

    omc_hooks = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit|MultiEdit",
                    "hooks": [
                        {"type": "command", "command": ".agent-hooks/omc-pipeline-check.sh"}
                    ],
                }
            ],
            "SessionStart": [
                {"hooks": [{"type": "command", "command": ".agent-hooks/omc-session-start.sh claude"}]}
            ],
            "SessionEnd": [
                {"hooks": [{"type": "command", "command": ".agent-hooks/omc-session-end.sh claude"}]}
            ],
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": ".agent-hooks/omc-prompt-inject.sh", "timeout": 10}]}
            ],
        }
    }

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists() and not force:
        # Merge: add hooks only if not already present
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if "hooks" not in existing:
            existing["hooks"] = {}
        hooks = existing["hooks"]
        if "PreToolUse" not in hooks:
            hooks["PreToolUse"] = omc_hooks["hooks"]["PreToolUse"]
        if "SessionStart" not in hooks:
            hooks["SessionStart"] = omc_hooks["hooks"]["SessionStart"]
        if "SessionEnd" not in hooks:
            hooks["SessionEnd"] = omc_hooks["hooks"]["SessionEnd"]
        if "UserPromptSubmit" not in hooks:
            hooks["UserPromptSubmit"] = omc_hooks["hooks"]["UserPromptSubmit"]
        settings_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    else:
        settings_path.write_text(
            json.dumps(omc_hooks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def _install_gemini_settings(settings_path: Path, *, force: bool) -> None:
    """Create or merge .gemini/settings.json with OMC BeforeTool/SessionStart/End/BeforeAgent hooks."""
    import json

    omc_hooks = {
        "hooks": {
            "BeforeTool": [
                {
                    "matcher": "write_file|replace",
                    "hooks": [
                        {
                            "type": "command",
                            "name": "omc-pipeline-check",
                            "description": "파이프라인 가드 — CONTRACT 미등록 파일 수정 차단 (exit 2)",
                            "command": ".agent-hooks/omc-pipeline-check.sh",
                            "timeout": 10000,
                        }
                    ],
                }
            ],
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "name": "omc-session-start",
                            "description": "OMC 세션 컨텍스트 자동 주입 (JSON)",
                            "command": ".agent-hooks/omc-session-start.sh gemini",
                        }
                    ]
                }
            ],
            "SessionEnd": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "name": "omc-session-end",
                            "description": "OMC session_end lifecycle 훅 실행",
                            "command": ".agent-hooks/omc-session-end.sh gemini",
                        }
                    ]
                }
            ],
            "BeforeAgent": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "name": "omc-before-agent",
                            "description": "사용자 메시지 기반 BM25 교훈 자동 주입 (JSON)",
                            "command": ".agent-hooks/omc-before-agent.sh",
                            "timeout": 10000,
                        }
                    ]
                }
            ],
        }
    }

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists() and not force:
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if "hooks" not in existing:
            existing["hooks"] = {}
        hooks = existing["hooks"]
        if "BeforeTool" not in hooks:
            hooks["BeforeTool"] = omc_hooks["hooks"]["BeforeTool"]
        if "SessionStart" not in hooks:
            hooks["SessionStart"] = omc_hooks["hooks"]["SessionStart"]
        if "SessionEnd" not in hooks:
            hooks["SessionEnd"] = omc_hooks["hooks"]["SessionEnd"]
        if "BeforeAgent" not in hooks:
            hooks["BeforeAgent"] = omc_hooks["hooks"]["BeforeAgent"]
        settings_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    else:
        settings_path.write_text(
            json.dumps(omc_hooks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


_install_claude_settings.__doc__ = (_install_claude_settings.__doc__ or "").replace(
    "HOOK_CONTRACT_SUMMARY",
    HOOK_CONTRACT_SUMMARY,
)


def _kit_root() -> Path:
    # omc_kit/scripts/install.py -> kit root is parent of scripts/
    here = Path(__file__).resolve()
    candidate = here.parents[1]
    if (candidate / "templates").is_dir():
        return candidate
    # Fallback: install.py was copied to project scripts/ — look for kit as sibling dir
    for parent in here.parents:
        nested = parent / "omc_kit"
        if (nested / "templates").is_dir():
            return nested
    return candidate

def _templates_root(kit_root: Path) -> Path:
    return kit_root / "templates"



def _check_force_regression(kit: Path, tgt: Path) -> bool:
    """--force 실행 전 kit < live 버전 회귀 위험을 감지해 사용자에게 경고.

    핵심 omc 스크립트를 샘플링해 kit이 더 오래됐으면 경고 후 확인을 요청.
    또한 templates/ ↔ live 규칙 파일 차이를 표시해 SSOT 불일치로 인한
    라이브 전용 수정사항 소실을 사전에 알린다.
    True = 계속 진행, False = 사용자가 중단 선택.
    """
    import difflib

    kit_scripts = kit / "scripts"
    live_scripts = tgt / "scripts"
    if not kit_scripts.is_dir() or not live_scripts.is_dir():
        return True

    # 핵심 스크립트 샘플링 (전부 비교하면 느림)
    _SAMPLE = ["omc_tdd_check.py", "omc_pipeline_guard.py", "omc_doctor.py", "omc_hub_push.py"]
    regressions: list[str] = []

    for name in _SAMPLE:
        kit_file = kit_scripts / name
        live_file = live_scripts / name
        if not kit_file.exists() or not live_file.exists():
            continue
        kit_lines = kit_file.read_text(encoding="utf-8").splitlines()
        live_lines = live_file.read_text(encoding="utf-8").splitlines()
        if kit_lines == live_lines:
            continue
        # 라인 수 차이로 간단 판단: kit이 더 적으면 기능이 빠진 것
        diff = len(live_lines) - len(kit_lines)
        if diff > 10:
            regressions.append(f"  {name}: live +{diff}줄 (kit이 더 오래된 버전)")

    # templates/ ↔ live 규칙 파일 SSOT 불일치 검사
    # install.py가 덮어쓸 파일 중 live에서만 수정된 내용을 사전 표시
    ssot_warnings: list[str] = []
    templates = _templates_root(kit)
    _RULE_DIRS = [
        (templates / ".cursor" / "rules", tgt / ".cursor" / "rules"),
        (templates / ".agent" / "rules", tgt / ".agent" / "rules"),
    ]
    for tmpl_dir, live_dir in _RULE_DIRS:
        if not tmpl_dir.is_dir() or not live_dir.is_dir():
            continue
        for tmpl_file in sorted(tmpl_dir.rglob("*")):
            if not tmpl_file.is_file():
                continue
            rel = tmpl_file.relative_to(tmpl_dir)
            live_file = live_dir / rel
            if not live_file.exists():
                continue
            tmpl_text = tmpl_file.read_text(encoding="utf-8")
            live_text = live_file.read_text(encoding="utf-8")
            if tmpl_text == live_text:
                continue
            live_lines_count = len(live_text.splitlines())
            tmpl_lines_count = len(tmpl_text.splitlines())
            diff_lines = live_lines_count - tmpl_lines_count
            sign = f"+{diff_lines}" if diff_lines >= 0 else str(diff_lines)
            ssot_warnings.append(
                f"  {live_file.relative_to(tgt)}: live {sign}줄 (덮어쓰면 소실)"
            )

    has_warnings = bool(regressions or ssot_warnings)
    if not has_warnings:
        return True

    if not sys.stdin.isatty():
        if regressions:
            print("[WARN] --force 버전 회귀 감지 (non-interactive 자동 진행):")
            for r in regressions:
                print(r)
        if ssot_warnings:
            print("[WARN] --force SSOT 불일치 감지 — 아래 파일의 live 수정사항이 덮어써집니다:")
            for w in ssot_warnings:
                print(w)
            print("[WARN] live → templates 동기화 후 재실행을 권장합니다.")
        print("[WARN] TTY 환경에서 재실행하면 상세 안내와 확인을 받을 수 있습니다.")
        return True

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(" ⚠️  --force 경고")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if regressions:
        print(" [버전 회귀] kit의 스크립트가 live 프로젝트보다 오래된 것 같습니다.")
        for r in regressions:
            print(r)
        print()

    if ssot_warnings:
        print(" [SSOT 불일치] 아래 파일은 live에서만 수정돼 있습니다.")
        print(" --force 실행 시 templates 버전으로 덮어써져 live 수정사항이 소실됩니다.")
        print()
        for w in ssot_warnings:
            print(w)
        print()
        print(" 권장 조치 (SSOT):")
        print("   1. live 파일을 templates/ 에 먼저 복사하세요")
        print("      예: cp .cursor/rules/omc-always.md omc_kit/templates/.cursor/rules/omc-always.md")
        print("   2. 또는: python3 scripts/omc_sync_ssot.py")
        print("   3. 그 후 install --force 재실행")
        print()

    if regressions:
        print(" 권장 조치 (버전 회귀):")
        print("   1. hub에서 최신 pull: cd /path/to/omc_kit && git pull")
        print("   2. 또는 live → hub 먼저 동기화: python3 scripts/omc_hub_push.py")
        print("   3. 그 후 install --force 재실행")
        print()

    try:
        ans = input(" 그래도 계속 진행할까요? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    return ans in ("y", "yes")


def main() -> int:
    ap = argparse.ArgumentParser(description="Install OMC kit into a target repository.")
    ap.add_argument("--target", type=Path, required=True, help="Target repository root.")
    ap.add_argument("--force", action="store_true", help="Overwrite existing files.")
    args = ap.parse_args()

    kit = _kit_root()
    templates = _templates_root(kit)
    tgt = args.target.resolve()
    force = bool(args.force)

    if force and not _check_force_regression(kit, tgt):
        print("[install] 중단됨")
        return 1

    to_copy = [
        # ── prompts ──────────────────────────────────────────────────────────
        (kit / "prompts" / "README.md", tgt / "prompts" / "README.md"),
        (kit / "prompts" / "team.json", tgt / "prompts" / "team.json"),
        (kit / "prompts" / "ROLE_ORCHESTRATOR.md", tgt / "prompts" / "ROLE_ORCHESTRATOR.md"),
        (kit / "prompts" / "MODE_AUTOPILOT.md", tgt / "prompts" / "MODE_AUTOPILOT.md"),
        (kit / "prompts" / "MODE_TEAM.md", tgt / "prompts" / "MODE_TEAM.md"),
        (kit / "prompts" / "MODE_ULTRAWORK.md", tgt / "prompts" / "MODE_ULTRAWORK.md"),
        (kit / "prompts" / "MODE_RALPH.md", tgt / "prompts" / "MODE_RALPH.md"),
        (kit / "prompts" / "MODE_DEEP_INTERVIEW.md", tgt / "prompts" / "MODE_DEEP_INTERVIEW.md"),
        (kit / "prompts" / "ROLE_SEARCH_ASSISTANT.md", tgt / "prompts" / "ROLE_SEARCH_ASSISTANT.md"),
        (kit / "prompts" / "ROLE_ANALYSIS_ASSISTANT.md", tgt / "prompts" / "ROLE_ANALYSIS_ASSISTANT.md"),
        (kit / "prompts" / "ROLE_CODE_REVIEW_ASSISTANT.md", tgt / "prompts" / "ROLE_CODE_REVIEW_ASSISTANT.md"),
        (kit / "prompts" / "ROLE_SENIOR_CODING_ASSISTANT.md", tgt / "prompts" / "ROLE_SENIOR_CODING_ASSISTANT.md"),
        # ── scripts (타겟에 배포되는 공용 스크립트) ───────────────────────────
        # kit-only (배포 안 됨): auto_prompt.py, autopilot.py, safe_trash.py,
        #   export_repo.py, test_*.py, conftest.py
        # omc_hub_push.py / omc_sync_ssot.py — omc_* 패턴으로 배포됨 (타겟→hub 역기여 지원)
        # 수동 목록 대신 glob 자동 감지 — 새 스크립트 추가 시 자동 포함됨
    ]

    # scripts: 화이트리스트 방식 — 명시된 파일만 배포 (기본값: 제외)
    # omc_*.py 전체 자동 포함 + 비-omc_ 명시 목록
    # 새 파일 추가 시 이 목록에 없으면 자동 제외됨 (안전한 기본값)
    scripts_src = kit / "scripts"
    for name in sorted(_deployed_script_names(kit)):
        to_copy.append((scripts_src / name, tgt / "scripts" / name))

    for s, d in to_copy:
        _copy(s, d, force=force)

    _copy(kit / "docs" / "omc_workflow.md", tgt / "docs" / "omc_workflow.md", force=force)
    _copy(kit / "docs" / "quickstart_kr.md", tgt / "docs" / "quickstart_kr.md", force=force)
    _copy(kit / "docs" / "kit_map.md", tgt / "docs" / "kit_map.md", force=force)
    _copy(kit / "docs" / "next_project_pack.md", tgt / "docs" / "next_project_pack.md", force=force)
    _copy(kit / "docs" / "agent_behavior.md", tgt / "docs" / "agent_behavior.md", force=force)
    _copy(kit / "docs" / "verification_checklist.md", tgt / "docs" / "verification_checklist.md", force=force)
    run_template = templates / "run"
    if run_template.exists():
        dst = tgt / "run"
        _copy(run_template, dst, force=force)
        _ensure_executable(dst)

    # Base prompt template (recommended). Only write if missing unless --force.
    prompt_common = templates / "PROMPT_COMMON.md"
    if prompt_common.exists():
        _copy(prompt_common, tgt / "PROMPT_COMMON.md", force=force)
    
    prompt_lean = templates / "PROMPT_COMMON_LEAN.md"
    if prompt_lean.exists():
        _copy(prompt_lean, tgt / "PROMPT_COMMON_LEAN.md", force=force)

    # .cursorignore — Cursor가 .agents/ 스킬을 슬래시 커맨드로 노출하지 않도록 차단
    cursorignore = templates / ".cursorignore"
    if cursorignore.exists():
        _copy(cursorignore, tgt / ".cursorignore", force=force)

    quickstart = """# OMC Kit Quickstart

## 30초 설치 후 바로 시작

```bash
# 1. 상태 확인
python3 scripts/omc_doctor.py --target .

# 2. 첫 작업 요청
python3 scripts/omc.py "만들고 싶은 것"

# 3. 스프린트 순서 (권장)
# /brainstorm → /office-hours → /plan → /task → /review → /ship → /retro
```

## 슬래시 커맨드 / 스킬

### Claude Code · Antigravity IDE (슬래시 커맨드)

| 커맨드 | 설명 |
|--------|------|
| `/brainstorm [주제]` | 요구사항 소크라테스식 탐색 |
| `/office-hours [요청]` | 제품 사고 먼저 — 6개 강제 질문 |
| `/ceo-review [모드]` | CEO 관점 기능 범위 재검토 |
| `/plan [작업]` | TDD 태스크 분해 |
| `/task [설명]` | 7단계 TDD 파이프라인 |
| `/review` | git diff 코드 리뷰 |
| `/investigate [이슈]` | 4단계 디버깅 방법론 |
| `/lesson [키워드]` | BM25 교훈 검색 |
| `/ship` | TDD 게이트 → 배포 |
| `/retro` | 회고 + 교훈 캡처 |
| `/status` | OMC 상태 확인 |

### Codex IDE (Agent Skills)

명시적: `$omc-plan`, `$omc-task`, `$omc-review` 등  
암묵적: "계획해줘", "태스크 나눠줘" 등 자연어로도 자동 트리거됩니다.

### Cursor

`.cursor/rules/omc-always.md`의 항상 적용 규칙으로 동작합니다. 슬래시 커맨드 없이 자연어로 요청하면 OMC 흐름을 따릅니다.

## 자주 쓰는 CLI 명령

```bash
python3 scripts/omc.py state status       # 현재 상태
python3 scripts/omc.py state compact      # 메모리 압축
python3 scripts/omc_lesson.py search "키워드"  # 교훈 BM25 검색
python3 scripts/omc_autopilot.py new --id feat-x --title "기능 X"
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json --dry-run
```

## 파일 지도

- `docs/kit_map.md` — 스크립트 전체 목록
- `docs/quickstart_kr.md` — 시나리오별 사용 가이드
- `docs/next_project_pack.md` — 다음 프로젝트로 이식하는 방법

"""
    _write(tgt / "docs" / "omc_quickstart.md", quickstart, force=force)

    # Bootstrap instructions for agentic tools.
    # 타겟에 파일이 이미 있으면 OMC 섹션만 추가, 없으면 전체 복사.
    # 마커가 있는 파일은 중복 추가 방지. 없는 파일은 전체 복사.
    _MERGE_MARKERS = {
        "AGENTS.md": "## OMC — Orchestrated Multi-agent Craft",
        "CLAUDE.md": "## OMC Overlay For Claude",
        "GEMINI.md": "## OMC Overlay For Gemini",
        "ETHOS.md":  "## Engineering Ethos",
        "CODEX.md":  "## OMC Overlay For Codex",
    }
    _handled: set[str] = set()

    # 마커 기반 병합 대상 (기존 파일에 섹션 추가)
    for tgt_name, marker in _MERGE_MARKERS.items():
        tgt_file = tgt / tgt_name
        tpl_file = templates / tgt_name
        if not tpl_file.exists():
            continue
        _handled.add(tgt_name)
        if tgt_file.exists() and not force:
            cur = tgt_file.read_text(encoding="utf-8")
            if marker not in cur:
                block = tpl_file.read_text(encoding="utf-8")
                tgt_file.write_text(cur.rstrip() + "\n\n" + block.rstrip() + "\n", encoding="utf-8")
        else:
            # ETHOS.md는 섹션 5에 실제 내용이 채워진 경우 --force여도 덮어쓰지 않는다.
            if tgt_name == "ETHOS.md" and tgt_file.exists() and force:
                cur = tgt_file.read_text(encoding="utf-8")
                if "___" not in cur:
                    print(f"[install] skipped {tgt_name} (섹션 5 이미 채워짐)")
                    continue
            _copy(tpl_file, tgt_file, force=force)

    # templates/ 루트의 나머지 .md 파일 자동 복사 (수동 등록 불필요)
    for tpl_file in sorted(templates.glob("*.md")):
        if tpl_file.name in _handled:
            continue
        _copy(tpl_file, tgt / tpl_file.name, force=force)

    # ── Shared agent-hooks (.agent-hooks/) ──────────────────────────────────
    agent_hooks_src = templates / ".agent-hooks"
    if agent_hooks_src.exists():
        for src in agent_hooks_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(agent_hooks_src)
            dst = tgt / ".agent-hooks" / rel
            _copy(src, dst, force=force)
            _ensure_executable(dst)

    # ── Claude Code slash commands (.claude/commands/) ───────────────────────
    claude_cmds_src = templates / ".claude" / "commands"
    if claude_cmds_src.exists():
        for src in claude_cmds_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(claude_cmds_src)
            dst = tgt / ".claude" / "commands" / rel
            _copy(src, dst, force=force)

    # ── Claude Code SessionStart/End hooks (.claude/settings.json) ──────────
    claude_settings = tgt / ".claude" / "settings.json"
    _install_claude_settings(claude_settings, force=force)

    # ── Gemini CLI commands (.gemini/commands/) ──────────────────────────────
    gemini_cmds_src = templates / ".gemini" / "commands"
    if gemini_cmds_src.exists():
        for src in gemini_cmds_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(gemini_cmds_src)
            dst = tgt / ".gemini" / "commands" / rel
            _copy(src, dst, force=force)

    # ── Codex CLI commands (.codex/commands/) ────────────────────────────────
    codex_cmds_src = templates / ".codex" / "commands"
    if codex_cmds_src.exists():
        for src in codex_cmds_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(codex_cmds_src)
            dst = tgt / ".codex" / "commands" / rel
            _copy(src, dst, force=force)

    # ── Gemini CLI SessionStart/End/BeforeAgent hooks (.gemini/settings.json) ─
    gemini_settings = tgt / ".gemini" / "settings.json"
    _install_gemini_settings(gemini_settings, force=force)

    # ── Codex CLI hooks (.codex/hooks.json) ──────────────────────────────────
    codex_hooks_src = templates / ".codex" / "hooks.json"
    if codex_hooks_src.exists():
        _copy(codex_hooks_src, tgt / ".codex" / "hooks.json", force=force)

    # ── Cursor project hooks (optional, when template files exist) ───────────
    cursor_hooks_json = templates / ".cursor" / "hooks.json"
    cursor_hook_start = templates / ".cursor" / "hooks" / "omc-session-start.sh"
    cursor_hook_end = templates / ".cursor" / "hooks" / "omc-session-end.sh"
    cursor_hook_require = templates / ".cursor" / "hooks" / "omc-require-confirm.sh"
    cursor_rules_dir = templates / ".cursor" / "rules"

    if cursor_hooks_json.exists():
        _copy(cursor_hooks_json, tgt / ".cursor" / "hooks.json", force=force)
    if cursor_hook_start.exists():
        dst = tgt / ".cursor" / "hooks" / "omc-session-start.sh"
        _copy(cursor_hook_start, dst, force=force)
        _ensure_executable(dst)
    if cursor_hook_end.exists():
        dst = tgt / ".cursor" / "hooks" / "omc-session-end.sh"
        _copy(cursor_hook_end, dst, force=force)
        _ensure_executable(dst)
    if cursor_hook_require.exists():
        dst = tgt / ".cursor" / "hooks" / "omc-require-confirm.sh"
        _copy(cursor_hook_require, dst, force=force)
        _ensure_executable(dst)

    cursor_hook_pipeline = templates / ".cursor" / "hooks" / "omc-pipeline-check.sh"
    if cursor_hook_pipeline.exists():
        dst = tgt / ".cursor" / "hooks" / "omc-pipeline-check.sh"
        _copy(cursor_hook_pipeline, dst, force=force)
        _ensure_executable(dst)

    # Cursor rules (optional)
    if cursor_rules_dir.exists():
        for src in cursor_rules_dir.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(cursor_rules_dir)
            dst = tgt / ".cursor" / "rules" / rel
            _copy(src, dst, force=force)

    # ── Agent Skills: .agents/skills/ (Codex) + .agent/skills/ (Antigravity) ──
    # Single source of truth: templates/.agents/skills/
    # Both .agents/ (Codex, plural) and .agent/ (Antigravity, singular) get identical content.
    agent_skills_src = templates / ".agents" / "skills"
    if agent_skills_src.exists():
        for src in agent_skills_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(agent_skills_src)
            for base in (".agents", ".agent"):
                dst = tgt / base / "skills" / rel
                _copy(src, dst, force=force)

    # ── Antigravity workflows (.agent/workflows/) ─────────────────────────────
    agent_workflows_src = templates / ".agent" / "workflows"
    if agent_workflows_src.exists():
        for src in agent_workflows_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(agent_workflows_src)
            dst = tgt / ".agent" / "workflows" / rel
            _copy(src, dst, force=force)

    # ── Antigravity workspace rules (.agent/rules/) ───────────────────────────
    agent_rules_src = templates / ".agent" / "rules"
    if agent_rules_src.exists():
        for src in agent_rules_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(agent_rules_src)
            dst = tgt / ".agent" / "rules" / rel
            _copy(src, dst, force=force)

    # ── .omc/lessons/ — Compound Engineering 교훈 디렉토리 ──────────────────
    lessons_dir = tgt / ".omc" / "lessons"
    if not lessons_dir.exists():
        lessons_dir.mkdir(parents=True, exist_ok=True)
        gitkeep = lessons_dir / ".gitkeep"
        gitkeep.write_text("# Compound Engineering 교훈 디렉토리\n", encoding="utf-8")
        print(f"[install] created {lessons_dir.relative_to(tgt)}/")

    # ── .omc/hooks.json — session_start 에 omc_context.py 자동 수집 포함 ────────
    omc_hooks_path = tgt / ".omc" / "hooks.json"
    import json as _json
    _default_hooks = {
        "version": 1,
        "hooks": {
            "session_start": [
                {"type": "shell", "command": "python3 scripts/omc_context.py --target ."},
                {"type": "builtin", "name": "refresh_notepad"},
                {"type": "builtin", "name": "auto_compact"},
                {
                    "type": "shell",
                    "name": "omc_status_check",
                    "command": "python3 scripts/omc.py state status",
                    "description": "새 세션 시작 시 OMC 상태 자동 출력 — LLM이 confirmed 여부를 즉시 파악",
                },
                {
                    "type": "shell",
                    "name": "role_reminder",
                    "command": "echo '[OMC] 역할 선언 필수: analysis(읽기전용) | directive(파일수정/실행) | analysis+directive(둘다)'",
                    "description": "역할 구분을 LLM에게 상기시킴",
                },
            ],
            "session_end": [
                {"type": "builtin", "name": "refresh_notepad"},
                {"type": "builtin", "name": "auto_compact"},
            ],
            "pre_compact": [{"type": "builtin", "name": "snapshot_memory"}],
            "post_compact": [{"type": "builtin", "name": "refresh_notepad"}],
        },
    }
    if not omc_hooks_path.exists() or force:
        omc_hooks_path.parent.mkdir(parents=True, exist_ok=True)
        omc_hooks_path.write_text(
            _json.dumps(_default_hooks, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[install] wrote {omc_hooks_path.relative_to(tgt)}")
    else:
        # 기존 파일에 누락된 훅을 비침습적으로 추가
        try:
            existing = _json.loads(omc_hooks_path.read_text(encoding="utf-8"))
            ss_hooks = existing.get("hooks", {}).get("session_start", [])
            changed = False

            # omc_context 훅 누락 시 추가
            has_ctx = any(h.get("command", "").startswith("python3 scripts/omc_context") for h in ss_hooks)
            if not has_ctx:
                ss_hooks.insert(0, {"type": "shell", "command": "python3 scripts/omc_context.py --target ."})
                changed = True

            # auto_compact 훅 누락 시 추가 (context 다음, status 앞)
            has_ac = any(h.get("name") == "auto_compact" for h in ss_hooks)
            if not has_ac:
                # context 훅 바로 뒤에 삽입
                ctx_idx = next(
                    (i for i, h in enumerate(ss_hooks) if h.get("command", "").startswith("python3 scripts/omc_context")),
                    0,
                )
                ss_hooks.insert(ctx_idx + 1, {"type": "builtin", "name": "auto_compact"})
                changed = True

            if changed:
                existing.setdefault("hooks", {})["session_start"] = ss_hooks
                omc_hooks_path.write_text(
                    _json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(f"[install] updated {omc_hooks_path.relative_to(tgt)} (added missing hooks)")
        except Exception:
            pass

    # Git pre-commit hook — universal physical guard (works for ALL LLMs)
    pre_commit_template = templates / "pre-commit"
    if pre_commit_template.exists():
        git_hooks_dir = tgt / ".git" / "hooks"
        if git_hooks_dir.exists():
            dst = git_hooks_dir / "pre-commit"
            _copy(pre_commit_template, dst, force=force)
            _ensure_executable(dst)
        else:
            # .git 없으면 scripts/ 에 복사해두고 안내 출력
            dst = tgt / "scripts" / "pre-commit.sample"
            _copy(pre_commit_template, dst, force=force)
            print(
                "[INFO] .git/hooks/ 폴더 없음 — pre-commit hook 을 수동으로 설치하세요:\n"
                "  git init  (아직 git repo가 아닌 경우)\n"
                "  cp scripts/pre-commit.sample .git/hooks/pre-commit\n"
                "  chmod +x .git/hooks/pre-commit\n"
                "  또는: python3 scripts/omc_doctor.py --fix"
            )

    print(f"Installed OMC kit into: {tgt}")

    _setup_ethos_section5(tgt)
    _install_shared_lessons(kit, tgt)

    return 0



def _detect_project_context(tgt: "Path") -> dict:
    """package.json / nx.json / 디렉토리 구조를 스캔해 프로젝트 맥락 자동 감지."""
    import json as _json

    ctx: dict = {"stack": "", "structure": "", "patterns": "", "forbidden": ""}

    # ── 스택 ─────────────────────────────────────────────────────────────────
    stack_parts: list = []
    pkg_path = tgt / "package.json"
    if pkg_path.exists():
        try:
            pkg = _json.loads(pkg_path.read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "typescript" in deps or (tgt / "tsconfig.json").exists():
                stack_parts.append("TypeScript")
            for fw, label in (
                ("react", "React"), ("next", "Next.js"),
                ("vue", "Vue"), ("svelte", "Svelte"),
                ("express", "Express"), ("fastify", "Fastify"),
                ("hono", "Hono"),
            ):
                if fw in deps:
                    stack_parts.append(label)
        except Exception:
            pass
    for py_file in ("pyproject.toml", "requirements.txt", "setup.py"):
        if (tgt / py_file).exists():
            stack_parts.append("Python")
            break
    for tool_file, label in (
        ("nx.json", "Nx monorepo"), ("turbo.json", "Turborepo"),
        ("lerna.json", "Lerna"), ("pnpm-workspace.yaml", "pnpm workspace"),
    ):
        if (tgt / tool_file).exists():
            stack_parts.append(label)
            break
    ctx["stack"] = " + ".join(stack_parts)

    # ── 디렉토리 구조 ────────────────────────────────────────────────────────
    structure_lines: list = []
    for dname, hint in (
        ("apps", "앱별 진입점"), ("libs", "재사용 라이브러리"),
        ("packages", "공유 패키지"), ("src", "소스 루트"),
        ("services", "백엔드 서비스"),
    ):
        d = tgt / dname
        if d.is_dir():
            subdirs = sorted(x.name for x in d.iterdir() if x.is_dir())[:5]
            sample = ", ".join(subdirs) + ("..." if len(subdirs) == 5 else "")
            if subdirs:  # 서브디렉토리 없는 빈 폴더 제외
                structure_lines.append(f"`{dname}/` — {hint} ({sample})")
    ctx["structure"] = "\n".join(f"- {s}" for s in structure_lines)

    # ── 기본 패턴 / 금지 ────────────────────────────────────────────────────
    ctx["patterns"] = "기존 파일 옆 패턴 먼저 → 같은 lib 내 패턴 → 새 패턴은 팀에 공유"
    ctx["forbidden"] = (
        "`any` 사용 금지, 컴포넌트 내 직접 API 호출 금지 (훅 분리)"
        if "TypeScript" in ctx["stack"]
        else "(직접 채우세요)"
    )
    return ctx


def _setup_ethos_section5(tgt: "Path") -> None:
    """ETHOS.md 섹션 5를 자동 감지 초안으로 채운다.

    대상 프로젝트(tgt)를 스캔해 스택·구조·패턴을 추론하고 초안을 생성.
    사용자는 Y/n/edit 중 선택만 하면 됨. 플레이스홀더가 없으면 건너뜀.
    """
    import re as _re

    ethos_path = tgt / "ETHOS.md"
    if not ethos_path.exists():
        return

    content = ethos_path.read_text(encoding="utf-8")
    if "___" not in content:
        return

    ctx = _detect_project_context(tgt)

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(" ETHOS.md 섹션 5 — 프로젝트 맥락 자동 감지")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print(f"  스택     : {ctx['stack'] or '(감지 실패 — 직접 입력 필요)'}")
    print(f"  구조     :")
    for line in ctx["structure"].splitlines():
        print(f"    {line}")
    print(f"  패턴     : {ctx['patterns']}")
    print(f"  금지     : {ctx['forbidden']}")
    print()

    try:
        ans = input("이 내용으로 채울까요? [Y/n/edit]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n[ETHOS] 건너뜀")
        return

    if ans == "n":
        print("[ETHOS] 건너뜀 — ETHOS.md를 직접 편집하세요.")
        return

    if ans == "edit":
        print("수정할 항목만 입력하세요. (Enter = 감지값 유지)")
        try:
            v = input(f"  스택 [{ctx['stack']}]: ").strip()
            if v:
                ctx["stack"] = v
            v = input(f"  패턴 [{ctx['patterns']}]: ").strip()
            if v:
                ctx["patterns"] = v
            v = input(f"  금지 [{ctx['forbidden']}]: ").strip()
            if v:
                ctx["forbidden"] = v
        except (EOFError, KeyboardInterrupt):
            print("\n[ETHOS] 건너뜀")
            return

    section5 = "\n## 5. 이 프로젝트 맥락\n\n"
    if ctx["stack"]:
        section5 += f"**스택:** {ctx['stack']}\n\n"
    if ctx["structure"]:
        section5 += f"**디렉토리 구조:**\n{ctx['structure']}\n\n"
    if ctx["patterns"]:
        section5 += f"**패턴 우선순위:** {ctx['patterns']}\n\n"
    if ctx["forbidden"]:
        section5 += f"**금지:** {ctx['forbidden']}\n"

    new_content = _re.sub(
        r"## 5\. 이 프로젝트 맥락.*?(?=\n---|\Z)",
        section5,
        content,
        flags=_re.DOTALL,
    )
    ethos_path.write_text(new_content, encoding="utf-8")
    print(f"[ETHOS] 섹션 5 저장 완료 → {ethos_path.relative_to(tgt)}")



def _install_shared_lessons(kit: Path, tgt: Path) -> None:
    """templates/shared_lessons/ 에 있는 교훈 파일을 .omc/lessons/ 에 복사한다.

    이미 존재하는 파일은 덮어쓰지 않아 프로젝트 고유 교훈을 보호한다.
    """
    shared_dir = kit / "templates" / "shared_lessons"
    if not shared_dir.is_dir():
        return

    lessons_dir = tgt / ".omc" / "lessons"
    candidates = sorted(shared_dir.glob("*.md"))
    if not candidates:
        return

    lessons_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for src in candidates:
        dst = lessons_dir / src.name
        if dst.exists():
            skipped += 1
            continue
        _copy(src, dst, force=True)
        copied += 1

    if copied or skipped:
        print(
            f"[shared_lessons] {copied}개 복사, {skipped}개 건너뜀"
            f" → {lessons_dir.relative_to(tgt)}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
