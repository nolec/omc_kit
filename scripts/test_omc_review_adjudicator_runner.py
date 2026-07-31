from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_adjudicator_runner import (
    _build_codex_command,
    _parse_codex_jsonl,
    _sanitized_environment,
    _trusted_codex_identity,
    run_fresh_codex_adjudication,
)


_RECEIPT_KEY = b"runner-test-receipt-key"


def _digest(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _packet():
    packet = {
        "status": "pending_blind_semantic_adjudication",
        "recorded_at": "2026-07-31T00:00:00Z",
        "case_count": 1,
        "receipt_key_sha256": hashlib.sha256(_RECEIPT_KEY).hexdigest(),
        "instructions": "Classify every finding.",
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff-1",
            "gold_findings": [{
                "id": "bug-1",
                "severity": "P1",
                "file": "src/service.py",
                "line": 10,
                "reason": "Regression.",
            }],
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0,
                    "severity": "P1",
                    "file": "src/service.py",
                    "line": 10,
                    "message": "Regression.",
                }]},
                {"alias": "review-set-b", "findings": []},
            ],
        }],
    }
    packet["packet_sha256"] = _digest(packet)
    return packet


def _adjudication(packet):
    return {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0,
                    "classification": "hit",
                    "gold_finding_id": "bug-1",
                    "evidence_accuracy": "accurate",
                }]},
                {"alias": "review-set-b", "findings": []},
            ],
        }],
    }


def _event_stream(packet, *, thread_id="thread-fresh-1"):
    return (
        json.dumps({"type": "thread.started", "thread_id": thread_id})
        + "\n"
        + json.dumps({
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(_adjudication(packet)),
            },
        })
        + "\n"
        + json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10}})
        + "\n"
    ).encode()


def test_parse_codex_jsonl_returns_provider_thread_and_structured_output():
    packet = _packet()

    thread_id, output = _parse_codex_jsonl(_event_stream(packet))

    assert thread_id == "thread-fresh-1"
    assert output == _adjudication(packet)


def test_parse_codex_jsonl_rejects_tool_execution_events():
    packet = _packet()
    event_stream = (
        json.dumps({"type": "thread.started", "thread_id": "thread-fresh-1"})
        + "\n"
        + json.dumps({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "cat /private/provider-mapping.json",
            },
        })
        + "\n"
        + json.dumps({
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(_adjudication(packet)),
            },
        })
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="tool-free"):
        _parse_codex_jsonl(event_stream)


def test_trusted_codex_identity_rejects_non_default_binary(tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_bytes(b"fake")
    fake_codex.chmod(0o755)

    with pytest.raises(ValueError, match="trusted Codex binary"):
        _trusted_codex_identity(str(fake_codex))


@pytest.mark.parametrize(
    "event_stream,error",
    [
        (b'{"type":"turn.completed"}\n', "exactly one thread.started"),
        (
            b'{"type":"thread.started","thread_id":"one"}\n'
            b'{"type":"thread.started","thread_id":"two"}\n',
            "exactly one thread.started",
        ),
        (b'{"type":"thread.started","thread_id":""}\n', "provider thread id"),
        (b"not-json\n", "invalid Codex JSONL"),
    ],
)
def test_parse_codex_jsonl_rejects_unverifiable_streams(event_stream, error):
    with pytest.raises(ValueError, match=error):
        _parse_codex_jsonl(event_stream)


def test_codex_command_forces_fresh_isolated_execution(tmp_path):
    command = _build_codex_command(
        codex_path="/fake/codex",
        schema_path=tmp_path / "schema.json",
        model="test-model",
    )

    assert command[:2] == ["/fake/codex", "exec"]
    assert "--ephemeral" in command
    assert "--json" in command
    assert ["--sandbox", "read-only"] == command[
        command.index("--sandbox"):command.index("--sandbox") + 2
    ]
    assert "--skip-git-repo-check" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "resume" not in command


def test_sanitized_environment_does_not_expose_omc_secrets():
    environment = _sanitized_environment({
        "HOME": "/tmp/home",
        "CODEX_HOME": "/tmp/codex",
        "PATH": "/usr/bin",
        "TMPDIR": "/tmp",
        "OMC_RECEIPT_KEY": "secret",
        "OMC_PRIVATE_MAPPING": "secret-map",
        "UNRELATED_SECRET": "also-secret",
    })

    assert environment["HOME"] == "/tmp/home"
    assert environment["CODEX_HOME"] == "/tmp/codex"
    assert "OMC_RECEIPT_KEY" not in environment
    assert "OMC_PRIVATE_MAPPING" not in environment
    assert "UNRELATED_SECRET" not in environment


def test_runner_atomically_writes_fresh_session_artifacts(monkeypatch, tmp_path):
    packet = _packet()
    observed = {}

    class Result:
        returncode = 0
        stdout = _event_stream(packet)
        stderr = b""

    def run(command, **kwargs):
        observed["command"] = command
        observed["cwd_entries"] = sorted(
            path.name for path in Path(kwargs["cwd"]).iterdir()
        )
        observed["schema"] = json.loads(
            (Path(kwargs["cwd"]) / "output-schema.json").read_text()
        )
        observed["env"] = kwargs["env"]
        return Result()

    monkeypatch.setattr("omc_review_adjudicator_runner.subprocess.run", run)
    monkeypatch.setattr(
        "omc_review_adjudicator_runner._trusted_codex_identity",
        lambda path: {"path": path, "sha256": "a" * 64},
    )
    destination = tmp_path / "completed-run"

    result = run_fresh_codex_adjudication(
        packet,
        receipt_key=_RECEIPT_KEY,
        output_dir=destination,
        model="test-model",
        codex_path="/fake/codex",
        timeout_sec=10,
        source_env={
            "HOME": "/tmp/home",
            "CODEX_HOME": "/tmp/codex",
            "OMC_RECEIPT_KEY": "must-not-leak",
        },
    )

    assert observed["cwd_entries"] == ["output-schema.json"]
    assert observed["schema"]["properties"]["packet_sha256"]["const"] == packet[
        "packet_sha256"
    ]
    assert "OMC_RECEIPT_KEY" not in observed["env"]
    assert result["adjudication"]["adjudicator_provenance"][
        "provider_session_id"
    ] == "thread-fresh-1"
    assert result["adjudication"]["adjudicator_provenance"][
        "execution_scope"
    ] == "runner_attested_fresh_session"
    assert (destination / "adjudication.json").is_file()
    assert (destination / "receipt-envelope.json").is_file()
    assert (destination / "event-stream.jsonl").read_bytes() == Result.stdout


def test_runner_leaves_no_completed_directory_when_output_is_invalid(
    monkeypatch, tmp_path
):
    packet = _packet()

    class Result:
        returncode = 0
        stdout = b'{"type":"thread.started","thread_id":"thread-only"}\n'
        stderr = b""

    monkeypatch.setattr(
        "omc_review_adjudicator_runner.subprocess.run",
        lambda *args, **kwargs: Result(),
    )
    monkeypatch.setattr(
        "omc_review_adjudicator_runner._trusted_codex_identity",
        lambda path: {"path": path, "sha256": "a" * 64},
    )
    destination = tmp_path / "failed-run"

    with pytest.raises(ValueError, match="final adjudication output"):
        run_fresh_codex_adjudication(
            packet,
            receipt_key=_RECEIPT_KEY,
            output_dir=destination,
            model="test-model",
            codex_path="/fake/codex",
            timeout_sec=10,
        )

    assert not destination.exists()
