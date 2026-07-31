from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_review_v6_adjudication as adjudication_module
from omc_review_v6_adjudication import (
    _digest,
    build_blind_adjudication_packet,
    build_second_adjudication_consensus,
    build_second_blind_packet,
    build_replacement_report,
    seal_adjudication_execution,
    seal_fresh_adjudication_execution,
)

_RECEIPT_KEY = b"trusted-test-receipt-key"


def _inputs():
    gold = {
        "status": "signed_off",
        "decision_policy": {"primary_severity": ["P0", "P1"]},
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff-1",
            "gold_findings": [{
                "id": "core-bug",
                "severity": "P1",
                "file": "src/service.py",
                "line": "40",
                "reason": "A real core regression.",
            }],
        }],
    }
    codex = {
        "status": "completed_native_provider_runs_pending_adjudication",
        "clean_baseline": True,
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff-1",
            "providers": {"codex": {"status": "completed", "findings": [{
                "severity": "P1", "file": "/tmp/codex-run/workspace/src/service.py", "line": "40", "message": "Possible bug."
            }]}},
        }],
    }
    omc = {
        "status": "completed_omc_runs_pending_adjudication",
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff-1",
            "providers": {"omc-review": {"status": "completed", "findings": [{
                "severity": "중대", "file": "src/service.py", "line": "40", "message": "Core bug.",
                "evidence_class": "behavioral_direct", "evidence": "The branch is reachable.",
            }]}},
        }],
    }
    return gold, codex, omc


def _build_packet(*inputs):
    return build_blind_adjudication_packet(
        *inputs,
        receipt_key=_RECEIPT_KEY,
    )


def _provenance(packet, execution_id, raw_output):
    return {
        "executor": "codex",
        "model": "test-model",
        "execution_id": execution_id,
        "execution_scope": "omc_seal_execution",
        "recorded_at": "2026-07-31T00:00:00Z",
        "input_packet_sha256": packet["packet_sha256"],
        "provider_mapping_visible": False,
        "raw_output_sha256": hashlib.sha256(raw_output).hexdigest(),
    }


def _attach_first_provenance(adjudication, packet):
    return _attach_execution_envelope(
        adjudication, packet, "first-test-session"
    )


def _second_provenance(packet, raw_output=b"second raw"):
    return _provenance(packet, "second-test-session", raw_output)


def _attach_execution_envelope(adjudication, packet, execution_id):
    sealed, raw_output = seal_adjudication_execution(
        packet,
        adjudication,
        executor="codex",
        model="test-model",
        recorded_at="2026-07-31T00:00:00Z",
        receipt_key=_RECEIPT_KEY,
    )
    adjudication.clear()
    adjudication.update(sealed)
    return raw_output


def _attach_fresh_execution_envelope(
    adjudication, packet, execution_id, provider_session_id
):
    event_stream = (
        json.dumps({
            "type": "thread.started",
            "thread_id": provider_session_id,
        })
        + "\n"
    ).encode()
    sealed, raw_output = seal_fresh_adjudication_execution(
        packet,
        adjudication,
        executor="codex",
        model="test-model",
        execution_id=execution_id,
        provider_session_id=provider_session_id,
        command_fingerprint="f" * 64,
        event_stream=event_stream,
        executor_binary_path="/trusted/codex",
        executor_binary_sha256="a" * 64,
        tool_free_execution_verified=True,
        recorded_at="2026-07-31T00:00:00Z",
        receipt_key=_RECEIPT_KEY,
    )
    adjudication.clear()
    adjudication.update(sealed)
    return raw_output


def test_blind_packet_hides_provider_names_but_keeps_private_alias_mapping():
    packet, private_mapping = _build_packet(*_inputs())

    rendered = json.dumps(packet, ensure_ascii=False).lower()
    assert "codex" not in rendered
    assert "omc-review" not in rendered
    assert packet["status"] == "pending_blind_semantic_adjudication"
    assert {item["alias"] for item in packet["cases"][0]["review_sets"]} == {
        "review-set-a", "review-set-b"
    }
    assert packet["cases"][0]["review_sets"][1]["findings"][0]["file"] == "src/service.py"
    assert set(private_mapping["alias_to_provider"].values()) == {"codex", "omc-review"}


def test_blind_packet_excludes_non_actionable_review_suggestions():
    gold, codex, omc = _inputs()
    omc["cases"][0]["providers"]["omc-review"]["findings"].append({
        "severity": "제안",
        "file": "src/service.py",
        "line": "50",
        "message": "Add another test.",
        "evidence_class": "test_quality_only",
    })

    packet, _ = _build_packet(gold, codex, omc)

    omc_findings = packet["cases"][0]["review_sets"][0]["findings"]
    assert len(omc_findings) == 1
    assert omc_findings[0]["message"] == "Core bug."


def test_replacement_report_requires_complete_blind_labels_and_all_three_gates():
    packet, private_mapping = _build_packet(*_inputs())
    adjudication = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug",
                    "evidence_accuracy": "accurate",
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug",
                    "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }

    report = build_replacement_report(packet, private_mapping, adjudication)

    assert report["replacement_verdict"] == "not_replaceable"
    assert report["providers"]["omc-review"]["key_hit_count"] == 1
    assert report["providers"]["codex"]["false_positive_count"] == 0
    assert report["replacement_gate"]["minimum_omc_key_hit_count"] is False

    adjudication["cases"][0]["review_sets"][0]["findings"][0]["classification"] = "false_positive"
    adjudication["cases"][0]["review_sets"][0]["findings"][0]["gold_finding_id"] = None
    adjudication["cases"][0]["review_sets"][0]["findings"][0]["evidence_accuracy"] = None
    report = build_replacement_report(packet, private_mapping, adjudication)
    assert report["replacement_verdict"] == "not_replaceable"


def test_replacement_report_requires_verified_adjudicator_session_independence(monkeypatch):
    monkeypatch.setattr(adjudication_module, "_MINIMUM_OMC_KEY_HIT_COUNT", 1)
    packet, private_mapping = _build_packet(*_inputs())
    adjudication = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }

    report = build_replacement_report(packet, private_mapping, adjudication)

    assert report["replacement_gate"]["adjudicator_session_independence_verified"] is False
    assert report["replacement_verdict"] == "not_replaceable"


def test_second_blind_packet_selects_all_first_pass_false_positive_candidates():
    packet, _ = _build_packet(*_inputs())
    adjudication = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
                    "evidence_accuracy": None,
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
                    "evidence_accuracy": None,
                }]},
            ],
        }],
    }

    second_packet = build_second_blind_packet(packet, adjudication)

    assert second_packet["status"] == "pending_second_blind_semantic_adjudication"
    assert second_packet["candidate_count"] == 2
    assert second_packet["cases"][0]["review_sets"][0]["findings"][0]["finding_index"] == 0
    assert {item["alias"] for item in second_packet["cases"][0]["review_sets"]} == {
        "review-set-a", "review-set-b"
    }


def test_second_adjudication_disagreement_excludes_unresolved_candidate_from_fp_metrics():
    packet, private_mapping = _build_packet(*_inputs())
    first = {
        "status": "completed", "packet_sha256": packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [
            {"alias": "review-set-a", "findings": [{"finding_index": 0, "classification": "false_positive", "gold_finding_id": None, "evidence_accuracy": None}]},
            {"alias": "review-set-b", "findings": [{"finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug", "evidence_accuracy": "accurate"}]},
        ]}],
    }
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed", "packet_sha256": second_packet["packet_sha256"], "provider_outputs_visible": False,
        "adjudicator_provenance": _second_provenance(second_packet),
        "cases": [{"case_id": "case-1", "review_sets": [{"alias": "review-set-a", "findings": [{
            "finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
        }]}]}],
    }
    first_raw = _attach_first_provenance(first, packet)
    second_raw = _attach_execution_envelope(
        second, second_packet, "second-test-session"
    )

    consensus = build_second_adjudication_consensus(
        packet, first, second_packet, second, first_raw, second_raw,
        receipt_key=_RECEIPT_KEY,
    )
    assert consensus["execution_independence"] == {
        "scope": "omc_seal_execution",
        "verified": True,
        "adjudicator_session_independence_verified": False,
    }
    report = build_replacement_report(
        packet, private_mapping, first, second_packet, second, first_raw, second_raw,
        receipt_key=_RECEIPT_KEY,
    )

    assert consensus["confirmed_false_positive_count"] == 0
    assert consensus["unresolved_candidate_count"] == 1
    assert report["providers"]["omc-review"]["false_positive_count"] == 0
    assert report["providers"]["omc-review"]["unresolved_candidate_count"] == 1
    assert report["providers"]["omc-review"]["miss_count"] == 0
    assert report["providers"]["omc-review"]["key_miss_count"] == 0


def test_unresolved_duplicate_does_not_remove_an_independent_first_pass_hit():
    gold, codex, omc = _inputs()
    omc["cases"][0]["providers"]["omc-review"]["findings"].append({
        "severity": "중대", "file": "src/service.py", "line": "41", "message": "Duplicate core bug.",
        "evidence_class": "behavioral_direct", "evidence": "The branch is reachable.",
    })
    packet, private_mapping = _build_packet(gold, codex, omc)
    first = {
        "status": "completed", "packet_sha256": packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [
            {"alias": "review-set-a", "findings": [
                {"finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug", "evidence_accuracy": "accurate"},
                {"finding_index": 1, "classification": "false_positive", "gold_finding_id": None, "evidence_accuracy": None},
            ]},
            {"alias": "review-set-b", "findings": [
                {"finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug", "evidence_accuracy": "accurate"},
            ]},
        ]}],
    }
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed", "packet_sha256": second_packet["packet_sha256"], "provider_outputs_visible": False,
        "adjudicator_provenance": _second_provenance(second_packet),
        "cases": [{"case_id": "case-1", "review_sets": [{"alias": "review-set-a", "findings": [{
            "finding_index": 1, "classification": "hit", "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
        }]}]}],
    }
    first_raw = _attach_first_provenance(first, packet)
    second_raw = _attach_execution_envelope(
        second, second_packet, "second-test-session"
    )

    report = build_replacement_report(
        packet, private_mapping, first, second_packet, second, first_raw, second_raw,
        receipt_key=_RECEIPT_KEY,
    )

    assert report["providers"]["omc-review"]["unresolved_candidate_count"] == 1
    assert report["providers"]["omc-review"]["hit_count"] == 1
    assert report["providers"]["omc-review"]["key_hit_count"] == 1


def test_second_consensus_rejects_packet_missing_a_first_pass_false_positive():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed", "packet_sha256": packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [
            {"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive", "gold_finding_id": None, "evidence_accuracy": None,
            }]},
            {"alias": "review-set-b", "findings": [{
                "finding_index": 0, "classification": "false_positive", "gold_finding_id": None, "evidence_accuracy": None,
            }]},
        ]}],
    }
    second_packet = build_second_blind_packet(packet, first)
    second_packet["cases"][0]["review_sets"] = second_packet["cases"][0]["review_sets"][:1]
    second = {
        "status": "completed", "packet_sha256": second_packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [{"alias": "review-set-a", "findings": [{
            "finding_index": 0, "classification": "false_positive", "gold_finding_id": None, "evidence_accuracy": None,
        }]}]}],
    }

    with pytest.raises(ValueError, match="second packet candidate set mismatch"):
        build_second_adjudication_consensus(
            packet, first, second_packet, second, b"first raw", b"second raw",
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_invalid_packet_self_hash():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed", "packet_sha256": packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [
            {"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
                "evidence_accuracy": None,
            }]},
            {"alias": "review-set-b", "findings": [{
                "finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug",
                "evidence_accuracy": "accurate",
            }]},
        ]}],
    }
    second_packet = build_second_blind_packet(packet, first)
    second_packet["recorded_at"] = "tampered"
    second = {
        "status": "completed", "packet_sha256": second_packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [{"alias": "review-set-a", "findings": [{
            "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
            "evidence_accuracy": None,
        }]}]}],
    }

    with pytest.raises(ValueError, match="second packet hash mismatch"):
        build_second_adjudication_consensus(
            packet, first, second_packet, second, b"first raw", b"second raw",
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_missing_adjudicator_provenance():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed", "packet_sha256": packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [
            {"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
                "evidence_accuracy": None,
            }]},
            {"alias": "review-set-b", "findings": [{
                "finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug",
                "evidence_accuracy": "accurate",
            }]},
        ]}],
    }
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed", "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [{"alias": "review-set-a", "findings": [{
            "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
            "evidence_accuracy": None,
        }]}]}],
    }
    first_raw = _attach_first_provenance(first, packet)

    with pytest.raises(ValueError, match="second adjudicator provenance is required"):
        build_second_adjudication_consensus(
            packet, first, second_packet, second, first_raw, b"second raw",
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_mismatched_adjudicator_provenance():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed", "packet_sha256": packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [
            {"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
                "evidence_accuracy": None,
            }]},
            {"alias": "review-set-b", "findings": [{
                "finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug",
                "evidence_accuracy": "accurate",
            }]},
        ]}],
    }
    second_packet = build_second_blind_packet(packet, first)
    provenance = _second_provenance(second_packet)
    provenance["input_packet_sha256"] = "wrong-packet"
    second = {
        "status": "completed", "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "adjudicator_provenance": provenance,
        "cases": [{"case_id": "case-1", "review_sets": [{"alias": "review-set-a", "findings": [{
            "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
            "evidence_accuracy": None,
        }]}]}],
    }
    first_raw = _attach_first_provenance(first, packet)

    with pytest.raises(ValueError, match="second adjudicator packet provenance mismatch"):
        build_second_adjudication_consensus(
            packet, first, second_packet, second, first_raw, b"second raw",
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_mutated_candidate_payload_with_valid_hash():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed", "packet_sha256": packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [
            {"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
                "evidence_accuracy": None,
            }]},
            {"alias": "review-set-b", "findings": [{
                "finding_index": 0, "classification": "hit", "gold_finding_id": "core-bug",
                "evidence_accuracy": "accurate",
            }]},
        ]}],
    }
    second_packet = build_second_blind_packet(packet, first)
    second_packet["cases"][0]["review_sets"][0]["findings"][0]["message"] = "tampered"
    second_packet["packet_sha256"] = _digest({
        key: value for key, value in second_packet.items() if key != "packet_sha256"
    })
    second = {
        "status": "completed", "packet_sha256": second_packet["packet_sha256"], "provider_outputs_visible": False,
        "cases": [{"case_id": "case-1", "review_sets": [{"alias": "review-set-a", "findings": [{
            "finding_index": 0, "classification": "false_positive", "gold_finding_id": None,
            "evidence_accuracy": None,
        }]}]}],
    }

    with pytest.raises(ValueError, match="second packet payload mismatch"):
        build_second_adjudication_consensus(
            packet, first, second_packet, second, b"first raw", b"second raw",
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_reused_seal_execution(monkeypatch):
    monkeypatch.setattr(
        adjudication_module,
        "_new_execution_id",
        lambda: "omc-exec-reused",
    )
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "false_positive",
                    "gold_finding_id": None, "evidence_accuracy": None,
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    first_raw = _attach_first_provenance(first, packet)
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed",
        "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "adjudicator_provenance": _provenance(
            second_packet, "first-test-session", b"second raw"
        ),
        "cases": [{
            "case_id": "case-1",
            "review_sets": [{"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive",
                "gold_finding_id": None, "evidence_accuracy": None,
            }]}],
        }],
    }
    second_raw = _attach_execution_envelope(
        second, second_packet, "first-test-session"
    )

    with pytest.raises(ValueError, match="independent seal execution"):
        build_second_adjudication_consensus(
            packet, first, second_packet, second, first_raw, second_raw,
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_raw_output_hash_mismatch():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "false_positive",
                    "gold_finding_id": None, "evidence_accuracy": None,
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    first_raw = _attach_first_provenance(first, packet)
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed",
        "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "adjudicator_provenance": _second_provenance(
            second_packet, b"expected raw"
        ),
        "cases": [{
            "case_id": "case-1",
            "review_sets": [{"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive",
                "gold_finding_id": None, "evidence_accuracy": None,
            }]}],
        }],
    }
    second_raw = _attach_execution_envelope(
        second, second_packet, "second-test-session"
    )

    with pytest.raises(ValueError, match="raw output hash mismatch"):
        build_second_adjudication_consensus(
            packet, first, second_packet, second, first_raw, b"different raw",
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_first_raw_output_hash_mismatch():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "false_positive",
                    "gold_finding_id": None, "evidence_accuracy": None,
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    first_raw = _attach_first_provenance(first, packet)
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed",
        "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "adjudicator_provenance": _second_provenance(second_packet),
        "cases": [{
            "case_id": "case-1",
            "review_sets": [{"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive",
                "gold_finding_id": None, "evidence_accuracy": None,
            }]}],
        }],
    }
    second_raw = _attach_execution_envelope(
        second, second_packet, "second-test-session"
    )

    with pytest.raises(ValueError, match="first adjudicator raw output hash mismatch"):
        build_second_adjudication_consensus(
            packet,
            first,
            second_packet,
            second,
            b"different first raw",
            second_raw,
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_session_not_bound_to_raw_execution_envelope():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "false_positive",
                    "gold_finding_id": None, "evidence_accuracy": None,
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    first_raw = _attach_execution_envelope(
        first, packet, "first-envelope-session"
    )
    first["adjudicator_provenance"]["execution_id"] = "forged-execution"
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed",
        "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [{"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive",
                "gold_finding_id": None, "evidence_accuracy": None,
            }]}],
        }],
    }
    second_raw = _attach_execution_envelope(
        second, second_packet, "second-envelope-session"
    )

    with pytest.raises(ValueError, match="execution envelope metadata mismatch"):
        build_second_adjudication_consensus(
            packet, first, second_packet, second, first_raw, second_raw,
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_rejects_jointly_forged_envelopes_without_trusted_receipt():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "false_positive",
                    "gold_finding_id": None, "evidence_accuracy": None,
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    first, first_raw = seal_adjudication_execution(
        packet,
        first,
        executor="codex",
        model="test-model",
        recorded_at="2026-07-31T00:00:00Z",
        receipt_key=_RECEIPT_KEY,
    )
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed",
        "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [{"alias": "review-set-a", "findings": [{
                "finding_index": 0, "classification": "false_positive",
                "gold_finding_id": None, "evidence_accuracy": None,
            }]}],
        }],
    }
    second, second_raw = seal_adjudication_execution(
        second_packet,
        second,
        executor="codex",
        model="test-model",
        recorded_at="2026-07-31T01:00:00Z",
        receipt_key=_RECEIPT_KEY,
    )

    forged_first = json.loads(json.dumps(first))
    forged_envelope = json.loads(first_raw)
    forged_first["adjudicator_provenance"]["execution_id"] = "forged-first-execution"
    forged_envelope["execution"]["execution_id"] = "forged-first-execution"
    forged_raw = (
        json.dumps(
            forged_envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    forged_first["adjudicator_provenance"]["raw_output_sha256"] = hashlib.sha256(
        forged_raw
    ).hexdigest()

    with pytest.raises(ValueError, match="trusted execution receipt"):
        build_second_adjudication_consensus(
            packet,
            forged_first,
            second_packet,
            second,
            forged_raw,
            second_raw,
            receipt_key=_RECEIPT_KEY,
        )


def test_packet_pins_receipt_key_and_seal_issues_its_own_execution_id():
    packet, _ = build_blind_adjudication_packet(
        *_inputs(),
        receipt_key=_RECEIPT_KEY,
    )
    adjudication = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }

    with pytest.raises(ValueError, match="packet trust anchor"):
        seal_adjudication_execution(
            packet,
            adjudication,
            executor="codex",
            model="test-model",
            receipt_key=b"attacker-controlled-key",
        )

    sealed, _ = seal_adjudication_execution(
        packet,
        adjudication,
        executor="codex",
        model="test-model",
        receipt_key=_RECEIPT_KEY,
    )

    assert packet["receipt_key_sha256"] == hashlib.sha256(_RECEIPT_KEY).hexdigest()
    assert sealed["adjudicator_provenance"]["execution_id"].startswith("omc-exec-")
    assert sealed["adjudicator_provenance"]["execution_scope"] == "omc_seal_execution"


def test_seal_rejects_packet_body_changed_after_hashing():
    packet, _ = build_blind_adjudication_packet(
        *_inputs(),
        receipt_key=_RECEIPT_KEY,
    )
    adjudication = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0, "classification": "hit",
                    "gold_finding_id": "core-bug", "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    packet["cases"][0]["review_sets"][0]["findings"][0]["message"] = "tampered"

    with pytest.raises(ValueError, match="packet hash mismatch"):
        seal_adjudication_execution(
            packet,
            adjudication,
            executor="codex",
            model="test-model",
            receipt_key=_RECEIPT_KEY,
        )


def test_second_consensus_accepts_two_distinct_fresh_provider_sessions():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0,
                    "classification": "false_positive",
                    "gold_finding_id": None,
                    "evidence_accuracy": None,
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0,
                    "classification": "hit",
                    "gold_finding_id": "core-bug",
                    "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    first_raw = _attach_fresh_execution_envelope(
        first, packet, "runner-first", "provider-thread-first"
    )
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed",
        "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [{"alias": "review-set-a", "findings": [{
                "finding_index": 0,
                "classification": "false_positive",
                "gold_finding_id": None,
                "evidence_accuracy": None,
            }]}],
        }],
    }
    second_raw = _attach_fresh_execution_envelope(
        second, second_packet, "runner-second", "provider-thread-second"
    )

    consensus = build_second_adjudication_consensus(
        packet,
        first,
        second_packet,
        second,
        first_raw,
        second_raw,
        receipt_key=_RECEIPT_KEY,
    )

    assert consensus["execution_independence"] == {
        "scope": "runner_attested_fresh_session",
        "verified": True,
        "adjudicator_session_independence_verified": True,
    }


def test_second_consensus_rejects_reused_provider_session():
    packet, _ = _build_packet(*_inputs())
    first = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0,
                    "classification": "false_positive",
                    "gold_finding_id": None,
                    "evidence_accuracy": None,
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0,
                    "classification": "hit",
                    "gold_finding_id": "core-bug",
                    "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    first_raw = _attach_fresh_execution_envelope(
        first, packet, "runner-first", "provider-thread-reused"
    )
    second_packet = build_second_blind_packet(packet, first)
    second = {
        "status": "completed",
        "packet_sha256": second_packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [{"alias": "review-set-a", "findings": [{
                "finding_index": 0,
                "classification": "false_positive",
                "gold_finding_id": None,
                "evidence_accuracy": None,
            }]}],
        }],
    }
    second_raw = _attach_fresh_execution_envelope(
        second, second_packet, "runner-second", "provider-thread-reused"
    )

    with pytest.raises(ValueError, match="provider session"):
        build_second_adjudication_consensus(
            packet,
            first,
            second_packet,
            second,
            first_raw,
            second_raw,
            receipt_key=_RECEIPT_KEY,
        )


def test_fresh_receipt_rejects_tampered_event_stream():
    packet, _ = _build_packet(*_inputs())
    adjudication = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0,
                    "classification": "hit",
                    "gold_finding_id": "core-bug",
                    "evidence_accuracy": "accurate",
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0,
                    "classification": "hit",
                    "gold_finding_id": "core-bug",
                    "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    raw_output = _attach_fresh_execution_envelope(
        adjudication, packet, "runner-first", "provider-thread-first"
    )
    envelope = json.loads(raw_output)
    envelope["event_stream_base64"] = "dGFtcGVyZWQ="
    tampered = (
        json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    adjudication["adjudicator_provenance"]["raw_output_sha256"] = hashlib.sha256(
        tampered
    ).hexdigest()

    with pytest.raises(ValueError, match="event stream hash"):
        adjudication_module._validate_adjudicator_provenance(
            packet,
            adjudication,
            label="first",
            raw_output=tampered,
            receipt_key=_RECEIPT_KEY,
        )


def test_fresh_seal_rejects_provider_session_not_present_in_event_stream():
    packet, _ = _build_packet(*_inputs())
    adjudication = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [{
                    "finding_index": 0,
                    "classification": "hit",
                    "gold_finding_id": "core-bug",
                    "evidence_accuracy": "accurate",
                }]},
                {"alias": "review-set-b", "findings": [{
                    "finding_index": 0,
                    "classification": "hit",
                    "gold_finding_id": "core-bug",
                    "evidence_accuracy": "accurate",
                }]},
            ],
        }],
    }
    event_stream = (
        '{"type":"thread.started","thread_id":"actual-provider-thread"}\n'
    ).encode()

    with pytest.raises(ValueError, match="provider session.*event stream"):
        seal_fresh_adjudication_execution(
            packet,
            adjudication,
            executor="codex",
            model="test-model",
            execution_id="runner-first",
            provider_session_id="claimed-provider-thread",
            command_fingerprint="f" * 64,
            event_stream=event_stream,
            executor_binary_path="/trusted/codex",
            executor_binary_sha256="a" * 64,
            tool_free_execution_verified=True,
            receipt_key=_RECEIPT_KEY,
        )


def test_adjudication_rejects_duplicate_finding_indexes():
    packet, _ = _build_packet(*_inputs())
    duplicate = {
        "finding_index": 0,
        "classification": "hit",
        "gold_finding_id": "core-bug",
        "evidence_accuracy": "accurate",
    }
    adjudication = {
        "status": "completed",
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible": False,
        "cases": [{
            "case_id": "case-1",
            "review_sets": [
                {"alias": "review-set-a", "findings": [duplicate, duplicate]},
                {"alias": "review-set-b", "findings": [duplicate]},
            ],
        }],
    }

    with pytest.raises(ValueError, match="finding set mismatch"):
        seal_fresh_adjudication_execution(
            packet,
            adjudication,
            executor="codex",
            model="test-model",
            execution_id="runner-first",
            provider_session_id="actual-provider-thread",
            command_fingerprint="f" * 64,
            event_stream=(
                '{"type":"thread.started","thread_id":"actual-provider-thread"}\n'
            ).encode(),
            executor_binary_path="/trusted/codex",
            executor_binary_sha256="a" * 64,
            tool_free_execution_verified=True,
            receipt_key=_RECEIPT_KEY,
        )
