from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_provider_transport_probe as probe_module
from omc_provider_transport_probe import (
    _evidence_hash,
    _load_private_key,
    _seal_evidence,
    probe_transport,
    validate_probe_evidence,
)


SCRIPT = Path(__file__).with_name("omc_provider_transport_probe.py")
TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_PUBLIC_KEY = base64.b64encode(
    TEST_PRIVATE_KEY.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
).decode("ascii")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _probe(path: Path, *, expected_sha256: str | None = None) -> dict[str, object]:
    return probe_transport(
        path,
        expected_transport_sha256=expected_sha256 or _sha256_file(path),
        signer_private_key=TEST_PRIVATE_KEY,
    )


def _validate(evidence: dict[str, object]) -> dict[str, object]:
    return validate_probe_evidence(
        evidence,
        trusted_signer_public_keys={TEST_PUBLIC_KEY},
    )


def _resign(evidence: dict[str, object]) -> None:
    _seal_evidence(evidence, TEST_PRIVATE_KEY)


def _write_private_key(path: Path) -> None:
    raw = TEST_PRIVATE_KEY.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    path.write_text(base64.b64encode(raw).decode("ascii"), encoding="utf-8")


def _write_transport(
    path: Path,
    *,
    capability_returncode: int = 0,
    capabilities: dict[str, object] | None = None,
    mutate_after_capabilities: bool = False,
    overflow_version: bool = False,
    overflow_stderr: bool = False,
    slow_version: bool = False,
    child_pid_path: Path | None = None,
    self_test_available: bool = True,
    environment_path: Path | None = None,
    cwd_path: Path | None = None,
) -> None:
    payload = capabilities or {
        "schema_version": "omc-provider-transport-capabilities/v1",
        "exact_pre_call_input_count": True,
        "cached_input_included": True,
        "reasoning_tokens_reported": True,
        "native_output_token_cap": True,
        "raw_usage_event_stream": True,
    }
    mutation_block = ""
    if mutate_after_capabilities:
        mutation_block = (
            "    source = Path(__file__)\n"
            "    source.write_text(source.read_text() + '\\n# changed\\n')\n"
        )
    version_block = "    print('transport 1.2.3')\n"
    environment_block = ""
    if environment_path is not None:
        environment_block = (
            f"Path({str(environment_path)!r}).write_text("
            "'present' if os.environ.get('OPENAI_API_KEY') else 'absent')\n"
        )
    if cwd_path is not None:
        environment_block += f"Path({str(cwd_path)!r}).write_text(os.getcwd())\n"
    if child_pid_path is not None:
        version_block = (
            "    child = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(30)'])\n"
            f"    Path({str(child_pid_path)!r}).write_text(str(child.pid))\n"
        )
    elif overflow_version:
        stream = "sys.stderr" if overflow_stderr else "sys.stdout"
        version_block = (
            f"    {stream}.write('x' * 70000)\n"
            f"    {stream}.flush()\n"
            "    time.sleep(5)\n"
        )
    elif slow_version:
        version_block = "    time.sleep(5)\n"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"payload = {payload!r}\n"
        f"{environment_block}"
        "if sys.argv[1:] == ['--version']:\n"
        f"{version_block}"
        "elif sys.argv[1:] == ['exec', '--help']:\n"
        "    print('--json --max-output-tokens')\n"
        "elif sys.argv[1:] == ['transport-capabilities', '--json']:\n"
        "    print(json.dumps(payload))\n"
        f"{mutation_block}"
        f"    raise SystemExit({capability_returncode})\n"
        "elif sys.argv[1:] == ['transport-self-test', '--json']:\n"
        + (
            "    print(json.dumps({'schema_version': 'omc-provider-transport-self-test/v1', "
            "'count_generation_payload_match': True, 'native_output_cap_forwarded': True, "
            "'usage_parsed': True, 'failure_is_fail_closed': True}))\n"
            if self_test_available
            else "    raise SystemExit(2)\n"
        )
        +
        "else:\n"
        "    raise SystemExit(99)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_probe_accepts_only_explicit_complete_transport_capabilities(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport)

    evidence = _probe(transport)

    assert evidence["status"] == "SUPPORTED"
    assert evidence["claim_eligible"] is False
    assert evidence["external_call_performed"] is False
    assert evidence["reason_codes"] == []
    assert evidence["expected_transport_sha256"] == _sha256_file(transport)
    assert evidence["checks"] == {
        "cached_input_included": True,
        "exact_pre_call_input_count": True,
        "native_output_token_cap": True,
        "raw_usage_event_stream": True,
        "reasoning_tokens_reported": True,
    }
    assert _validate(evidence) == evidence


def test_probe_evidence_rejects_rehashed_structured_source_forgery(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport, capability_returncode=2)
    evidence = _probe(transport)
    evidence["commands"][2]["returncode"] = 0
    evidence["commands"][2]["structured_output"] = {
        "schema_version": "omc-provider-transport-capabilities/v1",
        **{name: True for name in evidence["checks"]},
    }
    evidence["checks"] = {name: True for name in evidence["checks"]}
    evidence["reason_codes"] = []
    evidence["status"] = "SUPPORTED"
    evidence["evidence_sha256"] = _evidence_hash(evidence)

    with pytest.raises(ValueError, match="transport_probe_signature_invalid"):
        _validate(evidence)


def test_probe_executes_approved_snapshot_when_original_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = tmp_path / "transport"
    marker = tmp_path / "replacement-executed"
    _write_transport(transport)
    real_resolve_runtime = probe_module._resolve_runtime

    def replace_original_before_runtime_resolution(candidate: Path) -> Path:
        transport.write_text(
            f"#!/usr/bin/env python3\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
            encoding="utf-8",
        )
        transport.chmod(0o755)
        return real_resolve_runtime(candidate)

    monkeypatch.setattr(
        probe_module,
        "_resolve_runtime",
        replace_original_before_runtime_resolution,
    )

    evidence = _probe(transport)

    assert evidence["status"] == "HOLD_TRANSPORT_UNSUPPORTED"
    assert "transport_binary_changed" in evidence["reason_codes"]
    assert not marker.exists()


def test_probe_does_not_execute_unapproved_transport(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    observed = tmp_path / "executed.txt"
    _write_transport(transport, environment_path=observed)

    evidence = _probe(transport, expected_sha256="0" * 64)

    assert evidence["status"] == "HOLD_TRANSPORT_UNSUPPORTED"
    assert evidence["reason_codes"] == ["transport_hash_not_approved"]
    assert evidence["commands"] == []
    assert not observed.exists()


def test_probe_runs_in_disposable_working_directory(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    observed = tmp_path / "cwd.txt"
    _write_transport(transport, cwd_path=observed)

    evidence = _probe(transport)

    assert evidence["status"] == "SUPPORTED"
    assert Path(observed.read_text(encoding="utf-8")) not in {
        Path.cwd().resolve(),
        transport.parent.resolve(),
    }


def test_probe_uses_pinned_python_runtime_instead_of_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "fake-python-ran"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        f"#!/bin/sh\nprintf ran > {marker}\nexit 99\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    evidence = _probe(transport)

    assert evidence["status"] == "SUPPORTED"
    assert evidence["runtime_path"] == str(Path(sys.executable).resolve())
    assert not marker.exists()


def test_probe_evidence_does_not_persist_unrecognized_stdout_fields(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    capabilities = {
        "schema_version": "omc-provider-transport-capabilities/v1",
        "exact_pre_call_input_count": True,
        "cached_input_included": True,
        "reasoning_tokens_reported": True,
        "native_output_token_cap": True,
        "raw_usage_event_stream": True,
        "secret": "must-not-persist",
    }
    _write_transport(transport, capabilities=capabilities)

    evidence = _probe(transport)
    serialized = json.dumps(evidence, sort_keys=True)

    assert evidence["status"] == "SUPPORTED"
    assert "must-not-persist" not in serialized
    assert "stdout_base64" not in serialized


def test_probe_holds_before_provider_call_when_capability_surface_is_missing(
    tmp_path: Path,
) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport, capability_returncode=2)

    evidence = _probe(transport)

    assert evidence["status"] == "HOLD_TRANSPORT_UNSUPPORTED"
    assert evidence["external_call_performed"] is False
    assert evidence["reason_codes"] == ["transport_capabilities_unavailable"]
    assert all(command["action"] != "execute" for command in evidence["commands"])


def test_probe_holds_when_any_required_capability_is_false(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(
        transport,
        capabilities={
            "schema_version": "omc-provider-transport-capabilities/v1",
            "exact_pre_call_input_count": True,
            "cached_input_included": True,
            "reasoning_tokens_reported": True,
            "native_output_token_cap": False,
            "raw_usage_event_stream": True,
        },
    )

    evidence = _probe(transport)

    assert evidence["status"] == "HOLD_TRANSPORT_UNSUPPORTED"
    assert evidence["reason_codes"] == ["native_output_token_cap_missing"]


def test_probe_evidence_rejects_tampering(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport)
    evidence = _probe(transport)
    evidence["status"] = "HOLD_TRANSPORT_UNSUPPORTED"

    with pytest.raises(ValueError, match="transport_probe_evidence_hash_mismatch"):
        _validate(evidence)


def test_probe_evidence_rejects_rehashed_command_inconsistency(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport)
    evidence = _probe(transport)
    evidence["commands"][2]["returncode"] = 2
    _resign(evidence)

    with pytest.raises(ValueError, match="transport_probe_checks_inconsistent"):
        _validate(evidence)


def test_probe_evidence_rejects_rehashed_capability_claims(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(
        transport,
        capabilities={
            "schema_version": "omc-provider-transport-capabilities/v1",
            "exact_pre_call_input_count": True,
            "cached_input_included": True,
            "reasoning_tokens_reported": True,
            "native_output_token_cap": False,
            "raw_usage_event_stream": True,
        },
    )
    evidence = _probe(transport)
    evidence["checks"] = {name: True for name in evidence["checks"]}
    evidence["reason_codes"] = []
    evidence["status"] = "SUPPORTED"
    _resign(evidence)

    with pytest.raises(ValueError, match="transport_probe_checks_inconsistent"):
        _validate(evidence)


def test_probe_does_not_inherit_provider_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = tmp_path / "transport"
    observed = tmp_path / "environment.txt"
    _write_transport(transport, environment_path=observed)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-probe")

    evidence = _probe(transport)

    assert evidence["status"] == "SUPPORTED"
    assert observed.read_text(encoding="utf-8") == "absent"


def test_probe_holds_when_behavioral_self_test_is_missing(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport, self_test_available=False)

    evidence = _probe(transport)

    assert evidence["status"] == "HOLD_TRANSPORT_UNSUPPORTED"
    assert evidence["reason_codes"] == ["transport_self_test_unavailable"]


def test_probe_evidence_rejects_rehashed_noncanonical_argv(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport)
    evidence = _probe(transport)
    evidence["commands"][0]["args"] = ["exec", "--danger"]
    _resign(evidence)

    with pytest.raises(ValueError, match="transport_probe_commands_invalid"):
        _validate(evidence)


def test_probe_prevents_snapshot_self_mutation(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport, mutate_after_capabilities=True)

    evidence = _probe(transport)

    assert evidence["status"] == "HOLD_TRANSPORT_UNSUPPORTED"
    assert evidence["reason_codes"] == ["transport_capabilities_unavailable"]
    assert evidence["transport_snapshot_sha256"] == evidence[
        "transport_snapshot_sha256_after"
    ]


def test_probe_terminates_transport_when_output_exceeds_bound(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport, overflow_version=True)

    started = time.monotonic()
    evidence = _probe(transport)
    elapsed = time.monotonic() - started

    assert elapsed < 4
    assert evidence["status"] == "HOLD_TRANSPORT_UNSUPPORTED"
    assert evidence["commands"][0]["limit_exceeded"] is True
    assert evidence["commands"][0]["returncode"] != 0


def test_probe_bounds_stderr_output(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport, overflow_version=True, overflow_stderr=True)

    evidence = _probe(transport)

    assert evidence["commands"][0]["limit_exceeded"] is True
    assert evidence["commands"][0]["returncode"] != 0


def test_probe_terminates_transport_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport, slow_version=True)
    monkeypatch.setattr(
        "omc_provider_transport_probe.COMMAND_TIMEOUT_SECONDS",
        0.1,
    )

    evidence = _probe(transport)

    assert evidence["commands"][0]["timed_out"] is True
    assert evidence["commands"][0]["returncode"] != 0


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_probe_kills_descendant_when_parent_exits_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = tmp_path / "transport"
    child_pid_path = tmp_path / "child.pid"
    _write_transport(transport, child_pid_path=child_pid_path)
    monkeypatch.setattr(
        "omc_provider_transport_probe.COMMAND_TIMEOUT_SECONDS",
        2,
    )

    child_pid: int | None = None
    try:
        evidence = _probe(transport)
        child_pid = int(child_pid_path.read_text())
        deadline = time.monotonic() + 1
        while _pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)

        assert evidence["commands"][0]["timed_out"] is True
        assert not _pid_exists(child_pid)
    finally:
        if child_pid is not None and _pid_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def test_probe_evidence_requires_transport_digest(tmp_path: Path) -> None:
    transport = tmp_path / "transport"
    _write_transport(transport)
    evidence = _probe(transport)
    evidence["transport_sha256"] = None
    _resign(evidence)

    with pytest.raises(ValueError, match="transport_probe_transport_hash_invalid"):
        _validate(evidence)


def test_cli_writes_hold_evidence_for_current_style_transport(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    artifact_root = tmp_path / "artifacts"
    repo_root.mkdir()
    artifact_root.mkdir()
    transport = repo_root / "transport"
    output = artifact_root / "evidence.json"
    private_key = tmp_path / "signer.key"
    _write_transport(transport, capability_returncode=2)
    _write_private_key(private_key)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "probe",
            "--transport",
            str(transport),
            "--expected-transport-sha256",
            _sha256_file(transport),
            "--signer-private-key-file",
            str(private_key),
            "--repo-root",
            str(repo_root),
            "--artifact-root",
            str(artifact_root),
            "--expected-signer-public-key",
            TEST_PUBLIC_KEY,
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stdout)["status"] == "HOLD_TRANSPORT_UNSUPPORTED"
    assert _validate(json.loads(output.read_text(encoding="utf-8")))["status"] == (
        "HOLD_TRANSPORT_UNSUPPORTED"
    )


def test_cli_writes_structured_hold_when_transport_is_missing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    artifact_root = tmp_path / "artifacts"
    repo_root.mkdir()
    artifact_root.mkdir()
    transport = repo_root / "missing-transport"
    output = artifact_root / "evidence.json"
    private_key = tmp_path / "signer.key"
    _write_private_key(private_key)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "probe",
            "--transport",
            str(transport),
            "--expected-transport-sha256",
            "0" * 64,
            "--signer-private-key-file",
            str(private_key),
            "--repo-root",
            str(repo_root),
            "--artifact-root",
            str(artifact_root),
            "--expected-signer-public-key",
            TEST_PUBLIC_KEY,
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    assert json.loads(completed.stdout)["reason_codes"] == [
        "transport_binary_unavailable"
    ]
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["commands"] == []
    assert _validate(evidence)["status"] == "HOLD_TRANSPORT_UNSUPPORTED"


@pytest.mark.parametrize("key_root_name", ["repo", "artifacts"])
def test_private_key_must_be_outside_repo_and_artifact_roots(
    tmp_path: Path,
    key_root_name: str,
) -> None:
    repo_root = tmp_path / "repo"
    artifact_root = tmp_path / "artifacts"
    repo_root.mkdir()
    artifact_root.mkdir()
    key_root = repo_root if key_root_name == "repo" else artifact_root
    private_key = key_root / "signer.key"
    _write_private_key(private_key)

    with pytest.raises(ValueError, match="transport_probe_private_key_location_invalid"):
        _load_private_key(
            private_key,
            repo_root=repo_root,
            artifact_root=artifact_root,
        )
