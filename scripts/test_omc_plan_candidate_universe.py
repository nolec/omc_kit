import base64
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_plan_candidate_universe as candidate_universe
import omc_plan_context_selection as context_selection


SURFACES = (
    "ui_state",
    "ui_state",
    "api_payload",
    "api_payload",
    "data_indexing",
    "data_indexing",
    "backend_rules",
    "backend_rules",
    "multi_file_legacy",
    "multi_file_legacy",
)
AMBIGUITIES = (
    "low",
    "low",
    "low",
    "medium",
    "medium",
    "medium",
    "medium",
    "high",
    "high",
    "high",
)


def _sha(marker: str) -> str:
    return candidate_universe.canonical_digest(marker)[:40]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _records(count: int = 20) -> list[dict]:
    records = []
    for index in range(count):
        template_index = index % 10
        records.append({
            "source_record_id": f"observed-{index + 1:02d}",
            "session_id": f"202608{index + 1:02d}T010101-session",
            "confirmed_request": f"Observed request {index + 1}",
            "confirmed_at": f"2026-08-{index + 1:02d}T01:01:01+09:00",
            "repo_alias": f"repo-{index + 1:02d}",
            "repository_root": f"/private/work/repo-{index + 1:02d}",
            "baseline_commit": _sha(f"baseline-{index}"),
            "followup_commit": _sha(f"followup-{index}"),
            "completed_at": f"2026-08-{index + 1:02d}T02:01:01+09:00",
            "changed_paths": [f"src/change-{index + 1}.py"],
            "context_candidate_paths": [f"src/context-{index + 1}.py"],
            "surface": SURFACES[template_index],
            "ambiguity": AMBIGUITIES[template_index],
            "selected_object": template_index < 1,
            "request_source": "confirmed_session_record",
            "completion_source": "explicit_completion_receipt",
            "provider_outputs_available": False,
        })
    return records


def _inventory(records: list[dict] | None = None) -> dict:
    return candidate_universe.collect_private_inventory(
        records or _records(),
        observed_from="2026-08-01",
        observed_through="2026-08-31",
        provider_ledger_cutoff="2026-09-01T00:00:00+09:00",
    )


def _anchor_registry(private_key: Ed25519PrivateKey) -> dict:
    registry = {
        "schema_version": 1,
        "status": "active",
        "generation": 1,
        "previous_registry_sha256": None,
        "batches": [{
            "batch_id": "fresh-batch-a",
            "status": "active",
            "selection_sha256": candidate_universe.canonical_digest("selection"),
            "selection_commit": _sha("selection-commit"),
            "preregistration_manifest_sha256": candidate_universe.canonical_digest(
                "manifest"
            ),
            "source_commit": _sha("source-commit"),
            "retrieval_policy_sha256": candidate_universe.canonical_digest(
                "retrieval-policy"
            ),
            "selection_signer_public_key": candidate_universe.public_key_text(
                Ed25519PrivateKey.generate()
            ),
            "preregistration_signer_public_key": candidate_universe.public_key_text(
                Ed25519PrivateKey.generate()
            ),
        }],
        "signoff": {
            "signer": "confirmatory-anchor-root-v1",
            "signer_public_key": candidate_universe.public_key_text(private_key),
            "signature": "",
        },
        "registry_sha256": "",
    }
    registry["registry_sha256"] = context_selection._anchor_registry_digest(registry)
    registry["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.anchor_registry_payload(registry))
    ).decode("ascii")
    return registry


def _frozen_inputs(records: list[dict] | None = None):
    collector_key = Ed25519PrivateKey.generate()
    label_key = Ed25519PrivateKey.generate()
    universe_key = Ed25519PrivateKey.generate()
    seed_key = Ed25519PrivateKey.generate()
    anchor_key = Ed25519PrivateKey.generate()
    inventory_draft = _inventory(records)
    inventory = candidate_universe.seal_private_inventory(
        inventory_draft,
        collector_key,
        expected_inventory_sha256=inventory_draft["inventory_sha256"],
    )
    collector_trust = {
        "trusted_collector_public_keys": {
            candidate_universe.public_key_text(collector_key)
        },
        "expected_inventory_sha256": inventory["inventory_sha256"],
    }
    label_receipt = candidate_universe.sign_label_receipt(
        inventory,
        label_key,
        **collector_trust,
    )
    prior_commits = [_sha("prior-commit")]
    anchor_registry = _anchor_registry(anchor_key)
    prior_snapshot_key = Ed25519PrivateKey.generate()
    prior_snapshot = candidate_universe.sign_prior_commit_snapshot(
        prior_commits,
        anchor_registry_sha256=anchor_registry["registry_sha256"],
        signer_private_key=prior_snapshot_key,
    )
    prior_trust = {
        "prior_snapshot": prior_snapshot,
        "trusted_prior_snapshot_public_keys": {
            candidate_universe.public_key_text(prior_snapshot_key)
        },
        "expected_prior_snapshot_sha256": prior_snapshot["snapshot_sha256"],
        "prior_anchor_registry": anchor_registry,
        "trusted_anchor_public_keys": {
            candidate_universe.public_key_text(anchor_key)
        },
        "expected_anchor_registry_sha256": anchor_registry["registry_sha256"],
    }
    draft = candidate_universe.prepare_frozen_universe(
        inventory,
        **collector_trust,
        label_receipt=label_receipt,
        trusted_label_public_keys={
            candidate_universe.public_key_text(label_key)
        },
        **prior_trust,
        batch_id="fresh-batch-b-202608",
        source_snapshot_sha256=candidate_universe.canonical_digest(
            {"snapshot": "2026-08"}
        ),
    )
    universe = candidate_universe.seal_frozen_universe(
        draft,
        universe_key,
        expected_draft_sha256=draft["universe_sha256"],
        prior_snapshot=prior_snapshot,
        trusted_prior_snapshot_public_keys=prior_trust[
            "trusted_prior_snapshot_public_keys"
        ],
        expected_prior_snapshot_sha256=prior_snapshot["snapshot_sha256"],
    )
    return inventory, universe, prior_trust, universe_key, seed_key


def test_collect_private_inventory_audits_incomplete_records_without_inference():
    records = _records()
    del records[0]["completion_source"]

    inventory = _inventory(records)

    assert len(inventory["accepted_records"]) == 19
    assert inventory["audit"]["discovered_count"] == 20
    assert inventory["audit"]["rejected_reason_counts"] == {
        "invalid_provenance": 1
    }
    assert inventory["rejected_records"] == [{
        "source_record_id": "observed-01",
        "source_record_sha256": candidate_universe.canonical_digest(records[0]),
        "reason": "invalid_provenance",
    }]


def test_public_universe_is_seedless_and_hides_private_repository_roots():
    inventory, universe, prior_trust, universe_key, _ = _frozen_inputs()
    serialized = json.dumps(universe, ensure_ascii=False)

    assert "selection_seed" not in serialized
    assert "/private/work" not in serialized
    assert universe["provenance"]["private_inventory_sha256"] == (
        inventory["inventory_sha256"]
    )
    candidate_universe.validate_frozen_universe(
        universe,
        trusted_universe_public_keys={
            candidate_universe.public_key_text(universe_key)
        },
        expected_universe_sha256=universe["universe_sha256"],
        **prior_trust,
    )


def test_frozen_universe_rejects_tampering_and_self_signed_replacement():
    _, universe, prior_trust, universe_key, _ = _frozen_inputs()
    tampered = deepcopy(universe)
    tampered["candidates"][0]["case"]["request"] = "tampered"
    with pytest.raises(ValueError, match="universe digest mismatch"):
        candidate_universe.validate_frozen_universe(
            tampered,
            trusted_universe_public_keys={
                candidate_universe.public_key_text(universe_key)
            },
            expected_universe_sha256=universe["universe_sha256"],
            **prior_trust,
        )

    attacker_key = Ed25519PrivateKey.generate()
    replacement_draft = candidate_universe.unseal_frozen_universe(universe)
    replacement = candidate_universe.seal_frozen_universe(
        replacement_draft,
        attacker_key,
        expected_draft_sha256=replacement_draft["universe_sha256"],
        prior_snapshot=prior_trust["prior_snapshot"],
        trusted_prior_snapshot_public_keys=prior_trust[
            "trusted_prior_snapshot_public_keys"
        ],
        expected_prior_snapshot_sha256=prior_trust[
            "expected_prior_snapshot_sha256"
        ],
    )
    with pytest.raises(ValueError, match="universe signer is not trusted"):
        candidate_universe.validate_frozen_universe(
            replacement,
            trusted_universe_public_keys={
                candidate_universe.public_key_text(universe_key)
            },
            expected_universe_sha256=replacement["universe_sha256"],
            **prior_trust,
        )


def test_frozen_universe_rejects_invalid_provenance_before_sealing():
    _, universe, prior_trust, universe_key, _ = _frozen_inputs()
    draft = candidate_universe.unseal_frozen_universe(universe)
    draft["provenance"]["source_snapshot_sha256"] = "not-a-hash"
    draft["universe_sha256"] = candidate_universe._signed_digest(
        draft, "universe_sha256"
    )

    with pytest.raises(ValueError, match="provenance"):
        candidate_universe.seal_frozen_universe(
            draft,
            universe_key,
            expected_draft_sha256=draft["universe_sha256"],
            prior_snapshot=prior_trust["prior_snapshot"],
            trusted_prior_snapshot_public_keys=prior_trust[
                "trusted_prior_snapshot_public_keys"
            ],
            expected_prior_snapshot_sha256=prior_trust[
                "expected_prior_snapshot_sha256"
            ],
        )


def test_frozen_universe_rejects_malformed_candidate_before_sealing():
    _, universe, prior_trust, universe_key, _ = _frozen_inputs()
    draft = candidate_universe.unseal_frozen_universe(universe)
    draft["candidates"][0] = {}
    draft["universe_sha256"] = candidate_universe._signed_digest(
        draft, "universe_sha256"
    )

    with pytest.raises(ValueError, match="candidate"):
        candidate_universe.seal_frozen_universe(
            draft,
            universe_key,
            expected_draft_sha256=draft["universe_sha256"],
            prior_snapshot=prior_trust["prior_snapshot"],
            trusted_prior_snapshot_public_keys=prior_trust[
                "trusted_prior_snapshot_public_keys"
            ],
            expected_prior_snapshot_sha256=prior_trust[
                "expected_prior_snapshot_sha256"
            ],
        )


def test_frozen_universe_rejects_rollbacked_prior_commit_snapshot():
    _, universe, prior_trust, universe_key, _ = _frozen_inputs()
    rollbacked = {
        **prior_trust,
        "prior_snapshot": {
            **prior_trust["prior_snapshot"],
            "commits": [_sha("different-prior")],
        },
    }

    with pytest.raises(ValueError, match="prior snapshot digest mismatch"):
        candidate_universe.validate_frozen_universe(
            universe,
            trusted_universe_public_keys={
                candidate_universe.public_key_text(universe_key)
            },
            expected_universe_sha256=universe["universe_sha256"],
            **rollbacked,
        )


def test_post_freeze_seed_receipt_is_bound_to_approved_universe():
    _, universe, _, _, seed_key = _frozen_inputs()
    receipt = candidate_universe.issue_seed_receipt(
        universe,
        approved_universe_sha256=universe["universe_sha256"],
        seed="batch-b-independent-seed-v1",
        signer_private_key=seed_key,
    )

    assert receipt["universe_sha256"] == universe["universe_sha256"]
    assert receipt["seed"] == "batch-b-independent-seed-v1"
    with pytest.raises(ValueError, match="approved universe hash mismatch"):
        candidate_universe.issue_seed_receipt(
            universe,
            approved_universe_sha256="0" * 64,
            seed="different-seed",
            signer_private_key=seed_key,
        )


def test_signed_receipts_reject_malformed_document_shapes():
    label_key = Ed25519PrivateKey.generate()
    seed_key = Ed25519PrivateKey.generate()
    malformed_label = candidate_universe._seal_document(
        {
            "schema_version": 1,
            "status": "approved",
            "signoff": {
                "signer": "independent-label-reviewer-v1",
                "signer_public_key": "",
                "signature": "",
            },
            "receipt_sha256": "",
        },
        private_key=label_key,
        digest_field="receipt_sha256",
    )
    malformed_seed = candidate_universe._seal_document(
        {
            "schema_version": 1,
            "status": "approved",
            "universe_sha256": "0" * 64,
            "signoff": {
                "signer": "post-freeze-seed-signer-v1",
                "signer_public_key": "",
                "signature": "",
            },
            "receipt_sha256": "",
        },
        private_key=seed_key,
        digest_field="receipt_sha256",
    )

    with pytest.raises(ValueError, match="label receipt fields"):
        candidate_universe._validate_label_receipt(
            malformed_label,
            trusted_label_public_keys={
                candidate_universe.public_key_text(label_key)
            },
            expected_label_receipt_sha256=malformed_label["receipt_sha256"],
        )
    with pytest.raises(ValueError, match="seed receipt fields"):
        candidate_universe._validate_seed_receipt(
            malformed_seed,
            trusted_seed_public_keys={
                candidate_universe.public_key_text(seed_key)
            },
            expected_seed_receipt_sha256=malformed_seed["receipt_sha256"],
        )


@pytest.mark.parametrize("field", ["signer_public_key", "signature"])
def test_signed_receipt_rejects_non_string_signoff_values(field: str):
    seed_key = Ed25519PrivateKey.generate()
    public_key = candidate_universe.public_key_text(seed_key)
    receipt = candidate_universe.issue_seed_receipt(
        {"status": "frozen", "universe_sha256": "0" * 64},
        approved_universe_sha256="0" * 64,
        seed="batch-b-independent-seed-v1",
        signer_private_key=seed_key,
    )
    receipt["signoff"][field] = []

    with pytest.raises(ValueError, match="seed receipt signoff"):
        candidate_universe._validate_seed_receipt(
            receipt,
            trusted_seed_public_keys={public_key},
            expected_seed_receipt_sha256=receipt["receipt_sha256"],
        )


def test_selection_bundle_is_deterministic_and_bound_to_seed_receipt():
    _, universe, prior_trust, universe_key, seed_key = _frozen_inputs()
    receipt = candidate_universe.issue_seed_receipt(
        universe,
        approved_universe_sha256=universe["universe_sha256"],
        seed="batch-b-independent-seed-v1",
        signer_private_key=seed_key,
    )
    kwargs = {
        "universe": universe,
        "seed_receipt": receipt,
        **prior_trust,
        "trusted_universe_public_keys": {
            candidate_universe.public_key_text(universe_key)
        },
        "trusted_seed_public_keys": {
            candidate_universe.public_key_text(seed_key)
        },
        "expected_universe_sha256": universe["universe_sha256"],
        "expected_seed_receipt_sha256": receipt["receipt_sha256"],
    }

    first = candidate_universe.build_selection_bundle(**kwargs)
    second = candidate_universe.build_selection_bundle(**kwargs)

    assert first == second
    assert len(first["selection"]["cases"]) == 10
    assert first["frozen_universe_sha256"] == universe["universe_sha256"]
    assert first["seed_receipt_sha256"] == receipt["receipt_sha256"]

    wrong_receipt = deepcopy(receipt)
    wrong_receipt["seed"] = "tampered"
    with pytest.raises(ValueError, match="seed receipt digest mismatch"):
        candidate_universe.build_selection_bundle(
            **{**kwargs, "seed_receipt": wrong_receipt}
        )


def test_selection_fails_closed_when_observed_quota_is_insufficient():
    records = [
        record for record in _records()
        if record["ambiguity"] != "high"
    ]
    _, universe, prior_trust, universe_key, seed_key = _frozen_inputs(records)
    receipt = candidate_universe.issue_seed_receipt(
        universe,
        approved_universe_sha256=universe["universe_sha256"],
        seed="batch-b-independent-seed-v1",
        signer_private_key=seed_key,
    )

    with pytest.raises(ValueError, match="cannot satisfy frozen quotas"):
        candidate_universe.build_selection_bundle(
            universe,
            seed_receipt=receipt,
            **prior_trust,
            trusted_universe_public_keys={
                candidate_universe.public_key_text(universe_key)
            },
            trusted_seed_public_keys={
                candidate_universe.public_key_text(seed_key)
            },
            expected_universe_sha256=universe["universe_sha256"],
            expected_seed_receipt_sha256=receipt["receipt_sha256"],
        )


def test_current_omc_session_record_is_not_enough_without_completion_receipt():
    session = {
        "confirmation": {"status": "confirmed"},
        "git": {"head": "b66bd88", "branch": "main"},
    }

    assert session["confirmation"]["status"] == "confirmed"
    assert "followup_commit" not in session["git"]
    assert candidate_universe.session_has_explicit_completion_receipt(session) is False


def test_session_collector_requires_explicit_completion_receipt(tmp_path: Path):
    repo = tmp_path / "private-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "observed@example.com")
    _git(repo, "config", "user.name", "Observed Fixture")
    source_file = repo / "src" / "feature.py"
    source_file.parent.mkdir()
    source_file.write_text("value = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    baseline_commit = _git(repo, "rev-parse", "HEAD")
    source_file.write_text("value = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "followup")
    followup_commit = _git(repo, "rev-parse", "HEAD")
    session_id = "20260801T010101-observed"
    session_dir = repo / ".omc" / "state" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = {
        "session_id": session_id,
        "request": "Add an observed behavior",
        "created_at": "2026-08-01T01:01:01+09:00",
        "confirmation": {"status": "confirmed"},
        "git": {"head": baseline_commit[:7], "branch": "main"},
    }
    (session_dir / "session.json").write_text(json.dumps(session))
    manifest = {
        "schema_version": 1,
        "observed_from": "2026-08-01",
        "observed_through": "2026-08-31",
        "provider_ledger_cutoff": "2026-09-01T00:00:00+09:00",
        "sources": [{
            "source_record_id": "observed-real-01",
            "repo_alias": "repo-private",
            "repository_root": str(repo),
            "session_id": session_id,
            "context_candidate_paths": ["src/feature.py"],
            "surface": "ui_state",
            "ambiguity": "low",
            "selected_object": False,
        }],
    }

    missing = candidate_universe.collect_inventory_from_session_manifest(manifest)

    assert missing["audit"]["accepted_count"] == 0
    assert missing["audit"]["rejected_reason_counts"] == {
        "invalid_provenance": 1
    }

    completion = {
        "schema_version": 1,
        "session_id": session_id,
        "request_sha256": candidate_universe.canonical_digest(session["request"]),
        "baseline_commit": baseline_commit,
        "followup_commit": followup_commit,
        "completed_at": "2026-08-01T02:01:01+09:00",
        "changed_paths": ["src/feature.py"],
        "provider_outputs_available": False,
    }
    (session_dir / "completion.json").write_text(json.dumps(completion))

    collected = candidate_universe.collect_inventory_from_session_manifest(manifest)

    assert collected["audit"]["accepted_count"] == 1
    assert collected["accepted_records"][0]["followup_commit"] == followup_commit
    assert collected["accepted_records"][0]["completion_source"] == (
        "explicit_completion_receipt"
    )


def test_session_collector_rejects_receipt_without_git_evidence(tmp_path: Path):
    repo = tmp_path / "not-a-git-repo"
    session_id = "20260802T010101-fabricated"
    session_dir = repo / ".omc" / "state" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    request = "Fabricated observed behavior"
    (session_dir / "session.json").write_text(json.dumps({
        "session_id": session_id,
        "request": request,
        "created_at": "2026-08-02T01:01:01+09:00",
        "confirmation": {"status": "confirmed"},
        "git": {"head": _sha("fake-baseline")[:7], "branch": "main"},
    }))
    (session_dir / "completion.json").write_text(json.dumps({
        "schema_version": 1,
        "session_id": session_id,
        "request_sha256": candidate_universe.canonical_digest(request),
        "baseline_commit": _sha("fake-baseline"),
        "followup_commit": _sha("fake-followup"),
        "completed_at": "2026-08-02T02:01:01+09:00",
        "changed_paths": ["src/feature.py"],
        "provider_outputs_available": False,
    }))
    manifest = {
        "schema_version": 1,
        "observed_from": "2026-08-01",
        "observed_through": "2026-08-31",
        "provider_ledger_cutoff": "2026-09-01T00:00:00+09:00",
        "sources": [{
            "source_record_id": "fabricated-01",
            "repo_alias": "repo-private",
            "repository_root": str(repo),
            "session_id": session_id,
            "context_candidate_paths": ["src/feature.py"],
            "surface": "ui_state",
            "ambiguity": "low",
            "selected_object": False,
        }],
    }

    collected = candidate_universe.collect_inventory_from_session_manifest(manifest)

    assert collected["audit"]["accepted_count"] == 0
    assert collected["audit"]["rejected_reason_counts"] == {
        "invalid_provenance": 1
    }


def test_session_collector_audits_malformed_session_instead_of_crashing(
    tmp_path: Path,
):
    repo = tmp_path / "malformed-session"
    session_id = "20260803T010101-malformed"
    session_dir = repo / ".omc" / "state" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(json.dumps([]))
    (session_dir / "completion.json").write_text(json.dumps({
        "schema_version": 1,
        "session_id": session_id,
        "request_sha256": candidate_universe.canonical_digest("request"),
        "baseline_commit": _sha("baseline"),
        "followup_commit": _sha("followup"),
        "completed_at": "2026-08-03T02:01:01+09:00",
        "changed_paths": ["src/feature.py"],
        "provider_outputs_available": False,
    }))
    manifest = {
        "schema_version": 1,
        "observed_from": "2026-08-01",
        "observed_through": "2026-08-31",
        "provider_ledger_cutoff": "2026-09-01T00:00:00+09:00",
        "sources": [{
            "source_record_id": "malformed-01",
            "repo_alias": "repo-private",
            "repository_root": str(repo),
            "session_id": session_id,
            "context_candidate_paths": ["src/feature.py"],
            "surface": "ui_state",
            "ambiguity": "low",
            "selected_object": False,
        }],
    }

    collected = candidate_universe.collect_inventory_from_session_manifest(manifest)

    assert collected["audit"]["accepted_count"] == 0
    assert collected["audit"]["rejected_count"] == 1


def test_collect_sessions_cli_writes_private_inventory(tmp_path: Path):
    manifest_path = tmp_path / "sources.json"
    output_path = tmp_path / "inventory.json"
    private_key_path = tmp_path / "collector.key"
    collector_key = Ed25519PrivateKey.generate()
    private_key_path.write_text(base64.b64encode(collector_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )).decode("ascii"))
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "observed_from": "2026-08-01",
        "observed_through": "2026-08-31",
        "provider_ledger_cutoff": "2026-09-01T00:00:00+09:00",
        "sources": [{
            "source_record_id": "missing-receipt",
            "repo_alias": "repo-private",
            "repository_root": str(tmp_path / "missing"),
            "session_id": "missing-session",
            "context_candidate_paths": ["src/feature.py"],
            "surface": "ui_state",
            "ambiguity": "low",
            "selected_object": False,
        }],
    }))

    result = candidate_universe.main([
        "collect-sessions",
        str(manifest_path),
        "--private-key",
        str(private_key_path),
        "--output",
        str(output_path),
    ])

    assert result == 0
    inventory = json.loads(output_path.read_text())
    assert inventory["audit"]["discovered_count"] == 1
    assert inventory["audit"]["accepted_count"] == 0
    assert inventory["status"] == "approved"


def test_label_signing_rejects_inventory_without_trusted_collector_attestation():
    collector_key = Ed25519PrivateKey.generate()
    label_key = Ed25519PrivateKey.generate()
    inventory = _inventory()

    with pytest.raises(ValueError, match="collector"):
        candidate_universe.sign_label_receipt(
            inventory,
            label_key,
            trusted_collector_public_keys={
                candidate_universe.public_key_text(collector_key)
            },
            expected_inventory_sha256=inventory["inventory_sha256"],
        )


def test_prior_snapshot_must_be_signed_and_bound_to_anchor_registry():
    anchor_key = Ed25519PrivateKey.generate()
    prior_key = Ed25519PrivateKey.generate()
    attacker_key = Ed25519PrivateKey.generate()
    anchor_registry = _anchor_registry(anchor_key)
    snapshot = candidate_universe.sign_prior_commit_snapshot(
        [_sha("prior-commit")],
        anchor_registry_sha256=anchor_registry["registry_sha256"],
        signer_private_key=attacker_key,
    )

    with pytest.raises(ValueError, match="prior snapshot signer is not trusted"):
        candidate_universe._validate_prior_evidence(
            prior_snapshot=snapshot,
            trusted_prior_snapshot_public_keys={
                candidate_universe.public_key_text(prior_key)
            },
            expected_prior_snapshot_sha256=snapshot["snapshot_sha256"],
            prior_anchor_registry=anchor_registry,
            trusted_anchor_public_keys={
                candidate_universe.public_key_text(anchor_key)
            },
            expected_anchor_registry_sha256=anchor_registry["registry_sha256"],
        )


def test_session_collector_audits_invalid_source_value_types(tmp_path: Path):
    manifest = {
        "schema_version": 1,
        "observed_from": "2026-08-01",
        "observed_through": "2026-08-31",
        "provider_ledger_cutoff": "2026-09-01T00:00:00+09:00",
        "sources": [{
            "source_record_id": "malformed-source",
            "repo_alias": "repo-private",
            "repository_root": [],
            "session_id": "missing-session",
            "context_candidate_paths": ["src/feature.py"],
            "surface": "ui_state",
            "ambiguity": "low",
            "selected_object": False,
        }],
    }

    collected = candidate_universe.collect_inventory_from_session_manifest(manifest)

    assert collected["audit"]["accepted_count"] == 0
    assert collected["audit"]["rejected_reason_counts"] == {
        "invalid_provenance": 1
    }


def test_session_collector_matches_unicode_git_paths(tmp_path: Path):
    repo = tmp_path / "unicode-path-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "observed@example.com")
    _git(repo, "config", "user.name", "Observed Fixture")
    source_file = repo / "src" / "기능.py"
    source_file.parent.mkdir()
    source_file.write_text("value = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    baseline_commit = _git(repo, "rev-parse", "HEAD")
    source_file.write_text("value = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "followup")
    followup_commit = _git(repo, "rev-parse", "HEAD")
    session_id = "20260804T010101-unicode"
    session_dir = repo / ".omc" / "state" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    request = "Unicode path behavior"
    (session_dir / "session.json").write_text(json.dumps({
        "session_id": session_id,
        "request": request,
        "created_at": "2026-08-04T01:01:01+09:00",
        "confirmation": {"status": "confirmed"},
        "git": {"head": baseline_commit[:7], "branch": "main"},
    }))
    (session_dir / "completion.json").write_text(json.dumps({
        "schema_version": 1,
        "session_id": session_id,
        "request_sha256": candidate_universe.canonical_digest(request),
        "baseline_commit": baseline_commit,
        "followup_commit": followup_commit,
        "completed_at": "2026-08-04T02:01:01+09:00",
        "changed_paths": ["src/기능.py"],
        "provider_outputs_available": False,
    }))
    manifest = {
        "schema_version": 1,
        "observed_from": "2026-08-01",
        "observed_through": "2026-08-31",
        "provider_ledger_cutoff": "2026-09-01T00:00:00+09:00",
        "sources": [{
            "source_record_id": "unicode-01",
            "repo_alias": "repo-private",
            "repository_root": str(repo),
            "session_id": session_id,
            "context_candidate_paths": ["src/기능.py"],
            "surface": "ui_state",
            "ambiguity": "low",
            "selected_object": False,
        }],
    }

    collected = candidate_universe.collect_inventory_from_session_manifest(manifest)

    assert collected["audit"]["accepted_count"] == 1
