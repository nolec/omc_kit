from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "omc_quality_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("omc_quality_gate", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(root: Path, *, argv: list[str] | None = None, scope: str = "changed") -> Path:
    evidence = root / "project-manifest"
    evidence.write_text("test-command=python\n", encoding="utf-8")
    module = _load_module()
    config = {
        "schema_version": "omc-quality-gates/v1",
        "base_ref": "HEAD~1",
        "evidence": [
            {"path": "project-manifest", "sha256": module.file_sha256(evidence)}
        ],
        "gates": [
            {
                "id": "test",
                "purpose": "test",
                "argv": argv or [
                    "python3",
                    "-c",
                    "__import__('sys').stdout.write('|'.join(__import__('sys').argv[1:]))",
                    "{changed_files}",
                ],
                "scope": scope,
                "required": True,
                "timeout_sec": 30,
            }
        ],
    }
    path = root / ".omc" / "quality-gates.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_status_is_unconfigured_when_project_config_is_missing(tmp_path: Path):
    module = _load_module()

    assert module.status(tmp_path)["status"] == "unconfigured"


def test_valid_config_requires_matching_approval_receipt(tmp_path: Path):
    module = _load_module()
    _write_config(tmp_path)

    assert module.status(tmp_path)["status"] == "approval_required"


def test_approved_config_expands_changed_files_without_shell(tmp_path: Path, monkeypatch):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config_sha256 = module.canonical_file_sha256(config_path)
    module.approve(tmp_path, expected_config_sha256=config_sha256)
    monkeypatch.setattr(
        module,
        "_git_changed_files",
        lambda root, base_ref: ["src/a.py", "src/space name.py"],
    )

    result = module.run(tmp_path)

    assert result["status"] == "passed"
    assert result["gates"][0]["argv"][-2:] == ["src/a.py", "src/space name.py"]
    assert "src/a.py|src/space name.py" in result["gates"][0]["stdout"]


def test_config_change_invalidates_previous_approval(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    module.approve(tmp_path, expected_config_sha256=module.canonical_file_sha256(config_path))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"][0]["timeout_sec"] = 31
    config_path.write_text(json.dumps(config), encoding="utf-8")

    assert module.status(tmp_path)["status"] == "approval_required"


def test_evidence_change_marks_config_stale(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    module.approve(tmp_path, expected_config_sha256=module.canonical_file_sha256(config_path))
    (tmp_path / "project-manifest").write_text("changed\n", encoding="utf-8")

    assert module.status(tmp_path)["status"] == "stale"


@pytest.mark.parametrize("token", ["|", "&&", ";", "$(touch x)", "`touch x`", ">", "<"])
def test_config_rejects_shell_control_tokens(tmp_path: Path, token: str):
    module = _load_module()
    _write_config(tmp_path, argv=["python3", token])

    with pytest.raises(module.QualityGateError, match="unsafe argv token"):
        module.load_config(tmp_path)


def test_full_scope_requires_separate_approval(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path, scope="full")
    config_sha256 = module.canonical_file_sha256(config_path)
    module.approve(tmp_path, expected_config_sha256=config_sha256)

    assert module.status(tmp_path)["status"] == "full_scope_approval_required"

    module.approve(tmp_path, expected_config_sha256=config_sha256, allow_full=True)
    assert module.status(tmp_path)["status"] == "ready"


def test_status_cli_fails_while_approval_is_required(tmp_path: Path, capsys):
    module = _load_module()
    _write_config(tmp_path)

    assert module.main(["--target", str(tmp_path), "status"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "approval_required"


def test_status_cli_fails_while_full_scope_approval_is_required(tmp_path: Path, capsys):
    module = _load_module()
    config_path = _write_config(tmp_path, scope="full")
    module.approve(tmp_path, expected_config_sha256=module.canonical_file_sha256(config_path))

    assert module.main(["--target", str(tmp_path), "status"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "full_scope_approval_required"


def test_changed_scope_requires_changed_files_placeholder(tmp_path: Path):
    module = _load_module()
    _write_config(tmp_path, argv=["python3", "-m", "pytest"], scope="changed")

    with pytest.raises(module.QualityGateError, match="changed scope"):
        module.load_config(tmp_path)


def test_affected_scope_requires_base_and_head_placeholders(tmp_path: Path):
    module = _load_module()
    _write_config(tmp_path, argv=["quality-check", "{base_ref}"], scope="affected")

    with pytest.raises(module.QualityGateError, match="affected scope"):
        module.load_config(tmp_path)


def test_git_changed_files_include_committed_staged_and_unstaged_changes(tmp_path: Path):
    module = _load_module()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "quality-gate@example.com")
    git("config", "user.name", "Quality Gate")
    (tmp_path / "committed.txt").write_text("base\n", encoding="utf-8")
    (tmp_path / "staged.txt").write_text("base\n", encoding="utf-8")
    (tmp_path / "unstaged.txt").write_text("base\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "base")
    (tmp_path / "committed.txt").write_text("changed\n", encoding="utf-8")
    git("add", "committed.txt")
    git("commit", "-qm", "committed change")
    (tmp_path / "staged.txt").write_text("changed\n", encoding="utf-8")
    git("add", "staged.txt")
    (tmp_path / "unstaged.txt").write_text("changed\n", encoding="utf-8")

    assert module._git_changed_files(tmp_path, "HEAD~1") == [
        "committed.txt",
        "staged.txt",
        "unstaged.txt",
    ]


def test_run_cli_rejects_changed_file_override(tmp_path: Path):
    module = _load_module()

    with pytest.raises(SystemExit):
        module.main(
            [
                "--target",
                str(tmp_path),
                "run",
                "--changed-file",
                "only-this-file.py",
            ]
        )


def test_changed_gate_is_skipped_without_changed_files(tmp_path: Path, monkeypatch):
    module = _load_module()
    marker = tmp_path / "executed"
    config_path = _write_config(
        tmp_path,
        argv=[
            "python3",
            "-c",
            "__import__('pathlib').Path('executed').write_text('ran')",
            "{changed_files}",
        ],
    )
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "passed"
    assert result["gates"][0]["status"] == "skipped"
    assert not marker.exists()


def test_run_keeps_the_approved_config_snapshot(tmp_path: Path, monkeypatch):
    module = _load_module()
    marker = tmp_path / "unapproved-command-ran"
    config_path = _write_config(tmp_path)
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
    )
    original_status = module.status

    def replace_config_after_status(root: Path):
        result = original_status(root)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["gates"][0]["argv"] = [
            "python3",
            "-c",
            "__import__('pathlib').Path('unapproved-command-ran').write_text('ran')",
            "{changed_files}",
        ]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return result

    monkeypatch.setattr(module, "status", replace_config_after_status)
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: ["src/a.py"])

    result = module.run(tmp_path)

    assert result["status"] == "passed"
    assert not marker.exists()


def test_changed_file_starting_with_dash_is_passed_as_a_path(tmp_path: Path, monkeypatch):
    module = _load_module()
    config_path = _write_config(tmp_path)
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: ["--help"])

    result = module.run(tmp_path)

    assert result["status"] == "passed"
    assert result["gates"][0]["argv"][-1] == "./--help"
    assert "./--help" in result["gates"][0]["stdout"]


def test_timeout_bytes_are_returned_as_blocked_json(tmp_path: Path, monkeypatch, capsys):
    module = _load_module()
    config_path = _write_config(tmp_path)
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: ["src/a.py"])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=1,
            output=b"partial\xff",
            stderr=b"warning\xff",
        )

    monkeypatch.setattr(module.subprocess, "run", timeout)

    assert module.main(["--target", str(tmp_path), "run"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["gates"][0]["status"] == "timeout"
    assert result["gates"][0]["stdout"] == "partial�"
    assert result["gates"][0]["stderr"] == "warning�"


def test_missing_executable_returns_blocked_gate_result(tmp_path: Path, monkeypatch):
    module = _load_module()
    config_path = _write_config(
        tmp_path,
        argv=["definitely-missing-omc-command", "{changed_files}"],
    )
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: ["src/a.py"])

    result = module.run(tmp_path)

    assert result["status"] == "blocked"
    assert result["gates"][0]["status"] == "execution_error"


def test_proposal_requires_gate_specific_evidence(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal = {
        "schema_version": "omc-quality-gate-proposal/v1",
        "config": config,
        "rationale": [],
    }

    with pytest.raises(module.QualityGateError, match="missing rationale"):
        module.validate_proposal(proposal, tmp_path)


def test_proposal_accepts_evidence_backed_gate(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal = {
        "schema_version": "omc-quality-gate-proposal/v1",
        "config": config,
        "rationale": [
            {
                "gate_id": "test",
                "evidence_paths": ["project-manifest"],
                "scope_reason": "manifest exposes a file-scoped test command",
            }
        ],
    }

    assert module.validate_proposal(proposal, tmp_path)["schema_version"] == "omc-quality-gate-proposal/v1"


def test_all_llm_ship_surfaces_reference_shared_proposal_contract():
    paths = [
        ROOT / ".agents/skills/omc-ship/SKILL.md",
        ROOT / "templates/.agents/skills/omc-ship/SKILL.md",
        ROOT / "templates/.claude/commands/ship.md",
        ROOT / "templates/.gemini/commands/omc-commands.md",
    ]

    for path in paths:
        assert "docs/omc_quality_gates.md" in path.read_text(encoding="utf-8"), path


def test_shared_proposal_contract_is_tool_neutral():
    text = (ROOT / "docs/omc_quality_gates.md").read_text(encoding="utf-8")

    assert "omc-quality-gate-proposal/v1" in text
    assert "승인 전 실행 금지" in text
    assert "CI 설정" in text
    assert "프로젝트 manifest" in text
    for tool_name in ("Nx", "Jest", "Pytest"):
        assert tool_name not in text
