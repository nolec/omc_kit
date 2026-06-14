"""
test_omc_doctor_hook_block.py — omc_doctor.py hook_block 검사 항목 테스트

검증 항목:
  1. omc_doctor.py에 훅 차단 로직 검사 항목이 존재한다
  2. pre-commit에 pipeline_guard CONTRACT 검증이 있으면 OK 판정
  3. pre-commit에 pipeline_guard가 없으면 WARN 판정
  4. .agent-hooks/omc-pipeline-check.sh에 exit 2 차단 코드가 있으면 OK 판정
  5. .agent-hooks/omc-pipeline-check.sh가 없으면 WARN 판정
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_PATH = ROOT / "scripts" / "omc_doctor.py"


def _load_doctor():
    spec = importlib.util.spec_from_file_location("omc_doctor", DOCTOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────
# 1. omc_doctor.py에 hook_block 검사 코드 존재
# ─────────────────────────────────────────────
def test_doctor_source_has_hook_block_check():
    content = DOCTOR_PATH.read_text(encoding="utf-8")
    assert "hook_block" in content or "pipeline_guard" in content, \
        "omc_doctor.py에 pipeline_guard / hook_block 검사가 없음"


# ─────────────────────────────────────────────
# 2. pre-commit에 pipeline_guard 있으면 OK
# ─────────────────────────────────────────────
def test_doctor_check_reports_ok_when_precommit_has_pipeline_guard(tmp_path):
    git_hooks = tmp_path / "dot_git" / "hooks"
    git_hooks.mkdir(parents=True)
    hook = git_hooks / "pre-commit"
    hook.write_text("#!/bin/sh\npython3 scripts/omc_pipeline_guard.py status\npython3 scripts/omc_tdd_check.py --staged\n")
    hook.chmod(0o755)

    # _build_checks는 root / ".git" / "hooks" / "pre-commit"을 읽으므로
    # symlink로 연결
    git_link = tmp_path / ".git"
    git_link.symlink_to(tmp_path / "dot_git")

    doctor = _load_doctor()
    checks = doctor.run_checks(tmp_path)
    hook_check = next((c for c in checks if "pipeline_guard" in c.label and "CONTRACT" in c.label), None)
    assert hook_check is not None, "pipeline_guard CONTRACT 관련 검사 항목이 없음"
    assert hook_check.ok, f"pipeline_guard 있는 pre-commit인데 WARN: {hook_check.label}"


# ─────────────────────────────────────────────
# 3. pre-commit에 pipeline_guard 없으면 WARN
# ─────────────────────────────────────────────
def test_doctor_check_warns_when_precommit_missing_pipeline_guard(tmp_path):
    git_hooks = tmp_path / "dot_git" / "hooks"
    git_hooks.mkdir(parents=True)
    hook = git_hooks / "pre-commit"
    hook.write_text("#!/bin/sh\npython3 scripts/omc_tdd_check.py --staged\n")
    hook.chmod(0o755)

    git_link = tmp_path / ".git"
    git_link.symlink_to(tmp_path / "dot_git")

    doctor = _load_doctor()
    checks = doctor.run_checks(tmp_path)
    hook_check = next((c for c in checks if "pipeline_guard" in c.label and "CONTRACT" in c.label), None)
    assert hook_check is not None, "pipeline_guard CONTRACT 관련 검사 항목이 없음"
    assert not hook_check.ok, "pipeline_guard 없는 pre-commit인데 OK 판정됨"


# ─────────────────────────────────────────────
# 4. .agent-hooks/omc-pipeline-check.sh 존재 + exit 2 있으면 OK
# ─────────────────────────────────────────────
def test_doctor_check_reports_ok_when_agent_hook_has_exit2(tmp_path):
    agent_hooks = tmp_path / ".agent-hooks"
    agent_hooks.mkdir()
    hook = agent_hooks / "omc-pipeline-check.sh"
    hook.write_text("#!/bin/sh\n# Claude Code PreToolUse\nexit 2\n")
    hook.chmod(0o755)

    doctor = _load_doctor()
    checks = doctor.run_checks(tmp_path)
    agent_hook_check = next(
        (c for c in checks if "agent-hooks" in c.label and "omc-pipeline-check" in c.label),
        None,
    )
    assert agent_hook_check is not None, "agent-hooks/omc-pipeline-check 관련 검사 항목이 없음"
    assert agent_hook_check.ok, f"exit 2 있는 훅인데 WARN: {agent_hook_check.label}"


# ─────────────────────────────────────────────
# 5. .agent-hooks/omc-pipeline-check.sh 없으면 WARN
# ─────────────────────────────────────────────
def test_doctor_check_warns_when_agent_hook_missing(tmp_path):
    doctor = _load_doctor()
    checks = doctor.run_checks(tmp_path)
    agent_hook_check = next(
        (c for c in checks if "agent-hooks" in c.label and "omc-pipeline-check" in c.label),
        None,
    )
    assert agent_hook_check is not None, "agent-hooks/omc-pipeline-check 관련 검사 항목이 없음"
    assert not agent_hook_check.ok, ".agent-hooks 없는데 OK 판정됨"


def test_doctor_has_codex_posttooluse_soft_guard_check(tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text(
        '{"hooks":{"SessionStart":[],"UserPromptSubmit":[],"PostToolUse":[{"hooks":[{"command":"omc-post-file-check.sh"}]}]}}',
        encoding="utf-8",
    )

    doctor = _load_doctor()
    checks = doctor.run_checks(tmp_path)
    codex_soft_guard_check = next(
        (c for c in checks if ".codex/hooks.json" in c.label and "PostToolUse" in c.label),
        None,
    )
    assert codex_soft_guard_check is not None, "Codex PostToolUse 소프트 가드 검사 항목이 없음"
    assert codex_soft_guard_check.ok, "Codex PostToolUse 소프트 가드가 있는데도 OK 판정이 아님"
