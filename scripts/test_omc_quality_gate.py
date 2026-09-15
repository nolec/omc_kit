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

    assert module.status(tmp_path)["status"] == "approval_stale"


def test_proposal_apply_requires_explicit_absent_expectation(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_path.unlink()
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": "omc-quality-gate-proposal/v1",
                "config": config,
                "rationale": [
                    {
                        "gate_id": "test",
                        "evidence_paths": ["project-manifest"],
                        "scope_reason": "manifest defines a changed-file gate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.QualityGateError, match="expect-absent"):
        module.apply_proposal(tmp_path, proposal_path)


def test_proposal_apply_writes_config_and_requires_separate_approval(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_path.unlink()
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": "omc-quality-gate-proposal/v1",
                "config": config,
                "rationale": [
                    {
                        "gate_id": "test",
                        "evidence_paths": ["project-manifest"],
                        "scope_reason": "manifest defines a changed-file gate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = module.apply_proposal(tmp_path, proposal_path, expect_absent=True)

    assert result["status"] == "applied"
    assert result["config_sha256"] == module.canonical_file_sha256(config_path)
    assert module.status(tmp_path)["status"] == "approval_required"


def test_proposal_apply_rejects_changed_existing_config(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    current_hash = module.canonical_file_sha256(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"][0]["timeout_sec"] = 31
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": "omc-quality-gate-proposal/v1",
                "config": config,
                "rationale": [
                    {
                        "gate_id": "test",
                        "evidence_paths": ["project-manifest"],
                        "scope_reason": "manifest defines a changed-file gate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.QualityGateError, match="current config sha256"):
        module.apply_proposal(
            tmp_path,
            proposal_path,
            expected_current_sha256="0" * 64,
        )

    assert module.canonical_file_sha256(config_path) == current_hash


def test_proposal_apply_is_idempotent_for_identical_config(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": "omc-quality-gate-proposal/v1",
                "config": config,
                "rationale": [
                    {
                        "gate_id": "test",
                        "evidence_paths": ["project-manifest"],
                        "scope_reason": "manifest defines a changed-file gate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = module.apply_proposal(tmp_path, proposal_path)

    assert result == {
        "status": "unchanged",
        "config_sha256": module.canonical_file_sha256(config_path),
    }


def test_proposal_apply_cli_uses_compare_and_swap_contract(tmp_path: Path, capsys):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_path.unlink()
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": "omc-quality-gate-proposal/v1",
                "config": config,
                "rationale": [
                    {
                        "gate_id": "test",
                        "evidence_paths": ["project-manifest"],
                        "scope_reason": "manifest defines a changed-file gate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert module.main(
        [
            "--target",
            str(tmp_path),
            "proposal-apply",
            str(proposal_path),
            "--expect-absent",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"


def test_invalid_config_status_exposes_raw_file_sha256(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config_path.write_text("{broken", encoding="utf-8")

    result = module.status(tmp_path)

    assert result == {
        "status": "invalid",
        "reason": "quality gate config is invalid JSON",
        "config_file_sha256": module.file_sha256(config_path),
    }


def test_proposal_apply_rejects_changed_invalid_config(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": "omc-quality-gate-proposal/v1",
                "config": config,
                "rationale": [
                    {
                        "gate_id": "test",
                        "evidence_paths": ["project-manifest"],
                        "scope_reason": "manifest defines a changed-file gate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(module.QualityGateError, match="config file sha256 does not match"):
        module.apply_proposal(
            tmp_path,
            proposal_path,
            expected_current_file_sha256="0" * 64,
        )

    assert config_path.read_text(encoding="utf-8") == "{broken"


def test_proposal_apply_replaces_invalid_config_with_matching_raw_sha256(tmp_path: Path):
    module = _load_module()
    config_path = _write_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": "omc-quality-gate-proposal/v1",
                "config": config,
                "rationale": [
                    {
                        "gate_id": "test",
                        "evidence_paths": ["project-manifest"],
                        "scope_reason": "manifest defines a changed-file gate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text("{broken", encoding="utf-8")
    current_file_sha256 = module.file_sha256(config_path)

    result = module.apply_proposal(
        tmp_path,
        proposal_path,
        expected_current_file_sha256=current_file_sha256,
    )

    assert result["status"] == "applied"
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
    assert result["reason"] == "environment_not_ready"
    assert result["missing_executables"] == ["definitely-missing-omc-command"]
    assert result["gates"] == []


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


def test_proposal_rejects_host_bound_user_runtime_path_but_legacy_config_loads(
    tmp_path: Path,
):
    module = _load_module()
    config_path = _write_config(
        tmp_path,
        argv=[
            "/usr/bin/env",
            "PATH=/Users/alice/.local/project-runtime/bin:/usr/bin:/bin",
            "project-test",
            "{changed_files}",
        ],
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal = {
        "schema_version": "omc-quality-gate-proposal/v1",
        "config": config,
        "rationale": [
            {
                "gate_id": "test",
                "evidence_paths": ["project-manifest"],
                "scope_reason": "legacy project command",
            }
        ],
    }

    assert module.load_config(tmp_path) == config
    with pytest.raises(module.QualityGateError, match="config_not_portable"):
        module.validate_proposal(proposal, tmp_path)


@pytest.mark.parametrize(
    "token",
    [
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "/home/alice/project-runtime/bin/test",
        "~/project-runtime/bin/test",
        "~alice/project-runtime/bin/test",
        "$HOME/project-runtime/bin/test",
        "$NODE_HOME/bin/node",
        "$PATH/project-tool",
    ],
)
def test_proposal_rejects_machine_environment_bindings(tmp_path: Path, token: str):
    module = _load_module()
    config_path = _write_config(tmp_path, argv=[token, "{changed_files}"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal = {
        "schema_version": "omc-quality-gate-proposal/v1",
        "config": config,
        "rationale": [
            {
                "gate_id": "test",
                "evidence_paths": ["project-manifest"],
                "scope_reason": "machine-bound command",
            }
        ],
    }

    with pytest.raises(module.QualityGateError, match="config_not_portable"):
        module.validate_proposal(proposal, tmp_path)


@pytest.mark.parametrize(
    "executable",
    [
        "/opt/homebrew/bin/pnpm",
        "/usr/local/bin/node",
        "/private/tmp/project-tool",
    ],
)
def test_proposal_rejects_absolute_executable_but_legacy_config_loads(
    tmp_path: Path, executable: str
):
    module = _load_module()
    config_path = _write_config(tmp_path, argv=[executable, "{changed_files}"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal = {
        "schema_version": "omc-quality-gate-proposal/v1",
        "config": config,
        "rationale": [
            {
                "gate_id": "test",
                "evidence_paths": ["project-manifest"],
                "scope_reason": "machine-specific executable",
            }
        ],
    }

    assert module.load_config(tmp_path) == config
    with pytest.raises(module.QualityGateError, match="config_not_portable"):
        module.validate_proposal(proposal, tmp_path)


@pytest.mark.parametrize("wrapper", ["env", "/usr/bin/env"])
def test_proposal_rejects_env_wrapper_but_legacy_config_loads(
    tmp_path: Path, wrapper: str
):
    module = _load_module()
    config_path = _write_config(
        tmp_path, argv=[wrapper, "PORTABLE_FLAG=1", "python3", "{changed_files}"]
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal = {
        "schema_version": "omc-quality-gate-proposal/v1",
        "config": config,
        "rationale": [
            {
                "gate_id": "test",
                "evidence_paths": ["project-manifest"],
                "scope_reason": "legacy env wrapper",
            }
        ],
    }

    assert module.load_config(tmp_path) == config
    with pytest.raises(module.QualityGateError, match="config_not_portable"):
        module.validate_proposal(proposal, tmp_path)


@pytest.mark.parametrize("wrapper", ["sh", "bash", "zsh", "fish", "pwsh"])
def test_proposal_rejects_shell_wrapper_but_legacy_config_loads(
    tmp_path: Path, wrapper: str
):
    module = _load_module()
    config_path = _write_config(
        tmp_path, argv=[wrapper, "-c", "project-test", "{changed_files}"]
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal = {
        "schema_version": "omc-quality-gate-proposal/v1",
        "config": config,
        "rationale": [
            {
                "gate_id": "test",
                "evidence_paths": ["project-manifest"],
                "scope_reason": "legacy shell wrapper",
            }
        ],
    }

    assert module.load_config(tmp_path) == config
    with pytest.raises(module.QualityGateError, match="config_not_portable"):
        module.validate_proposal(proposal, tmp_path)


@pytest.mark.parametrize("executable", ["python3", "scripts/project-check"])
def test_proposal_accepts_path_resolved_or_project_relative_executable(
    tmp_path: Path, executable: str
):
    module = _load_module()
    config_path = _write_config(tmp_path, argv=[executable, "{changed_files}"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    proposal = {
        "schema_version": "omc-quality-gate-proposal/v1",
        "config": config,
        "rationale": [
            {
                "gate_id": "test",
                "evidence_paths": ["project-manifest"],
                "scope_reason": "portable executable",
            }
        ],
    }

    assert module.validate_proposal(proposal, tmp_path) == proposal


def test_failed_required_preflight_skips_product_gates(tmp_path: Path, monkeypatch):
    module = _load_module()
    marker = tmp_path / "product-gate-ran"
    config_path = _write_config(tmp_path, scope="full")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"] = [
        {
            "id": "runtime",
            "purpose": "preflight",
            "argv": ["python3", "-c", "raise SystemExit(9)"],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
        {
            "id": "test",
            "purpose": "test",
            "argv": [
                "python3",
                "-c",
                "__import__('pathlib').Path('product-gate-ran').write_text('ran')",
            ],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
    ]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "blocked"
    assert result["gates"][0]["status"] == "failed"
    assert result["gates"][1]["status"] == "skipped"
    assert result["gates"][1]["reason"] == "preflight_failed"
    assert not marker.exists()


def test_missing_executable_is_detected_before_any_gate_runs(tmp_path: Path, monkeypatch):
    module = _load_module()
    marker = tmp_path / "first-gate-ran"
    config_path = _write_config(tmp_path, scope="full")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"] = [
        {
            "id": "test",
            "purpose": "test",
            "argv": [
                "python3",
                "-c",
                "__import__('pathlib').Path('first-gate-ran').write_text('ran')",
            ],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
        {
            "id": "build",
            "purpose": "build",
            "argv": ["definitely-missing-portable-ship-command"],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
    ]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "blocked"
    assert result["reason"] == "environment_not_ready"
    assert result["missing_executables"] == [
        "definitely-missing-portable-ship-command"
    ]
    assert result["gates"] == []
    assert not marker.exists()


def test_env_wrapped_missing_executable_is_detected_before_any_gate_runs(
    tmp_path: Path, monkeypatch
):
    module = _load_module()
    marker = tmp_path / "first-gate-ran"
    config_path = _write_config(tmp_path, scope="full")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"] = [
        {
            "id": "test",
            "purpose": "test",
            "argv": [
                "python3",
                "-c",
                "__import__('pathlib').Path('first-gate-ran').write_text('ran')",
            ],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
        {
            "id": "build",
            "purpose": "build",
            "argv": [
                "/usr/bin/env",
                "PORTABLE_FLAG=1",
                "definitely-missing-env-wrapped-command",
            ],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
    ]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "blocked"
    assert result["reason"] == "environment_not_ready"
    assert result["missing_executables"] == [
        "definitely-missing-env-wrapped-command"
    ]
    assert result["gates"] == []
    assert not marker.exists()


@pytest.mark.parametrize(
    "env_options",
    [["-i"], ["-u", "PATH"], ["--unset=PATH"]],
)
def test_env_wrapper_path_removal_uses_effective_command_search_path(
    tmp_path: Path, monkeypatch, env_options: list[str]
):
    module = _load_module()
    marker = tmp_path / "first-gate-ran"
    tool_dir = tmp_path / "caller-only-bin"
    tool_dir.mkdir()
    executable = tool_dir / "caller-only-command"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv(
        "PATH", f"{tool_dir}{module.os.pathsep}{module.os.environ['PATH']}"
    )
    config_path = _write_config(tmp_path, scope="full")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"] = [
        {
            "id": "test",
            "purpose": "test",
            "argv": [
                module.sys.executable,
                "-c",
                "__import__('pathlib').Path('first-gate-ran').write_text('ran')",
            ],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
        {
            "id": "build",
            "purpose": "build",
            "argv": ["/usr/bin/env", *env_options, executable.name],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
    ]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "blocked"
    assert result["reason"] == "environment_not_ready"
    assert result["missing_executables"] == [executable.name]
    assert result["gates"] == []
    assert not marker.exists()


def test_env_wrapper_explicit_path_restores_command_search_after_ignore_environment(
    tmp_path: Path,
):
    module = _load_module()

    assert module._declared_executables(
        tmp_path,
        ["/usr/bin/env", "-i", "PATH=project-bin", "project-command"],
    ) == (
        [
            ("/usr/bin/env", None, tmp_path),
            ("project-command", "project-bin", tmp_path),
        ],
        [],
    )


@pytest.mark.parametrize(
    "env_argv, executable_path",
    [
        (["PATH=project-bin", "project-command"], "project-bin/project-command"),
        (
            ["-C", "nested", "PATH=project-bin", "project-command"],
            "nested/project-bin/project-command",
        ),
        (["-C", "nested", "project-bin/project-command"], "nested/project-bin/project-command"),
    ],
)
def test_env_wrapper_resolves_relative_search_locations_from_effective_cwd(
    tmp_path: Path, monkeypatch, env_argv: list[str], executable_path: str
):
    module = _load_module()
    executable = tmp_path / executable_path
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    config_path = _write_config(
        tmp_path,
        argv=["/usr/bin/env", *env_argv],
        scope="full",
    )
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "passed"
    assert result["gates"][0]["status"] == "passed"


def test_missing_env_chdir_is_rejected_before_any_gate_runs(
    tmp_path: Path, monkeypatch
):
    module = _load_module()
    marker = tmp_path / "first-gate-ran"
    config_path = _write_config(tmp_path, scope="full")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"] = [
        {
            "id": "test",
            "purpose": "test",
            "argv": [
                module.sys.executable,
                "-c",
                "__import__('pathlib').Path('first-gate-ran').write_text('ran')",
            ],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
        {
            "id": "build",
            "purpose": "build",
            "argv": ["/usr/bin/env", "-C", "missing-directory", "/bin/true"],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
    ]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "blocked"
    assert result["reason"] == "environment_not_ready"
    assert result["missing_working_directories"] == ["missing-directory"]
    assert result["gates"] == []
    assert not marker.exists()


def test_env_option_after_assignment_is_treated_as_utility_before_any_gate_runs(
    tmp_path: Path, monkeypatch
):
    module = _load_module()
    marker = tmp_path / "first-gate-ran"
    config_path = _write_config(tmp_path, scope="full")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"] = [
        {
            "id": "test",
            "purpose": "test",
            "argv": [
                module.sys.executable,
                "-c",
                "__import__('pathlib').Path('first-gate-ran').write_text('ran')",
            ],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
        {
            "id": "build",
            "purpose": "build",
            "argv": ["/usr/bin/env", "FLAG=1", "-i", module.sys.executable],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
    ]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "blocked"
    assert result["reason"] == "environment_not_ready"
    assert result["missing_executables"] == ["-i"]
    assert result["gates"] == []
    assert not marker.exists()


def test_env_double_dash_still_allows_assignments_before_utility(
    tmp_path: Path, monkeypatch
):
    module = _load_module()
    config_path = _write_config(
        tmp_path,
        argv=["/usr/bin/env", "--", "FLAG=1", module.sys.executable, "-c", "pass"],
        scope="full",
    )
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    result = module.run(tmp_path)

    assert result["status"] == "passed"
    assert result["gates"][0]["status"] == "passed"


@pytest.mark.parametrize(
    "argv",
    [
        ["/usr/bin/env", "-S", "'unterminated"],
        ["/usr/bin/env", "-u"],
        ["/usr/bin/env", "--unset"],
        ["/usr/bin/env", "-C"],
        ["/usr/bin/env", "--chdir"],
        ["/usr/bin/env", "-P"],
    ],
)
def test_malformed_env_wrapper_is_rejected_before_any_gate_runs(
    tmp_path: Path, monkeypatch, argv: list[str]
):
    module = _load_module()
    marker = tmp_path / "first-gate-ran"
    config_path = _write_config(tmp_path, scope="full")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"] = [
        {
            "id": "test",
            "purpose": "test",
            "argv": [
                "python3",
                "-c",
                "__import__('pathlib').Path('first-gate-ran').write_text('ran')",
            ],
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
        {
            "id": "build",
            "purpose": "build",
            "argv": argv,
            "scope": "full",
            "required": True,
            "timeout_sec": 30,
        },
    ]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
        allow_full=True,
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: [])

    with pytest.raises(module.QualityGateError, match="environment_not_ready"):
        module.run(tmp_path)

    assert not marker.exists()


def test_optional_missing_executable_keeps_legacy_nonblocking_behavior(
    tmp_path: Path, monkeypatch
):
    module = _load_module()
    config_path = _write_config(
        tmp_path,
        argv=["definitely-missing-optional-command", "{changed_files}"],
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["gates"][0]["required"] = False
    config_path.write_text(json.dumps(config), encoding="utf-8")
    module.approve(
        tmp_path,
        expected_config_sha256=module.canonical_file_sha256(config_path),
    )
    monkeypatch.setattr(module, "_git_changed_files", lambda root, base_ref: ["x"])

    result = module.run(tmp_path)

    assert result["status"] == "passed"
    assert result["gates"][0]["status"] == "execution_error"


def test_all_llm_ship_surfaces_reference_shared_proposal_contract():
    paths = [
        ROOT / ".agents/skills/omc-ship/SKILL.md",
        ROOT / "templates/.agents/skills/omc-ship/SKILL.md",
        ROOT / "templates/.claude/commands/ship.md",
        ROOT / "templates/.gemini/commands/omc-commands.md",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "docs/omc_quality_gates.md" in text, path
        assert "omc_quality_gate.py --target . status" in text, path
        assert "omc_quality_gate.py --target . run" in text, path
        assert "omc_tdd_check.py --run-tests" not in text, path


def test_shared_proposal_contract_is_tool_neutral():
    text = (ROOT / "docs/omc_quality_gates.md").read_text(encoding="utf-8")

    assert "omc-quality-gate-proposal/v1" in text
    assert "승인 전 실행 금지" in text
    assert "CI 설정" in text
    assert "프로젝트 manifest" in text
    for tool_name in ("Nx", "Jest", "Pytest"):
        assert tool_name not in text


def test_ship_surfaces_explain_portable_environment_preflight():
    contract = (ROOT / "docs/omc_quality_gates.md").read_text(encoding="utf-8")

    for phrase in (
        "config_not_portable",
        "environment_not_ready",
        "preflight",
        "runtime을 자동 설치하지",
    ):
        assert phrase in contract
    for path in (
        ROOT / ".agents/skills/omc-ship/SKILL.md",
        ROOT / "templates/.agents/skills/omc-ship/SKILL.md",
        ROOT / "templates/.claude/commands/ship.md",
        ROOT / "templates/.gemini/commands/omc-commands.md",
    ):
        surface = path.read_text(encoding="utf-8")
        assert "environment_not_ready" in surface, path
        assert "품질 명령" in surface, path


def test_tdd_compatibility_path_does_not_invoke_framework_commands_directly():
    text = (ROOT / "scripts/omc_tdd_check.py").read_text(encoding="utf-8")

    for fragment in ("npx nx", "npx jest", "pytest --"):
        assert fragment not in text
