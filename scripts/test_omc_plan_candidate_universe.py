import base64
import json
import os
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta
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


def _collection_anchor(
    tmp_path: Path,
    *,
    committed_at: datetime | None = None,
):
    repo = tmp_path / "collection-anchor"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "anchor@example.com")
    _git(repo, "config", "user.name", "Collection Anchor")
    (repo / "anchor.txt").write_text("frozen\n")
    _git(repo, "add", ".")
    env = None
    if committed_at is not None:
        timestamp = committed_at.isoformat()
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": timestamp,
            "GIT_COMMITTER_DATE": timestamp,
        }
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "collection anchor"],
        check=True,
        env=env,
    )
    commit = _git(repo, "rev-parse", "HEAD")
    committed_at = datetime.fromisoformat(_git(repo, "show", "-s", "--format=%cI"))
    start = committed_at + timedelta(hours=1)
    end = start + timedelta(days=30)
    cutoff = end + timedelta(days=1)
    return repo, commit, start, end, cutoff


def _commit_preregistration_registry(
    repo: Path,
    preregistration: dict,
    *,
    committed_at: datetime | None = None,
) -> tuple[str, str]:
    registry_path = (
        ".omc/benchmarks/plan-preregistrations/"
        f"{preregistration['batch_id']}.json"
    )
    registry_file = repo / registry_path
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(
        candidate_universe.prepare_preregistration_registry_record(
            preregistration
        )
    ))
    _git(repo, "add", registry_path)
    env = None
    if committed_at is not None:
        timestamp = committed_at.isoformat()
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": timestamp,
            "GIT_COMMITTER_DATE": timestamp,
        }
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "register preregistration"],
        check=True,
        env=env,
    )
    return _git(repo, "rev-parse", "HEAD"), registry_path


def _external_registration_receipt(
    preregistration: dict,
    *,
    authority_key: Ed25519PrivateKey,
    registry_commit: str,
    registry_path: str,
    registered_at: datetime,
) -> dict:
    receipt = {
        "schema_version": 1,
        "status": "registered",
        "batch_id": preregistration["batch_id"],
        "preregistration_sha256": preregistration["preregistration_sha256"],
        "registry_commit": registry_commit,
        "registry_path": registry_path,
        "registered_at": registered_at.isoformat(),
        "signoff": {
            "signer": "independent-preregistration-timestamp-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "receipt_sha256": "",
    }
    return candidate_universe._seal_document(
        receipt,
        private_key=authority_key,
        digest_field="receipt_sha256",
    )


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

    tampered = deepcopy(inventory)
    tampered["rejected_records"][0]["reason"] = "document_only_task"
    tampered["audit"]["rejected_reason_counts"] = {"document_only_task": 1}
    tampered["inventory_sha256"] = candidate_universe._inventory_digest(tampered)
    with pytest.raises(ValueError, match="private inventory audit"):
        candidate_universe.seal_private_inventory(
            tampered,
            Ed25519PrivateKey.generate(),
            expected_inventory_sha256=tampered["inventory_sha256"],
        )


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


def test_prospective_preregistration_enforces_first_n_and_task_eligibility(
    tmp_path: Path,
):
    signer_key = Ed25519PrivateKey.generate()
    authority_key = Ed25519PrivateKey.generate()
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(tmp_path)
    pilot_session_ids = [
        "20260811T160453-0862907a",
        "20260811T161251-7d35f901",
        "20260811T162647-0fab68f5",
    ]
    draft = candidate_universe.prepare_collection_preregistration(
        batch_id="fresh-batch-b",
        collection_anchor_commit=anchor_commit,
        collection_anchor_repository_root=str(anchor_repo),
        observed_from=start.isoformat(),
        observed_through=end.isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
        pilot_session_ids=pilot_session_ids,
        registration_authority_public_key=(
            candidate_universe.public_key_text(authority_key)
        ),
    )
    frozen = candidate_universe.seal_collection_preregistration(
        draft,
        signer_key,
        collection_anchor_repository_root=str(anchor_repo),
        expected_preregistration_sha256=draft["preregistration_sha256"],
    )
    trust = {
        "trusted_preregistration_public_keys": {
            candidate_universe.public_key_text(signer_key)
        },
        "expected_preregistration_sha256": frozen["preregistration_sha256"],
    }

    sessions = [{
        "session_id": pilot_session_ids[0],
        "completed_at": (start - timedelta(hours=2)).isoformat(),
        "work_class": "implementation",
    }, {
        "session_id": "too-early",
        "completed_at": (start - timedelta(seconds=1)).isoformat(),
        "work_class": "implementation",
    }]
    sessions.extend({
        "session_id": f"confirmatory-{index:02d}",
        "completed_at": (start + timedelta(minutes=index)).isoformat(),
        "work_class": "implementation",
    } for index in range(1, 17))
    sessions.extend({
        "session_id": f"excluded-{work_class}",
        "completed_at": (start + timedelta(minutes=30 + index)).isoformat(),
        "work_class": work_class,
    } for index, work_class in enumerate((
        "synthetic",
        "document_only",
        "benchmark_maintenance",
    )))

    classified = candidate_universe.classify_preregistered_sessions(
        frozen,
        sessions=sessions,
        **trust,
    )
    dispositions = {
        item["session_id"]: item["disposition"] for item in classified
    }

    assert dispositions[pilot_session_ids[0]] == "pilot_observed"
    assert dispositions["too-early"] == "outside_window"
    assert dispositions["confirmatory-15"] == "confirmatory_candidate"
    assert dispositions["confirmatory-16"] == "collection_limit_exceeded"
    assert dispositions["excluded-synthetic"] == "synthetic_task"
    assert dispositions["excluded-document_only"] == "document_only_task"
    assert dispositions["excluded-benchmark_maintenance"] == (
        "benchmark_maintenance_task"
    )


def test_preregistered_collector_consumes_sampling_policy(
    tmp_path: Path,
    monkeypatch,
):
    signer_key = Ed25519PrivateKey.generate()
    registration_receipt_key = Ed25519PrivateKey.generate()
    source_snapshot_key = Ed25519PrivateKey.generate()
    collector_key = Ed25519PrivateKey.generate()
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(tmp_path)
    draft = candidate_universe.prepare_collection_preregistration(
        batch_id="fresh-batch-b",
        collection_anchor_commit=anchor_commit,
        collection_anchor_repository_root=str(anchor_repo),
        observed_from=start.isoformat(),
        observed_through=end.isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
        pilot_session_ids=["pilot-01"],
        registration_authority_public_key=(
            candidate_universe.public_key_text(registration_receipt_key)
        ),
    )
    frozen = candidate_universe.seal_collection_preregistration(
        draft,
        signer_key,
        collection_anchor_repository_root=str(anchor_repo),
        expected_preregistration_sha256=draft["preregistration_sha256"],
    )
    registry_commit, registry_path = _commit_preregistration_registry(
        anchor_repo, frozen
    )
    registration_receipt = _external_registration_receipt(
        frozen,
        authority_key=registration_receipt_key,
        registry_commit=registry_commit,
        registry_path=registry_path,
        registered_at=start - timedelta(seconds=1),
    )
    records = _records(19)
    work_classes = ["implementation"] * 16 + [
        "synthetic",
        "document_only",
        "benchmark_maintenance",
    ]
    for index, record in enumerate(records):
        record["confirmed_at"] = (start + timedelta(minutes=index)).isoformat()
        record["completed_at"] = (
            start + timedelta(minutes=index, seconds=30)
        ).isoformat()
    raw_inventory = candidate_universe.collect_private_inventory(
        records,
        observed_from=start.date().isoformat(),
        observed_through=end.date().isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
    )
    monkeypatch.setattr(
        candidate_universe,
        "collect_inventory_from_session_manifest",
        lambda manifest: raw_inventory,
    )
    sources = [{
            "source_record_id": record["source_record_id"],
            "repo_alias": record["repo_alias"],
            "repository_root": record["repository_root"],
            "session_id": record["session_id"],
            "context_candidate_paths": record["context_candidate_paths"],
            "surface": record["surface"],
            "ambiguity": record["ambiguity"],
            "selected_object": record["selected_object"],
            "work_class": work_classes[index],
        } for index, record in enumerate(records)]
    source_snapshot_draft = (
        candidate_universe.prepare_preregistered_source_snapshot(
            sources=sources,
            preregistration=frozen,
            trusted_preregistration_public_keys={
                candidate_universe.public_key_text(signer_key)
            },
            expected_preregistration_sha256=(
                frozen["preregistration_sha256"]
            ),
            preregistration_registry_repository_root=str(anchor_repo),
            preregistration_registry_commit=registry_commit,
            preregistration_registry_path=registry_path,
            registration_receipt=registration_receipt,
            trusted_registration_receipt_public_keys={
                candidate_universe.public_key_text(registration_receipt_key)
            },
            expected_registration_receipt_sha256=(
                registration_receipt["receipt_sha256"]
            ),
        )
    )
    source_snapshot = candidate_universe.seal_preregistered_source_snapshot(
        source_snapshot_draft,
        source_snapshot_key,
        expected_source_snapshot_sha256=(
            source_snapshot_draft["source_snapshot_sha256"]
        ),
    )
    same_signer_snapshot = (
        candidate_universe.seal_preregistered_source_snapshot(
            source_snapshot_draft,
            signer_key,
            expected_source_snapshot_sha256=(
                source_snapshot_draft["source_snapshot_sha256"]
            ),
        )
    )
    with pytest.raises(ValueError, match="signer must be independent"):
        candidate_universe.collect_preregistered_inventory_from_session_manifest(
            same_signer_snapshot,
            preregistration=frozen,
            trusted_preregistration_public_keys={
                candidate_universe.public_key_text(signer_key)
            },
            expected_preregistration_sha256=frozen["preregistration_sha256"],
            trusted_source_snapshot_public_keys={
                candidate_universe.public_key_text(signer_key)
            },
            expected_source_snapshot_sha256=(
                same_signer_snapshot["source_snapshot_sha256"]
            ),
            collector_public_key=candidate_universe.public_key_text(collector_key),
            preregistration_registry_repository_root=str(anchor_repo),
            registration_receipt=registration_receipt,
            trusted_registration_receipt_public_keys={
                candidate_universe.public_key_text(registration_receipt_key)
            },
            expected_registration_receipt_sha256=(
                registration_receipt["receipt_sha256"]
            ),
        )
    authority_signer_snapshot = (
        candidate_universe.seal_preregistered_source_snapshot(
            source_snapshot_draft,
            registration_receipt_key,
            expected_source_snapshot_sha256=(
                source_snapshot_draft["source_snapshot_sha256"]
            ),
        )
    )
    with pytest.raises(ValueError, match="signer must be independent"):
        candidate_universe.collect_preregistered_inventory_from_session_manifest(
            authority_signer_snapshot,
            preregistration=frozen,
            trusted_preregistration_public_keys={
                candidate_universe.public_key_text(signer_key)
            },
            expected_preregistration_sha256=frozen["preregistration_sha256"],
            trusted_source_snapshot_public_keys={
                candidate_universe.public_key_text(registration_receipt_key)
            },
            expected_source_snapshot_sha256=(
                authority_signer_snapshot["source_snapshot_sha256"]
            ),
            collector_public_key=candidate_universe.public_key_text(collector_key),
            preregistration_registry_repository_root=str(anchor_repo),
            registration_receipt=registration_receipt,
            trusted_registration_receipt_public_keys={
                candidate_universe.public_key_text(registration_receipt_key)
            },
            expected_registration_receipt_sha256=(
                registration_receipt["receipt_sha256"]
            ),
        )

    inventory = candidate_universe.collect_preregistered_inventory_from_session_manifest(
        source_snapshot,
        preregistration=frozen,
        trusted_preregistration_public_keys={
            candidate_universe.public_key_text(signer_key)
        },
        expected_preregistration_sha256=frozen["preregistration_sha256"],
        trusted_source_snapshot_public_keys={
            candidate_universe.public_key_text(source_snapshot_key)
        },
        expected_source_snapshot_sha256=(
            source_snapshot["source_snapshot_sha256"]
        ),
        collector_public_key=candidate_universe.public_key_text(collector_key),
        preregistration_registry_repository_root=str(anchor_repo),
        registration_receipt=registration_receipt,
        trusted_registration_receipt_public_keys={
            candidate_universe.public_key_text(registration_receipt_key)
        },
        expected_registration_receipt_sha256=(
            registration_receipt["receipt_sha256"]
        ),
    )

    assert inventory["schema_version"] == 2
    assert inventory["preregistration_sha256"] == frozen[
        "preregistration_sha256"
    ]
    assert inventory["source_snapshot_sha256"] == source_snapshot[
        "source_snapshot_sha256"
    ]
    assert inventory["collector_public_key"] == candidate_universe.public_key_text(
        collector_key
    )
    assert inventory["audit"]["accepted_count"] == 15
    assert inventory["audit"]["rejected_reason_counts"] == {
        "benchmark_maintenance_task": 1,
        "collection_limit_exceeded": 1,
        "document_only_task": 1,
        "synthetic_task": 1,
    }
    approved_inventory = candidate_universe.seal_preregistered_inventory(
        inventory,
        collector_key,
        source_snapshot=source_snapshot,
        trusted_source_snapshot_public_keys={
            candidate_universe.public_key_text(source_snapshot_key)
        },
        expected_source_snapshot_sha256=(
            source_snapshot["source_snapshot_sha256"]
        ),
        expected_preregistration_sha256=frozen["preregistration_sha256"],
        expected_inventory_sha256=inventory["inventory_sha256"],
    )
    label_key = Ed25519PrivateKey.generate()
    collector_trust = {
        "trusted_collector_public_keys": {
            candidate_universe.public_key_text(collector_key)
        },
        "expected_inventory_sha256": approved_inventory["inventory_sha256"],
    }
    with pytest.raises(ValueError, match="source snapshot evidence is required"):
        candidate_universe.sign_label_receipt(
            approved_inventory,
            label_key,
            **collector_trust,
        )
    source_evidence = {
        "source_snapshot": source_snapshot,
        "trusted_source_snapshot_public_keys": {
            candidate_universe.public_key_text(source_snapshot_key)
        },
        "expected_source_snapshot_sha256": source_snapshot[
            "source_snapshot_sha256"
        ],
        "expected_preregistration_sha256": frozen["preregistration_sha256"],
    }
    with pytest.raises(ValueError, match="complete provenance evidence is required"):
        candidate_universe.sign_label_receipt(
            approved_inventory,
            label_key,
            **collector_trust,
            **source_evidence,
        )
    complete_provenance = {
        **source_evidence,
        "preregistration": frozen,
        "trusted_preregistration_public_keys": {
            candidate_universe.public_key_text(signer_key)
        },
        "preregistration_registry_repository_root": str(anchor_repo),
        "registration_receipt": registration_receipt,
        "trusted_registration_receipt_public_keys": {
            candidate_universe.public_key_text(registration_receipt_key)
        },
        "expected_registration_receipt_sha256": registration_receipt[
            "receipt_sha256"
        ],
    }
    for conflicting_collector_key in (signer_key, registration_receipt_key):
        conflicting_inventory = deepcopy(inventory)
        conflicting_inventory["collector_public_key"] = (
            candidate_universe.public_key_text(conflicting_collector_key)
        )
        conflicting_inventory["inventory_sha256"] = (
            candidate_universe._inventory_digest(conflicting_inventory)
        )
        conflicting_inventory = candidate_universe._seal_document(
            conflicting_inventory | {"status": "approved"},
            private_key=conflicting_collector_key,
            digest_field="inventory_sha256",
        )
        with pytest.raises(ValueError, match="collector must be independent"):
            candidate_universe.sign_label_receipt(
                conflicting_inventory,
                label_key,
                trusted_collector_public_keys={
                    candidate_universe.public_key_text(conflicting_collector_key)
                },
                expected_inventory_sha256=conflicting_inventory[
                    "inventory_sha256"
                ],
                **complete_provenance,
            )
    label_receipt = candidate_universe.sign_label_receipt(
        approved_inventory,
        label_key,
        **collector_trust,
        **complete_provenance,
    )
    assert label_receipt["private_inventory_sha256"] == approved_inventory[
        "inventory_sha256"
    ]
    anchor_key = Ed25519PrivateKey.generate()
    anchor_registry = _anchor_registry(anchor_key)
    prior_snapshot_key = Ed25519PrivateKey.generate()
    prior_snapshot = candidate_universe.sign_prior_commit_snapshot(
        [_sha("preregistered-prior-commit")],
        anchor_registry_sha256=anchor_registry["registry_sha256"],
        signer_private_key=prior_snapshot_key,
    )
    universe_args = {
        **collector_trust,
        "label_receipt": label_receipt,
        "trusted_label_public_keys": {
            candidate_universe.public_key_text(label_key)
        },
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
        "batch_id": "fresh-batch-b-downstream",
        "source_snapshot_sha256": source_snapshot["source_snapshot_sha256"],
    }
    with pytest.raises(ValueError, match="source snapshot evidence is required"):
        candidate_universe.prepare_frozen_universe(
            approved_inventory,
            **universe_args,
        )
    with pytest.raises(ValueError, match="complete provenance evidence is required"):
        candidate_universe.prepare_frozen_universe(
            approved_inventory,
            **universe_args,
            source_snapshot=source_snapshot,
            trusted_source_snapshot_public_keys={
                candidate_universe.public_key_text(source_snapshot_key)
            },
            expected_preregistration_sha256=frozen["preregistration_sha256"],
        )
    universe_provenance = {
        key: value
        for key, value in complete_provenance.items()
        if key != "expected_source_snapshot_sha256"
    }
    frozen_universe = candidate_universe.prepare_frozen_universe(
        approved_inventory,
        **universe_args,
        **universe_provenance,
    )
    assert frozen_universe["provenance"]["source_snapshot_sha256"] == (
        source_snapshot["source_snapshot_sha256"]
    )

    unregistered_snapshot = deepcopy(source_snapshot)
    unregistered_snapshot["registration_receipt_sha256"] = "a" * 64
    unregistered_snapshot["source_snapshot_sha256"] = (
        candidate_universe._signed_digest(
            unregistered_snapshot, "source_snapshot_sha256"
        )
    )
    unregistered_snapshot = candidate_universe._seal_document(
        unregistered_snapshot,
        private_key=source_snapshot_key,
        digest_field="source_snapshot_sha256",
    )
    unregistered_inventory = deepcopy(inventory)
    unregistered_inventory["source_snapshot_sha256"] = unregistered_snapshot[
        "source_snapshot_sha256"
    ]
    unregistered_inventory["inventory_sha256"] = (
        candidate_universe._inventory_digest(unregistered_inventory)
    )
    unregistered_inventory = candidate_universe._seal_document(
        unregistered_inventory | {"status": "approved"},
        private_key=collector_key,
        digest_field="inventory_sha256",
    )
    unregistered_provenance = {
        **complete_provenance,
        "source_snapshot": unregistered_snapshot,
        "expected_source_snapshot_sha256": unregistered_snapshot[
            "source_snapshot_sha256"
        ],
    }
    unregistered_collector_trust = {
        **collector_trust,
        "expected_inventory_sha256": unregistered_inventory["inventory_sha256"],
    }
    with pytest.raises(
        ValueError, match="registration receipt source snapshot mismatch"
    ):
        candidate_universe.sign_label_receipt(
            unregistered_inventory,
            label_key,
            **unregistered_collector_trust,
            **unregistered_provenance,
        )

    source_signed_inventory = deepcopy(inventory)
    source_signed_inventory["collector_public_key"] = (
        candidate_universe.public_key_text(source_snapshot_key)
    )
    source_signed_inventory["inventory_sha256"] = (
        candidate_universe._inventory_digest(source_signed_inventory)
    )
    source_signed_inventory = candidate_universe._seal_document(
        source_signed_inventory | {"status": "approved"},
        private_key=source_snapshot_key,
        digest_field="inventory_sha256",
    )
    with pytest.raises(ValueError, match="collector must be independent"):
        candidate_universe.sign_label_receipt(
            source_signed_inventory,
            label_key,
            trusted_collector_public_keys={
                candidate_universe.public_key_text(source_snapshot_key)
            },
            expected_inventory_sha256=source_signed_inventory[
                "inventory_sha256"
            ],
            source_snapshot=source_snapshot,
            trusted_source_snapshot_public_keys={
                candidate_universe.public_key_text(source_snapshot_key)
            },
            expected_source_snapshot_sha256=(
                source_snapshot["source_snapshot_sha256"]
            ),
            expected_preregistration_sha256=frozen["preregistration_sha256"],
            preregistration=frozen,
            trusted_preregistration_public_keys={
                candidate_universe.public_key_text(signer_key)
            },
            preregistration_registry_repository_root=str(anchor_repo),
            registration_receipt=registration_receipt,
            trusted_registration_receipt_public_keys={
                candidate_universe.public_key_text(registration_receipt_key)
            },
            expected_registration_receipt_sha256=(
                registration_receipt["receipt_sha256"]
            ),
        )
    with pytest.raises(
        ValueError, match="preregistered inventory requires source snapshot"
    ):
        candidate_universe.seal_private_inventory(
            inventory,
            source_snapshot_key,
            expected_inventory_sha256=inventory["inventory_sha256"],
        )
    with pytest.raises(ValueError, match="collector must be independent"):
        candidate_universe.seal_preregistered_inventory(
            inventory,
            source_snapshot_key,
            source_snapshot=source_snapshot,
            trusted_source_snapshot_public_keys={
                candidate_universe.public_key_text(source_snapshot_key)
            },
            expected_source_snapshot_sha256=(
                source_snapshot["source_snapshot_sha256"]
            ),
            expected_preregistration_sha256=frozen["preregistration_sha256"],
            expected_inventory_sha256=inventory["inventory_sha256"],
        )
    tampered_receipt = deepcopy(registration_receipt)
    tampered_receipt["registered_at"] = end.isoformat()
    with pytest.raises(ValueError, match="registration receipt digest mismatch"):
        candidate_universe.collect_preregistered_inventory_from_session_manifest(
            source_snapshot,
            preregistration=frozen,
            trusted_preregistration_public_keys={
                candidate_universe.public_key_text(signer_key)
            },
            expected_preregistration_sha256=frozen["preregistration_sha256"],
            trusted_source_snapshot_public_keys={
                candidate_universe.public_key_text(source_snapshot_key)
            },
            expected_source_snapshot_sha256=(
                source_snapshot["source_snapshot_sha256"]
            ),
            collector_public_key=candidate_universe.public_key_text(collector_key),
            preregistration_registry_repository_root=str(anchor_repo),
            registration_receipt=tampered_receipt,
            trusted_registration_receipt_public_keys={
                candidate_universe.public_key_text(registration_receipt_key)
            },
            expected_registration_receipt_sha256=(
                registration_receipt["receipt_sha256"]
            ),
        )

    source_input_path = tmp_path / "source-ledger.json"
    source_draft_path = tmp_path / "source-snapshot-draft.json"
    manifest_path = tmp_path / "source-snapshot-frozen.json"
    preregistration_path = tmp_path / "preregistration.json"
    registration_receipt_path = tmp_path / "registration-receipt.json"
    source_key_path = tmp_path / "source-snapshot.key"
    collector_key_path = tmp_path / "collector.key"
    output_path = tmp_path / "preregistered-inventory.json"
    label_key_path = tmp_path / "label.key"
    label_output_path = tmp_path / "label-receipt.json"
    collector_key = Ed25519PrivateKey.generate()
    source_input_path.write_text(json.dumps({
        "schema_version": 1,
        "sources": sources,
    }))
    preregistration_path.write_text(json.dumps(frozen))
    registration_receipt_path.write_text(json.dumps(registration_receipt))
    source_key_path.write_text(base64.b64encode(
        source_snapshot_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode("ascii"))
    collector_key_path.write_text(base64.b64encode(collector_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )).decode("ascii"))
    label_key_path.write_text(base64.b64encode(label_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )).decode("ascii"))

    assert candidate_universe.main([
        "prepare-source-snapshot",
        str(source_input_path),
        str(preregistration_path),
        str(registration_receipt_path),
        "--trusted-preregistration-public-key",
        candidate_universe.public_key_text(signer_key),
        "--expected-preregistration-sha256",
        frozen["preregistration_sha256"],
        "--preregistration-registry-repository-root", str(anchor_repo),
        "--preregistration-registry-commit", registry_commit,
        "--preregistration-registry-path", registry_path,
        "--trusted-registration-receipt-public-key",
        candidate_universe.public_key_text(registration_receipt_key),
        "--expected-registration-receipt-sha256",
        registration_receipt["receipt_sha256"],
        "--output",
        str(source_draft_path),
    ]) == 0
    cli_source_draft = json.loads(source_draft_path.read_text())
    assert candidate_universe.main([
        "seal-source-snapshot",
        str(source_draft_path),
        "--private-key",
        str(source_key_path),
        "--approved-source-snapshot-sha256",
        cli_source_draft["source_snapshot_sha256"],
        "--output",
        str(manifest_path),
    ]) == 0
    source_snapshot = json.loads(manifest_path.read_text())

    assert candidate_universe.main([
        "collect-preregistered-sessions",
        str(manifest_path),
        str(preregistration_path),
        str(registration_receipt_path),
        "--trusted-preregistration-public-key",
        candidate_universe.public_key_text(signer_key),
        "--expected-preregistration-sha256",
        frozen["preregistration_sha256"],
        "--trusted-source-snapshot-public-key",
        candidate_universe.public_key_text(source_snapshot_key),
        "--expected-source-snapshot-sha256",
        source_snapshot["source_snapshot_sha256"],
        "--preregistration-registry-repository-root", str(anchor_repo),
        "--trusted-registration-receipt-public-key",
        candidate_universe.public_key_text(registration_receipt_key),
        "--expected-registration-receipt-sha256",
        registration_receipt["receipt_sha256"],
        "--private-key",
        str(collector_key_path),
        "--output",
        str(output_path),
    ]) == 0
    cli_inventory = json.loads(output_path.read_text())
    assert cli_inventory["status"] == "approved"
    assert candidate_universe.main([
        "sign-labels",
        str(output_path),
        "--private-key",
        str(label_key_path),
        "--trusted-collector-public-key",
        candidate_universe.public_key_text(collector_key),
        "--expected-inventory-sha256",
        cli_inventory["inventory_sha256"],
        "--source-snapshot",
        str(manifest_path),
        "--trusted-source-snapshot-public-key",
        candidate_universe.public_key_text(source_snapshot_key),
        "--expected-source-snapshot-sha256",
        source_snapshot["source_snapshot_sha256"],
        "--expected-preregistration-sha256",
        frozen["preregistration_sha256"],
        "--preregistration",
        str(preregistration_path),
        "--trusted-preregistration-public-key",
        candidate_universe.public_key_text(signer_key),
        "--preregistration-registry-repository-root",
        str(anchor_repo),
        "--registration-receipt",
        str(registration_receipt_path),
        "--trusted-registration-receipt-public-key",
        candidate_universe.public_key_text(registration_receipt_key),
        "--expected-registration-receipt-sha256",
        registration_receipt["receipt_sha256"],
        "--output",
        str(label_output_path),
    ]) == 0
    assert json.loads(label_output_path.read_text())["status"] == "approved"

    for mutate in (
        lambda value: value["sources"].pop(0),
        lambda value: value["sources"][0].update({"work_class": "document_only"}),
        lambda value: value["preregistration_registry"].update({
            "commit": _sha("different-registry-commit")
        }),
    ):
        tampered = deepcopy(source_snapshot)
        mutate(tampered)
        with pytest.raises(ValueError, match="source snapshot digest mismatch"):
            candidate_universe.collect_preregistered_inventory_from_session_manifest(
                tampered,
                preregistration=frozen,
                trusted_preregistration_public_keys={
                    candidate_universe.public_key_text(signer_key)
                },
                expected_preregistration_sha256=(
                    frozen["preregistration_sha256"]
                ),
                trusted_source_snapshot_public_keys={
                    candidate_universe.public_key_text(source_snapshot_key)
                },
                expected_source_snapshot_sha256=(
                    source_snapshot["source_snapshot_sha256"]
                ),
                collector_public_key=candidate_universe.public_key_text(collector_key),
                preregistration_registry_repository_root=str(anchor_repo),
                registration_receipt=registration_receipt,
                trusted_registration_receipt_public_keys={
                    candidate_universe.public_key_text(
                        registration_receipt_key
                    )
                },
                expected_registration_receipt_sha256=(
                    registration_receipt["receipt_sha256"]
                ),
            )


def test_prospective_preregistration_rejects_post_signoff_window_extension(
    tmp_path: Path,
):
    signer_key = Ed25519PrivateKey.generate()
    authority_key = Ed25519PrivateKey.generate()
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(tmp_path)
    draft = candidate_universe.prepare_collection_preregistration(
        batch_id="fresh-batch-b",
        collection_anchor_commit=anchor_commit,
        collection_anchor_repository_root=str(anchor_repo),
        observed_from=start.isoformat(),
        observed_through=end.isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
        pilot_session_ids=["20260811T160453-0862907a"],
        registration_authority_public_key=(
            candidate_universe.public_key_text(authority_key)
        ),
    )
    frozen = candidate_universe.seal_collection_preregistration(
        draft,
        signer_key,
        collection_anchor_repository_root=str(anchor_repo),
        expected_preregistration_sha256=draft["preregistration_sha256"],
    )
    tampered = deepcopy(frozen)
    tampered["observation_window"]["observed_through"] = (
        (end + timedelta(hours=1)).isoformat()
    )

    with pytest.raises(ValueError, match="preregistration digest mismatch"):
        candidate_universe.validate_collection_preregistration(
            tampered,
            trusted_preregistration_public_keys={
                candidate_universe.public_key_text(signer_key)
            },
            expected_preregistration_sha256=frozen["preregistration_sha256"],
        )


def test_prospective_preregistration_requires_fixed_chronological_window(
    tmp_path: Path,
):
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(tmp_path)
    with pytest.raises(ValueError, match="observation window"):
        candidate_universe.prepare_collection_preregistration(
            batch_id="fresh-batch-b",
            collection_anchor_commit=anchor_commit,
            collection_anchor_repository_root=str(anchor_repo),
            observed_from=end.isoformat(),
            observed_through=start.isoformat(),
            provider_ledger_cutoff=cutoff.isoformat(),
            pilot_session_ids=["20260811T160453-0862907a"],
            registration_authority_public_key=candidate_universe.public_key_text(
                Ed25519PrivateKey.generate()
            ),
        )


def test_prospective_preregistration_rejects_unverified_git_anchor(
    tmp_path: Path,
):
    anchor_repo, _, start, end, cutoff = _collection_anchor(tmp_path)

    with pytest.raises(ValueError, match="collection anchor"):
        candidate_universe.prepare_collection_preregistration(
            batch_id="fresh-batch-b",
            collection_anchor_commit=_sha("missing-anchor"),
            collection_anchor_repository_root=str(anchor_repo),
            observed_from=start.isoformat(),
            observed_through=end.isoformat(),
            provider_ledger_cutoff=cutoff.isoformat(),
            pilot_session_ids=["20260811T160453-0862907a"],
            registration_authority_public_key=candidate_universe.public_key_text(
                Ed25519PrivateKey.generate()
            ),
        )


def test_prospective_preregistration_rejects_late_receipt_for_backdated_commit(
    tmp_path: Path,
):
    signer_key = Ed25519PrivateKey.generate()
    receipt_key = Ed25519PrivateKey.generate()
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(
        tmp_path,
        committed_at=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    draft = candidate_universe.prepare_collection_preregistration(
        batch_id="fresh-batch-b",
        collection_anchor_commit=anchor_commit,
        collection_anchor_repository_root=str(anchor_repo),
        observed_from=start.isoformat(),
        observed_through=end.isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
        pilot_session_ids=["20260811T160453-0862907a"],
        registration_authority_public_key=(
            candidate_universe.public_key_text(receipt_key)
        ),
    )
    frozen = candidate_universe.seal_collection_preregistration(
        draft,
        signer_key,
        collection_anchor_repository_root=str(anchor_repo),
        expected_preregistration_sha256=draft["preregistration_sha256"],
    )
    registry_commit, registry_path = _commit_preregistration_registry(
        anchor_repo,
        frozen,
        committed_at=end + timedelta(hours=1),
    )

    late_receipt = _external_registration_receipt(
        frozen,
        authority_key=receipt_key,
        registry_commit=registry_commit,
        registry_path=registry_path,
        registered_at=end + timedelta(hours=1),
    )
    with pytest.raises(ValueError, match="registration receipt mismatch"):
        candidate_universe.validate_preregistration_registration_receipt(
            late_receipt,
            preregistration=frozen,
            trusted_receipt_public_keys={
                candidate_universe.public_key_text(receipt_key)
            },
            expected_receipt_sha256=late_receipt["receipt_sha256"],
        )


def test_local_authority_batch_is_in_default_revocation_set():
    assert (
        "bf651249b7d2d3c5e159f6e53ebfb9d623a7979c2ecb272cc97055e11e11c434"
        in candidate_universe.REVOKED_COLLECTION_PREREGISTRATION_SHA256S
    )


def test_registration_receipt_rejects_revoked_preregistration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    signer_key = Ed25519PrivateKey.generate()
    authority_key = Ed25519PrivateKey.generate()
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(tmp_path)
    draft = candidate_universe.prepare_collection_preregistration(
        batch_id="revoked-batch-b",
        collection_anchor_commit=anchor_commit,
        collection_anchor_repository_root=str(anchor_repo),
        observed_from=start.isoformat(),
        observed_through=end.isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
        pilot_session_ids=["pilot-01"],
        registration_authority_public_key=(
            candidate_universe.public_key_text(authority_key)
        ),
    )
    frozen = candidate_universe.seal_collection_preregistration(
        draft,
        signer_key,
        collection_anchor_repository_root=str(anchor_repo),
        expected_preregistration_sha256=draft["preregistration_sha256"],
    )
    registry_commit, registry_path = _commit_preregistration_registry(
        anchor_repo,
        frozen,
    )
    receipt = _external_registration_receipt(
        frozen,
        authority_key=authority_key,
        registry_commit=registry_commit,
        registry_path=registry_path,
        registered_at=start - timedelta(seconds=1),
    )
    monkeypatch.setattr(
        candidate_universe,
        "REVOKED_COLLECTION_PREREGISTRATION_SHA256S",
        frozenset({frozen["preregistration_sha256"]}),
        raising=False,
    )

    with pytest.raises(ValueError, match="preregistration is revoked"):
        candidate_universe.validate_collection_preregistration(
            frozen,
            trusted_preregistration_public_keys={
                candidate_universe.public_key_text(signer_key)
            },
            expected_preregistration_sha256=frozen["preregistration_sha256"],
        )

    with pytest.raises(ValueError, match="preregistration is revoked"):
        candidate_universe.validate_preregistration_registration_receipt(
            receipt,
            preregistration=frozen,
            trusted_receipt_public_keys={
                candidate_universe.public_key_text(authority_key)
            },
            expected_receipt_sha256=receipt["receipt_sha256"],
        )


def test_preregistration_rejects_receipt_from_post_hoc_trusted_signer(
    tmp_path: Path,
):
    preregistration_key = Ed25519PrivateKey.generate()
    authority_key = Ed25519PrivateKey.generate()
    attacker_key = Ed25519PrivateKey.generate()
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(tmp_path)
    same_authority_draft = candidate_universe.prepare_collection_preregistration(
        batch_id="same-authority",
        collection_anchor_commit=anchor_commit,
        collection_anchor_repository_root=str(anchor_repo),
        observed_from=start.isoformat(),
        observed_through=end.isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
        pilot_session_ids=["pilot-01"],
        registration_authority_public_key=(
            candidate_universe.public_key_text(preregistration_key)
        ),
    )
    with pytest.raises(ValueError, match="authority must be independent"):
        candidate_universe.seal_collection_preregistration(
            same_authority_draft,
            preregistration_key,
            collection_anchor_repository_root=str(anchor_repo),
            expected_preregistration_sha256=(
                same_authority_draft["preregistration_sha256"]
            ),
        )
    draft = candidate_universe.prepare_collection_preregistration(
        batch_id="fresh-batch-b",
        collection_anchor_commit=anchor_commit,
        collection_anchor_repository_root=str(anchor_repo),
        observed_from=start.isoformat(),
        observed_through=end.isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
        pilot_session_ids=["pilot-01"],
        registration_authority_public_key=(
            candidate_universe.public_key_text(authority_key)
        ),
    )
    frozen = candidate_universe.seal_collection_preregistration(
        draft,
        preregistration_key,
        collection_anchor_repository_root=str(anchor_repo),
        expected_preregistration_sha256=draft["preregistration_sha256"],
    )
    forged = {
        "schema_version": 1,
        "status": "registered",
        "batch_id": frozen["batch_id"],
        "preregistration_sha256": frozen["preregistration_sha256"],
        "registry_commit": "b" * 40,
        "registry_path": "registry.json",
        "registered_at": (start - timedelta(seconds=1)).isoformat(),
        "signoff": {
            "signer": "independent-preregistration-timestamp-v1",
            "signer_public_key": "",
            "signature": "",
        },
        "receipt_sha256": "",
    }
    forged = candidate_universe._seal_document(
        forged,
        private_key=attacker_key,
        digest_field="receipt_sha256",
    )

    with pytest.raises(ValueError, match="authority mismatch"):
        candidate_universe.validate_preregistration_registration_receipt(
            forged,
            preregistration=frozen,
            trusted_receipt_public_keys={
                candidate_universe.public_key_text(attacker_key)
            },
            expected_receipt_sha256=forged["receipt_sha256"],
        )

    malformed = deepcopy(forged)
    malformed["signoff"] = []
    with pytest.raises(
        ValueError, match="registration receipt signoff is invalid"
    ):
        candidate_universe.validate_preregistration_registration_receipt(
            malformed,
            preregistration=frozen,
            trusted_receipt_public_keys={
                candidate_universe.public_key_text(attacker_key)
            },
            expected_receipt_sha256=forged["receipt_sha256"],
        )


def test_prospective_preregistration_rejects_wrong_registry_record(
    tmp_path: Path,
):
    signer_key = Ed25519PrivateKey.generate()
    authority_key = Ed25519PrivateKey.generate()
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(tmp_path)
    draft = candidate_universe.prepare_collection_preregistration(
        batch_id="fresh-batch-b",
        collection_anchor_commit=anchor_commit,
        collection_anchor_repository_root=str(anchor_repo),
        observed_from=start.isoformat(),
        observed_through=end.isoformat(),
        provider_ledger_cutoff=cutoff.isoformat(),
        pilot_session_ids=["20260811T160453-0862907a"],
        registration_authority_public_key=(
            candidate_universe.public_key_text(authority_key)
        ),
    )
    frozen = candidate_universe.seal_collection_preregistration(
        draft,
        signer_key,
        collection_anchor_repository_root=str(anchor_repo),
        expected_preregistration_sha256=draft["preregistration_sha256"],
    )
    registry_path = ".omc/benchmarks/plan-preregistrations/fresh-batch-b.json"
    registry_file = anchor_repo / registry_path
    registry_file.parent.mkdir(parents=True)
    wrong_record = candidate_universe.prepare_preregistration_registry_record(
        frozen
    )
    wrong_record["preregistration_sha256"] = candidate_universe.canonical_digest(
        "different-preregistration"
    )
    registry_file.write_text(json.dumps(wrong_record))
    _git(anchor_repo, "add", registry_path)
    _git(anchor_repo, "commit", "-qm", "register wrong preregistration")
    registry_commit = _git(anchor_repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="registry record is invalid"):
        candidate_universe.validate_preregistration_registry_anchor(
            frozen,
            repository_root=str(anchor_repo),
            registry_commit=registry_commit,
            registry_path=registry_path,
        )


def test_preregistration_cli_prepares_and_seals_approved_draft(tmp_path: Path):
    anchor_repo, anchor_commit, start, end, cutoff = _collection_anchor(tmp_path)
    draft_path = tmp_path / "preregistration-draft.json"
    frozen_path = tmp_path / "preregistration-frozen.json"
    registry_record_path = tmp_path / "preregistration-registry.json"
    private_key_path = tmp_path / "preregistration.key"
    signer_key = Ed25519PrivateKey.generate()
    authority_key = Ed25519PrivateKey.generate()
    private_key_path.write_text(base64.b64encode(signer_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )).decode("ascii"))

    assert candidate_universe.main([
        "prepare-preregistration",
        "--batch-id", "fresh-batch-b",
        "--collection-anchor-commit", anchor_commit,
        "--collection-anchor-repository-root", str(anchor_repo),
        "--observed-from", start.isoformat(),
        "--observed-through", end.isoformat(),
        "--provider-ledger-cutoff", cutoff.isoformat(),
        "--pilot-session-id", "20260811T160453-0862907a",
        "--registration-authority-public-key",
        candidate_universe.public_key_text(authority_key),
        "--output", str(draft_path),
    ]) == 0
    draft = json.loads(draft_path.read_text())

    assert candidate_universe.main([
        "seal-preregistration",
        str(draft_path),
        "--private-key", str(private_key_path),
        "--collection-anchor-repository-root", str(anchor_repo),
        "--approved-preregistration-sha256", draft["preregistration_sha256"],
        "--output", str(frozen_path),
    ]) == 0
    frozen = json.loads(frozen_path.read_text())
    candidate_universe.validate_collection_preregistration(
        frozen,
        trusted_preregistration_public_keys={
            candidate_universe.public_key_text(signer_key)
        },
        expected_preregistration_sha256=frozen["preregistration_sha256"],
    )
    assert candidate_universe.main([
        "prepare-preregistration-registry-record",
        str(frozen_path),
        "--output", str(registry_record_path),
    ]) == 0
    assert json.loads(registry_record_path.read_text()) == {
        "schema_version": 1,
        "batch_id": "fresh-batch-b",
        "preregistration_sha256": frozen["preregistration_sha256"],
    }
    with pytest.raises(SystemExit):
        candidate_universe._parser().parse_args([
            "issue-preregistration-registration-receipt"
        ])


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
