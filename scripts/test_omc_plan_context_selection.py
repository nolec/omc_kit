import base64
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
    "low", "low", "low", "medium", "medium",
    "medium", "medium", "high", "high", "high",
)
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PREREGISTRATION_V5_SKILL = FIXTURES / "omc_plan_skill_preregistration_v5.md"
PREREGISTRATION_V5_PROTOCOL = (
    FIXTURES / "omc_plan_runtime_protocol_preregistration_v5.json"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def _batch(
    tmp_path: Path,
    *,
    source_by_index: dict[int, str] | None = None,
    request_by_index: dict[int, str] | None = None,
    symlink_index: int | None = None,
    binary_files_by_index: dict[int, dict[str, bytes]] | None = None,
    text_files_by_index: dict[int, dict[str, str]] | None = None,
) -> tuple[dict[str, Path], dict, list[str]]:
    source_by_index = source_by_index or {}
    request_by_index = request_by_index or {}
    binary_files_by_index = binary_files_by_index or {}
    text_files_by_index = text_files_by_index or {}
    repo_roots: dict[str, Path] = {}
    cases = []
    for index in range(10):
        alias = f"repo-{index}"
        repo = tmp_path / alias
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "context@example.com")
        _git(repo, "config", "user.name", "Context Test")
        (repo / "src").mkdir()
        source = repo / "src" / f"service-{index}.py"
        source.write_text(
            source_by_index.get(index, "def run():\n    return None\n")
        )
        if index == symlink_index:
            (repo / "src" / "linked.py").symlink_to(source.name)
        for filename, content in binary_files_by_index.get(index, {}).items():
            (repo / "src" / filename).write_bytes(content)
        for filename, content in text_files_by_index.get(index, {}).items():
            destination = repo / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        if index == 0:
            (repo / "vendor").mkdir()
            (repo / "vendor" / "private.txt").write_text("excluded\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "baseline")
        baseline = _git(repo, "rev-parse", "HEAD")
        source.write_text(f"def run():\n    return {index}\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "followup")
        followup = _git(repo, "rev-parse", "HEAD")
        repo_roots[alias] = repo
        cases.append({
            "case_id": f"batch-a-plan-{index + 1:02d}",
            "repo_alias": alias,
            "baseline_commit": baseline,
            "followup_commit": followup,
            "request": request_by_index.get(
                index, f"서비스 {index} 실행 계획을 작성한다"
            ),
            "context_candidate_paths": [f"src/service-{index}.py"],
            "surface": SURFACES[index],
            "ambiguity": AMBIGUITIES[index],
            "selected_object": index < 2,
        })

    prior_commits = ["a" * 40]
    selection = {
        "schema_version": 1,
        "status": "preregistered",
        "batch_id": "batch-a",
        "selection_policy": {
            "provider_outputs_available_during_selection": False,
            "prior_registry_sha256": context_selection.canonical_digest(prior_commits),
            "required_surface_counts": {
                "ui_state": 2,
                "api_payload": 2,
                "data_indexing": 2,
                "backend_rules": 2,
                "multi_file_legacy": 2,
            },
            "required_ambiguity_counts": {"low": 3, "medium": 4, "high": 3},
            "maximum_selected_object_cases": 2,
        },
        "cases": cases,
        "selection_sha256": context_selection.canonical_digest(cases),
    }
    return repo_roots, selection, prior_commits


def _public_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )).decode("ascii")


def _anchor_entry(batch_id: str, *, marker: str) -> dict:
    return {
        "batch_id": batch_id,
        "status": "active",
        "selection_sha256": marker * 64,
        "selection_commit": marker * 40,
        "preregistration_manifest_sha256": marker.upper() * 64,
        "source_commit": marker.upper() * 40,
        "retrieval_policy_sha256": ("f" if marker != "f" else "e") * 64,
        "selection_signer_public_key": base64.b64encode(
            bytes([int(marker, 16)]) * 32
        ).decode("ascii"),
        "preregistration_signer_public_key": base64.b64encode(
            bytes([int(marker, 16) + 1]) * 32
        ).decode("ascii"),
    }


def _anchor_registry(
    private_key: Ed25519PrivateKey,
    batches: list[dict],
    *,
    generation: int = 1,
    previous_registry_sha256: str | None = None,
) -> dict:
    registry = {
        "schema_version": 1,
        "status": "active",
        "generation": generation,
        "previous_registry_sha256": previous_registry_sha256,
        "batches": deepcopy(batches),
        "signoff": {
            "signer": "confirmatory-anchor-root-v1",
            "signer_public_key": _public_key(private_key),
            "signature": "",
        },
    }
    registry["registry_sha256"] = context_selection._anchor_registry_digest(registry)
    registry["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.anchor_registry_payload(registry))
    ).decode("ascii")
    return registry


def _selection_provenance(
    selection: dict,
    private_key: Ed25519PrivateKey,
    *,
    author_session_id: str = "selection-author-session",
) -> dict:
    provenance = {
        "schema_version": 1,
        "status": "attested",
        "selection_sha256": selection["selection_sha256"],
        "selection_commit": context_selection.FROZEN_SELECTION_COMMIT,
        "author_session_id": author_session_id,
        "signoff": {
            "signer": "selection-author",
            "signer_public_key": _public_key(private_key),
            "signature": "",
        },
    }
    provenance["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.selection_provenance_payload(provenance))
    ).decode("ascii")
    return provenance


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **batch_options,
):
    repo_roots, selection, prior_commits = _batch(tmp_path, **batch_options)
    monkeypatch.setattr(
        context_selection,
        "FROZEN_SELECTION_SHA256",
        selection["selection_sha256"],
    )
    author_key = Ed25519PrivateKey.generate()
    provenance = _selection_provenance(selection, author_key)
    packet = context_selection.prepare_context_selection_packet(
        selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        selection_provenance=provenance,
        trusted_selection_public_keys={_public_key(author_key)},
    )
    return repo_roots, selection, prior_commits, packet


def _trusted_selection_keys(packet: dict) -> set[str]:
    return {packet["selection_provenance"]["signoff"]["signer_public_key"]}


def _readiness_preregistration(
    selection: dict, monkeypatch: pytest.MonkeyPatch
) -> dict:
    private_key = Ed25519PrivateKey.generate()
    public_key = _public_key(private_key)
    protocol = json.loads(
        (FIXTURES / "omc_plan_runtime_protocol.json").read_text()
    )
    skill_path = Path(".agents/skills/omc-plan/SKILL.md")
    manifest = {
        "schema_version": 1,
        "status": "preregistered",
        "source_commit": context_selection.FROZEN_RANKING_V4_SOURCE_COMMIT,
        "retrieval_policy_sha256": context_selection.canonical_digest(
            context_selection.RETRIEVAL_POLICY
        ),
        "selection_sha256": selection["selection_sha256"],
        "skill_sha256": hashlib.sha256(skill_path.read_bytes()).hexdigest(),
        "protocol_sha256": context_selection.canonical_digest(protocol),
        "signoff": {
            "signer": "local-preregistration-v1",
            "signer_public_key": public_key,
            "signature": "",
        },
    }
    manifest["manifest_sha256"] = (
        context_selection._preregistration_manifest_digest(manifest)
    )
    manifest["signoff"]["signature"] = base64.b64encode(
        private_key.sign(
            context_selection.preregistration_manifest_payload(manifest)
        )
    ).decode("ascii")
    monkeypatch.setattr(
        context_selection,
        "FROZEN_PREREGISTRATION_V5_SHA256",
        manifest["manifest_sha256"],
    )
    monkeypatch.setattr(
        context_selection,
        "FROZEN_PREREGISTRATION_V5_PUBLIC_KEY",
        public_key,
    )
    return {
        "preregistration_manifest": manifest,
        "skill_path": skill_path,
        "protocol": protocol,
        "trusted_preregistration_public_keys": {public_key},
    }


def _signed_response(packet: dict, private_key: Ed25519PrivateKey) -> dict:
    response = {
        "schema_version": 1,
        "status": "selected",
        "packet_sha256": packet["packet_sha256"],
        "selector": {
            "session_id": "independent-selector-session",
            "provider_outputs_available": False,
        },
        "cases": [
            {
                "case_id": case["case_id"],
                "selected_paths": [f"src/service-{index}.py"],
            }
            for index, case in enumerate(packet["cases"])
        ],
        "signoff": {
            "signer": "independent-selector",
            "signer_public_key": _public_key(private_key),
            "signature": "",
        },
    }
    response["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.selector_response_payload(response))
    ).decode("ascii")
    return response


def test_packet_rejects_a_valid_but_non_frozen_confirmatory_selection(tmp_path):
    repo_roots, selection, prior_commits = _batch(tmp_path)
    author_key = Ed25519PrivateKey.generate()
    provenance = _selection_provenance(selection, author_key)

    with pytest.raises(ValueError, match="frozen selection digest"):
        context_selection.prepare_context_selection_packet(
            selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            selection_provenance=provenance,
            trusted_selection_public_keys={_public_key(author_key)},
        )


def test_active_frozen_selection_anchor_matches_fresh_batch_a_v2():
    selection = json.loads(
        (FIXTURES / "omc_plan_confirmatory_batch_a_v2_selection.json").read_text()
    )

    assert selection["selection_sha256"] == (
        context_selection.FROZEN_SELECTION_SHA256
    )
    assert context_selection.FROZEN_SELECTION_COMMIT == (
        "337f00da0a89ca9dd74c84ca092838bbd2fb820b"
    )


def test_anchor_registry_accepts_trusted_append_only_chain():
    root_key = Ed25519PrivateKey.generate()
    first = _anchor_registry(root_key, [_anchor_entry("batch-a", marker="1")])
    second = _anchor_registry(
        root_key,
        [
            _anchor_entry("batch-a", marker="1"),
            _anchor_entry("batch-b", marker="2"),
        ],
        generation=2,
        previous_registry_sha256=first["registry_sha256"],
    )

    result = context_selection.validate_confirmatory_anchor_registry(
        second,
        trusted_root_public_keys={_public_key(root_key)},
        expected_registry_sha256=second["registry_sha256"],
        previous_registry=first,
    )

    assert result == {
        "batch-a": _anchor_entry("batch-a", marker="1"),
        "batch-b": _anchor_entry("batch-b", marker="2"),
    }


def test_anchor_registry_accepts_next_generation_from_signed_checkpoint():
    root_key = Ed25519PrivateKey.generate()
    first = _anchor_registry(root_key, [_anchor_entry("batch-a", marker="1")])
    second = _anchor_registry(
        root_key,
        [
            _anchor_entry("batch-a", marker="1"),
            _anchor_entry("batch-b", marker="2"),
        ],
        generation=2,
        previous_registry_sha256=first["registry_sha256"],
    )
    third = _anchor_registry(
        root_key,
        [
            _anchor_entry("batch-a", marker="1"),
            _anchor_entry("batch-b", marker="2"),
            _anchor_entry("batch-c", marker="3"),
        ],
        generation=3,
        previous_registry_sha256=second["registry_sha256"],
    )

    result = context_selection.validate_confirmatory_anchor_registry(
        third,
        trusted_root_public_keys={_public_key(root_key)},
        expected_registry_sha256=third["registry_sha256"],
        previous_registry=second,
    )

    assert list(result) == ["batch-a", "batch-b", "batch-c"]


def test_anchor_registry_rejects_stale_signed_genesis():
    root_key = Ed25519PrivateKey.generate()
    first = _anchor_registry(root_key, [_anchor_entry("batch-a", marker="1")])
    second = _anchor_registry(
        root_key,
        [
            _anchor_entry("batch-a", marker="1"),
            _anchor_entry("batch-b", marker="2"),
        ],
        generation=2,
        previous_registry_sha256=first["registry_sha256"],
    )

    with pytest.raises(ValueError, match="expected registry hash mismatch"):
        context_selection.validate_confirmatory_anchor_registry(
            first,
            trusted_root_public_keys={_public_key(root_key)},
            expected_registry_sha256=second["registry_sha256"],
        )


def test_anchor_registry_rejects_untrusted_self_signature():
    root_key = Ed25519PrivateKey.generate()
    registry = _anchor_registry(root_key, [_anchor_entry("batch-a", marker="1")])

    with pytest.raises(ValueError, match="anchor root signer is not trusted"):
        context_selection.validate_confirmatory_anchor_registry(
            registry,
            trusted_root_public_keys=set(),
            expected_registry_sha256=registry["registry_sha256"],
        )


def test_anchor_registry_rejects_trusted_signature_tampering():
    root_key = Ed25519PrivateKey.generate()
    registry = _anchor_registry(root_key, [_anchor_entry("batch-a", marker="1")])
    registry["batches"][0]["source_commit"] = "f" * 40
    registry["registry_sha256"] = context_selection._anchor_registry_digest(registry)

    with pytest.raises(ValueError, match="anchor registry signature is invalid"):
        context_selection.validate_confirmatory_anchor_registry(
            registry,
            trusted_root_public_keys={_public_key(root_key)},
            expected_registry_sha256=registry["registry_sha256"],
        )


def test_anchor_registry_rejects_reused_selection_hash():
    root_key = Ed25519PrivateKey.generate()
    first_entry = _anchor_entry("batch-a", marker="1")
    second_entry = _anchor_entry("batch-b", marker="2")
    second_entry["selection_sha256"] = first_entry["selection_sha256"]
    registry = _anchor_registry(root_key, [first_entry, second_entry])

    with pytest.raises(ValueError, match="selection hashes must be unique"):
        context_selection.validate_confirmatory_anchor_registry(
            registry,
            trusted_root_public_keys={_public_key(root_key)},
            expected_registry_sha256=registry["registry_sha256"],
        )


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("previous_hash", "previous registry hash mismatch"),
        ("generation", "anchor registry generation is invalid"),
        ("existing_batch", "anchor registry must be append-only"),
        ("duplicate_batch", "anchor registry batch ids must be unique"),
    ],
)
def test_anchor_registry_rejects_rollback_replay_and_mutation(mutation, error):
    root_key = Ed25519PrivateKey.generate()
    first_entry = _anchor_entry("batch-a", marker="1")
    first = _anchor_registry(root_key, [first_entry])
    second_entries = [first_entry, _anchor_entry("batch-b", marker="2")]
    generation = 2
    previous_hash = first["registry_sha256"]
    if mutation == "previous_hash":
        previous_hash = "0" * 64
    elif mutation == "generation":
        generation = 1
    elif mutation == "existing_batch":
        second_entries[0] = _anchor_entry("batch-a", marker="3")
    elif mutation == "duplicate_batch":
        second_entries[1] = _anchor_entry("batch-a", marker="2")
    second = _anchor_registry(
        root_key,
        second_entries,
        generation=generation,
        previous_registry_sha256=previous_hash,
    )

    with pytest.raises(ValueError, match=error):
        context_selection.validate_confirmatory_anchor_registry(
            second,
            trusted_root_public_keys={_public_key(root_key)},
            expected_registry_sha256=second["registry_sha256"],
            previous_registry=first,
        )


def test_confirmatory_preregistration_binds_all_claim_inputs():
    manifest = json.loads(
        (FIXTURES / "omc_plan_confirmatory_preregistration_v5.json").read_text()
    )
    selection = json.loads(
        (FIXTURES / "omc_plan_confirmatory_batch_a_v2_selection.json").read_text()
    )
    protocol = json.loads(PREREGISTRATION_V5_PROTOCOL.read_text())

    result = context_selection.validate_confirmatory_preregistration_manifest(
        manifest,
        selection=selection,
        skill_path=PREREGISTRATION_V5_SKILL,
        protocol=protocol,
        trusted_preregistration_public_keys={
            manifest["signoff"]["signer_public_key"]
        },
    )

    assert result == {
        "source_commit": "4e1ac03d4bed1fa945989b549af6cd61b9c51a34",
        "retrieval_policy_sha256": context_selection.canonical_digest(
            context_selection.RETRIEVAL_POLICY
        ),
        "selection_sha256": selection["selection_sha256"],
        "skill_sha256": manifest["skill_sha256"],
        "protocol_sha256": manifest["protocol_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
    }


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("source_commit", "0" * 40),
        ("retrieval_policy_sha256", "1" * 64),
        ("selection_sha256", "2" * 64),
        ("skill_sha256", "3" * 64),
        ("protocol_sha256", "4" * 64),
    ],
)
def test_confirmatory_preregistration_rejects_changed_claim_inputs(
    field, replacement
):
    manifest = json.loads(
        (FIXTURES / "omc_plan_confirmatory_preregistration_v5.json").read_text()
    )
    manifest[field] = replacement

    with pytest.raises(ValueError, match="preregistration"):
        context_selection.validate_confirmatory_preregistration_manifest(
            manifest,
            selection=json.loads(
                (FIXTURES / "omc_plan_confirmatory_batch_a_v2_selection.json").read_text()
            ),
            skill_path=PREREGISTRATION_V5_SKILL,
            protocol=json.loads(
                PREREGISTRATION_V5_PROTOCOL.read_text()
            ),
            trusted_preregistration_public_keys={
                manifest["signoff"]["signer_public_key"]
            },
        )


def test_confirmatory_preregistration_rejects_untrusted_self_signature():
    manifest = json.loads(
        (FIXTURES / "omc_plan_confirmatory_preregistration_v5.json").read_text()
    )

    with pytest.raises(ValueError, match="signer is not trusted"):
        context_selection.validate_confirmatory_preregistration_manifest(
            manifest,
            selection=json.loads(
                (FIXTURES / "omc_plan_confirmatory_batch_a_v2_selection.json").read_text()
            ),
            skill_path=PREREGISTRATION_V5_SKILL,
            protocol=json.loads(
                PREREGISTRATION_V5_PROTOCOL.read_text()
            ),
            trusted_preregistration_public_keys=set(),
        )


def test_confirmatory_preregistration_rejects_tampered_selection_content():
    manifest = json.loads(
        (FIXTURES / "omc_plan_confirmatory_preregistration_v5.json").read_text()
    )
    selection = json.loads(
        (FIXTURES / "omc_plan_confirmatory_batch_a_v2_selection.json").read_text()
    )
    selection["cases"][0]["request"] = "tampered after preregistration"

    with pytest.raises(ValueError, match="selection content hash mismatch"):
        context_selection.validate_confirmatory_preregistration_manifest(
            manifest,
            selection=selection,
            skill_path=PREREGISTRATION_V5_SKILL,
            protocol=json.loads(
                PREREGISTRATION_V5_PROTOCOL.read_text()
            ),
            trusted_preregistration_public_keys={
                manifest["signoff"]["signer_public_key"]
            },
        )


def test_packet_requires_the_full_confirmatory_selection_contract(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits = _batch(tmp_path)
    selection["cases"] = selection["cases"][:1]
    selection["selection_sha256"] = context_selection.canonical_digest(selection["cases"])
    monkeypatch.setattr(
        context_selection,
        "FROZEN_SELECTION_SHA256",
        selection["selection_sha256"],
    )
    author_key = Ed25519PrivateKey.generate()
    provenance = _selection_provenance(selection, author_key)

    with pytest.raises(ValueError, match="exactly 10 cases"):
        context_selection.prepare_context_selection_packet(
            selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            selection_provenance=provenance,
            trusted_selection_public_keys={_public_key(author_key)},
        )


def test_packet_is_baseline_only_and_does_not_leak_private_or_followup_data(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)

    serialized = json.dumps(packet, ensure_ascii=False)
    assert len(serialized.encode("utf-8")) < 10_000
    assert all(str(repo) not in serialized for repo in repo_roots.values())
    assert all(case["followup_commit"] not in serialized for case in selection["cases"])
    assert "followup_commit" not in serialized
    assert "context_candidate_paths" not in serialized
    assert "baseline_tree" not in packet["cases"][0]
    assert packet["selection_sha256"] == selection["selection_sha256"]

    output_root = tmp_path / "workspaces"
    workspaces = context_selection.materialize_baseline_workspaces(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        output_root=output_root,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
    )
    workspace = workspaces["batch-a-plan-01"]
    assert (workspace / "src/service-0.py").is_file()
    assert not (workspace / "vendor/private.txt").exists()


def test_materialization_is_atomic_when_a_later_case_fails(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    packet["cases"][1]["baseline_tree_sha256"] = "0" * 64
    packet["packet_sha256"] = context_selection.canonical_digest({
        key: value for key, value in packet.items() if key != "packet_sha256"
    })
    output_root = tmp_path / "workspaces"

    with pytest.raises(ValueError, match="packet projection mismatch"):
        context_selection.materialize_baseline_workspaces(
            packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            output_root=output_root,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )

    assert not output_root.exists()


def test_rehashed_packet_case_id_cannot_escape_the_output_root(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    escaped = tmp_path / "escaped-case"
    packet["cases"][0]["case_id"] = str(escaped)
    packet["packet_sha256"] = context_selection.canonical_digest({
        key: value for key, value in packet.items() if key != "packet_sha256"
    })
    output_root = tmp_path / "workspaces"

    with pytest.raises(ValueError, match="packet projection mismatch|case id"):
        context_selection.materialize_baseline_workspaces(
            packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            output_root=output_root,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )

    assert not escaped.exists()
    assert not output_root.exists()


def test_signed_selector_response_builds_an_immutable_baseline_manifest(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    response = _signed_response(packet, private_key)
    trusted_key = response["signoff"]["signer_public_key"]

    manifest = context_selection.apply_selector_response(
        packet,
        response,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selector_public_keys={trusted_key},
        trusted_selection_public_keys=_trusted_selection_keys(packet),
    )

    assert manifest["status"] == "preregistered"
    assert manifest["selection_sha256"] == packet["selection_sha256"]
    assert manifest["cases"][0]["selected_context"][0]["path"] == "src/service-0.py"
    context_selection.validate_baseline_context_manifest(
        manifest,
        packet=packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selector_public_keys={trusted_key},
        trusted_selection_public_keys=_trusted_selection_keys(packet),
    )

    tampered = deepcopy(manifest)
    tampered["cases"][0]["selected_context"][0]["path"] = "src/missing.py"
    with pytest.raises(ValueError, match="manifest hash"):
        context_selection.validate_baseline_context_manifest(
            tampered,
            packet=packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selector_public_keys={trusted_key},
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )


def test_selector_response_requires_packet_case_order(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    response = _signed_response(packet, private_key)
    response["cases"].reverse()
    response["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.selector_response_payload(response))
    ).decode("ascii")

    with pytest.raises(ValueError, match="case order"):
        context_selection.apply_selector_response(
            packet,
            response,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selector_public_keys={_public_key(private_key)},
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )


def test_signed_author_provenance_prevents_session_id_spoofing(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    response = _signed_response(packet, private_key)
    response["selector"]["session_id"] = "selection-author-session"
    response["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.selector_response_payload(response))
    ).decode("ascii")

    with pytest.raises(ValueError, match="independent session"):
        context_selection.apply_selector_response(
            packet,
            response,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selector_public_keys={_public_key(private_key)},
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )


def test_packet_rehash_cannot_replace_the_signed_author_session(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    trusted_selection_keys = _trusted_selection_keys(packet)
    tampered = deepcopy(packet)
    tampered["selection_author_session_id"] = "forged-author-session"
    tampered["packet_sha256"] = context_selection.canonical_digest({
        key: value for key, value in tampered.items() if key != "packet_sha256"
    })
    selector_key = Ed25519PrivateKey.generate()
    response = _signed_response(tampered, selector_key)

    with pytest.raises(ValueError, match="provenance contract"):
        context_selection.apply_selector_response(
            tampered,
            response,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selector_public_keys={_public_key(selector_key)},
            trusted_selection_public_keys=trusted_selection_keys,
        )


def test_selector_response_rejects_non_baseline_duplicate_and_relaxed_policy(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    response = _signed_response(packet, private_key)
    trusted_key = response["signoff"]["signer_public_key"]

    invalid_path = deepcopy(response)
    invalid_path["cases"][0]["selected_paths"] = ["src/new.py"]
    invalid_path["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.selector_response_payload(invalid_path))
    ).decode("ascii")
    with pytest.raises(ValueError, match="baseline tree"):
        context_selection.apply_selector_response(
            packet,
            invalid_path,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selector_public_keys={trusted_key},
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )

    duplicate_case = deepcopy(response)
    duplicate_case["cases"].append(deepcopy(duplicate_case["cases"][0]))
    duplicate_case["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.selector_response_payload(duplicate_case))
    ).decode("ascii")
    with pytest.raises(ValueError, match="cases do not match"):
        context_selection.apply_selector_response(
            packet,
            duplicate_case,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selector_public_keys={trusted_key},
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )

    relaxed_policy = deepcopy(packet)
    relaxed_policy["retrieval_policy"]["maximum_selected_files_per_case"] = 100
    relaxed_policy["retrieval_policy_sha256"] = context_selection.canonical_digest(
        relaxed_policy["retrieval_policy"]
    )
    relaxed_policy["packet_sha256"] = context_selection.canonical_digest({
        key: value for key, value in relaxed_policy.items() if key != "packet_sha256"
    })
    relaxed_response = deepcopy(response)
    relaxed_response["packet_sha256"] = relaxed_policy["packet_sha256"]
    relaxed_response["signoff"]["signature"] = base64.b64encode(
        private_key.sign(context_selection.selector_response_payload(relaxed_response))
    ).decode("ascii")
    with pytest.raises(ValueError, match="frozen policy"):
        context_selection.apply_selector_response(
            relaxed_policy,
            relaxed_response,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selector_public_keys={trusted_key},
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )


def test_local_transfer_readiness_freezes_exact_payload_without_private_mapping(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    preregistration = _readiness_preregistration(selection, monkeypatch)

    readiness = context_selection.prepare_local_transfer_readiness(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
        execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
        **preregistration,
    )

    assert readiness["status"] == "approval_required"
    assert readiness["execution_contract"]["attestation_type"] == "operator_attested"
    assert readiness["execution_contract"]["replacement_claim_eligible"] is False
    assert readiness["provider_execution_allowed"] is False
    assert readiness["preregistration_manifest_sha256"] == (
        preregistration["preregistration_manifest"]["manifest_sha256"]
    )
    assert len(readiness["transfer_manifest"]["cases"]) == 10
    serialized = json.dumps(readiness, ensure_ascii=False)
    assert all(str(repo) not in serialized for repo in repo_roots.values())
    assert all(case["followup_commit"] not in serialized for case in selection["cases"])
    assert "repo_alias" not in serialized
    assert "private_mapping" not in serialized
    context_selection.validate_local_transfer_readiness(
        readiness,
        packet=packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
        **preregistration,
    )


def test_local_transfer_readiness_rejects_runtime_protocol_drift(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    preregistration = _readiness_preregistration(selection, monkeypatch)
    preregistration["protocol"] = deepcopy(preregistration["protocol"])
    preregistration["protocol"]["reasoning_effort"] = "high"

    with pytest.raises(ValueError, match="claim input mismatch"):
        context_selection.prepare_local_transfer_readiness(
            packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
            execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
            **preregistration,
        )


def test_local_transfer_readiness_rejects_skill_drift(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    preregistration = _readiness_preregistration(selection, monkeypatch)
    changed_skill = tmp_path / "changed-SKILL.md"
    changed_skill.write_text("# Changed OMC Plan\n", encoding="utf-8")
    preregistration["skill_path"] = changed_skill

    with pytest.raises(ValueError, match="claim input mismatch"):
        context_selection.prepare_local_transfer_readiness(
            packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
            execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
            **preregistration,
        )


def test_local_transfer_readiness_uses_signed_baseline_context_manifest(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits, packet = _prepare(
        tmp_path,
        monkeypatch,
        text_files_by_index={0: {"src/unrelated-large.txt": "x" * 260_000}},
    )
    preregistration = _readiness_preregistration(selection, monkeypatch)
    selector_key = Ed25519PrivateKey.generate()
    response = _signed_response(packet, selector_key)
    trusted_selector_keys = {_public_key(selector_key)}
    baseline_context_manifest = context_selection.apply_selector_response(
        packet,
        response,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selector_public_keys=trusted_selector_keys,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
    )

    readiness = context_selection.prepare_local_transfer_readiness(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
        execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
        baseline_context_manifest=baseline_context_manifest,
        trusted_selector_public_keys=trusted_selector_keys,
        **preregistration,
    )

    assert readiness["baseline_context_manifest_sha256"] == (
        baseline_context_manifest["manifest_sha256"]
    )
    assert readiness["transfer_manifest"]["baseline_context_manifest_sha256"] == (
        baseline_context_manifest["manifest_sha256"]
    )
    first_case_paths = {
        item["relative_path"]
        for item in readiness["transfer_bundle"]["cases"][0]["files"]
    }
    assert first_case_paths == {"src/service-0.py"}
    assert "src/unrelated-large.txt" not in json.dumps(readiness)
    context_selection.validate_local_transfer_readiness(
        readiness,
        packet=packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
        baseline_context_manifest=baseline_context_manifest,
        trusted_selector_public_keys=trusted_selector_keys,
        **preregistration,
    )


@pytest.mark.parametrize(
    "source, expected_code",
    [
        ('API_KEY = "super-secret-value"\n', "credential_assignment"),
        ('SOURCE = "/Users/private/project/file.py"\n', "local_user_path"),
    ],
)
def test_local_transfer_readiness_omits_sensitive_source(
    tmp_path, monkeypatch, source, expected_code
):
    repo_roots, selection, prior_commits, packet = _prepare(
        tmp_path,
        monkeypatch,
        source_by_index={0: source},
    )
    preregistration = _readiness_preregistration(selection, monkeypatch)

    readiness = context_selection.prepare_local_transfer_readiness(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
        execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
        **preregistration,
    )

    manifest = readiness["transfer_manifest"]
    source_entry = next(
        item
        for item in manifest["cases"][0]["files"]
        if item["relative_path"] == "src/service-0.py"
    )
    payload_paths = {
        item["relative_path"]
        for item in readiness["transfer_bundle"]["cases"][0]["files"]
    }
    assert source_entry["transfer_disposition"] == "omitted_sensitive"
    assert source_entry["privacy_classification"] == "omitted_sensitive"
    assert source_entry["sensitive_codes"] == [expected_code]
    assert source_entry["blob_sha256"]
    assert manifest["omitted_sensitive_count"] == 1
    assert manifest["omitted_binary_count"] == 0
    assert manifest["omitted_file_count"] == 1
    assert "src/service-0.py" not in payload_paths
    assert source not in json.dumps(readiness, ensure_ascii=False)
    assert readiness["privacy_audit"]["status"] == "sanitized"


def test_local_transfer_readiness_blocks_sensitive_request(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(
        tmp_path,
        monkeypatch,
        request_by_index={0: 'API_KEY = "super-secret-value"'},
    )
    preregistration = _readiness_preregistration(selection, monkeypatch)

    with pytest.raises(ValueError, match="request contains sensitive content"):
        context_selection.prepare_local_transfer_readiness(
            packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
            execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
            **preregistration,
        )


def test_local_transfer_readiness_rejects_symlinks(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(
        tmp_path,
        monkeypatch,
        symlink_index=0,
    )
    preregistration = _readiness_preregistration(selection, monkeypatch)

    with pytest.raises(ValueError, match="regular files"):
        context_selection.prepare_local_transfer_readiness(
            packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
            execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
            **preregistration,
        )


def test_local_transfer_readiness_records_and_omits_binary_assets(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits, packet = _prepare(
        tmp_path,
        monkeypatch,
        binary_files_by_index={
            0: {
                "asset.png": b"\x89PNG\r\n\x1a\n\xff",
                "favicon.ico": b"\x00\x00\x01\x00\x01\x00",
                "control.bin": b"\x00\x01\x02\x03",
            }
        },
    )
    preregistration = _readiness_preregistration(selection, monkeypatch)

    readiness = context_selection.prepare_local_transfer_readiness(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
        execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
        **preregistration,
    )

    manifest_case = readiness["transfer_manifest"]["cases"][0]
    binaries = {
        item["relative_path"]: item
        for item in manifest_case["files"]
        if item["transfer_disposition"] == "omitted_binary"
    }
    payload_paths = {
        item["relative_path"]
        for item in readiness["transfer_bundle"]["cases"][0]["files"]
    }
    assert set(binaries) == {
        "src/asset.png",
        "src/control.bin",
        "src/favicon.ico",
    }
    assert all(
        item["privacy_classification"] == "not_scanned_binary"
        and item["blob_sha256"]
        for item in binaries.values()
    )
    assert binaries["src/asset.png"]["binary_reason"] == "invalid_utf8"
    assert binaries["src/favicon.ico"]["binary_reason"] == "control_character"
    assert binaries["src/control.bin"]["binary_reason"] == "control_character"
    assert readiness["transfer_manifest"]["omitted_file_count"] == 3
    assert not set(binaries).intersection(payload_paths)


def test_local_transfer_readiness_preserves_unicode_paths(tmp_path, monkeypatch):
    repo_roots, selection, prior_commits, packet = _prepare(
        tmp_path,
        monkeypatch,
        text_files_by_index={0: {"docs/보고서.md": "검증 결과\n"}},
    )
    preregistration = _readiness_preregistration(selection, monkeypatch)

    readiness = context_selection.prepare_local_transfer_readiness(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
        execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
        **preregistration,
    )

    paths = {
        item["relative_path"]
        for item in readiness["transfer_manifest"]["cases"][0]["files"]
    }
    assert "docs/보고서.md" in paths


def test_local_contract_cannot_self_assert_provider_verified_attestation():
    with pytest.raises(ValueError, match="native provider receipt"):
        context_selection.build_execution_contract(
            context_selection.DEFAULT_EXECUTION_BUDGET,
            attestation_type="provider_verified",
        )

    invalid_budget = dict(context_selection.DEFAULT_EXECUTION_BUDGET)
    invalid_budget["max_provider_calls"] = 0
    with pytest.raises(ValueError, match="execution budget"):
        context_selection.build_execution_contract(invalid_budget)


def test_transfer_budget_blocks_case_above_input_limit():
    contract = context_selection.build_execution_contract(
        context_selection.DEFAULT_EXECUTION_BUDGET
    )
    manifest = {
        "case_count": 1,
        "cases": [{"case_id": "oversized", "estimated_input_tokens": 250_001}],
    }

    with pytest.raises(ValueError, match="input token budget"):
        context_selection.validate_transfer_budget(manifest, contract)


def test_transfer_budget_uses_serialized_payload_and_prompt_reserve():
    case_payload = {
        "case_id": "escaped-payload",
        "request": "계획을 작성한다",
        "files": [{
            "relative_path": "src/generated.txt",
            "content_utf8": "\n" * 130_000,
        }],
    }
    raw_source_estimate = len(case_payload["files"][0]["content_utf8"]) // 3
    actual_estimate = context_selection.estimate_case_input_tokens(case_payload)
    contract = context_selection.build_execution_contract(
        context_selection.DEFAULT_EXECUTION_BUDGET
    )
    manifest = {
        "case_count": 1,
        "cases": [{
            "case_id": case_payload["case_id"],
            "estimated_input_tokens": actual_estimate,
        }],
    }

    assert raw_source_estimate < contract["max_input_tokens"]
    assert actual_estimate > contract["max_input_tokens"]
    with pytest.raises(ValueError, match="input token budget"):
        context_selection.validate_transfer_budget(manifest, contract)


def test_sensitive_readiness_output_must_be_outside_all_repositories(tmp_path):
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()

    with pytest.raises(ValueError, match="outside repositories"):
        context_selection.validate_sensitive_output_path(
            source_repo / "readiness.json",
            repo_roots={"source": source_repo},
        )
    with pytest.raises(ValueError, match="outside repositories"):
        context_selection.validate_sensitive_output_path(
            Path(context_selection.__file__).resolve().parent.parent
            / "readiness.json",
            repo_roots={"source": source_repo},
        )

    outside = tmp_path / "artifacts" / "readiness.json"
    assert context_selection.validate_sensitive_output_path(
        outside,
        repo_roots={"source": source_repo},
    ) == outside.resolve()


def test_local_transfer_readiness_detects_manifest_and_budget_tampering(
    tmp_path, monkeypatch
):
    repo_roots, selection, prior_commits, packet = _prepare(tmp_path, monkeypatch)
    preregistration = _readiness_preregistration(selection, monkeypatch)
    readiness = context_selection.prepare_local_transfer_readiness(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
        execution_budget=context_selection.DEFAULT_EXECUTION_BUDGET,
        **preregistration,
    )

    tampered_manifest = deepcopy(readiness)
    tampered_manifest["transfer_manifest"]["cases"][0]["files"][0][
        "blob_sha256"
    ] = "0" * 64
    with pytest.raises(ValueError, match="readiness hash|contract mismatch"):
        context_selection.validate_local_transfer_readiness(
            tampered_manifest,
            packet=packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
            **preregistration,
        )

    tampered_budget = deepcopy(readiness)
    tampered_budget["execution_contract"]["max_provider_calls"] = 2
    tampered_budget["readiness_sha256"] = context_selection.canonical_digest({
        key: value
        for key, value in tampered_budget.items()
        if key != "readiness_sha256"
    })
    with pytest.raises(ValueError, match="contract mismatch"):
        context_selection.validate_local_transfer_readiness(
            tampered_budget,
            packet=packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=prior_commits,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
            **preregistration,
        )


def _load_retrieval_development_inputs():
    corpus = json.loads(
        (FIXTURES / "omc_plan_retrieval_development.json").read_text()
    )
    batch_a = json.loads(
        (FIXTURES / "omc_plan_confirmatory_batch_a_selection.json").read_text()
    )
    return corpus, batch_a


def _load_retrieval_development_v2_inputs():
    corpus = json.loads(
        (FIXTURES / "omc_plan_retrieval_development_v2.json").read_text()
    )
    batch_a = json.loads(
        (FIXTURES / "omc_plan_confirmatory_batch_a_selection.json").read_text()
    )
    return corpus, batch_a


def _load_retrieval_development_v3_inputs():
    corpus = json.loads(
        (FIXTURES / "omc_plan_retrieval_development_v3.json").read_text()
    )
    batch_a = json.loads(
        (FIXTURES / "omc_plan_confirmatory_batch_a_selection.json").read_text()
    )
    return corpus, batch_a


def _refresh_corpus_digest(corpus):
    corpus["corpus_sha256"] = context_selection.canonical_digest({
        key: value for key, value in corpus.items() if key != "corpus_sha256"
    })


def test_retrieval_development_corpus_is_frozen_and_disjoint_from_batch_a():
    corpus, batch_a = _load_retrieval_development_inputs()

    summary = context_selection.validate_retrieval_development_corpus(
        corpus,
        confirmatory_selection=batch_a,
    )

    assert summary == {
        "case_count": 5,
        "critical_path_count": 5,
        "surface_counts": {
            "ui_state": 1,
            "api_payload": 1,
            "data_indexing": 1,
            "backend_rules": 1,
            "multi_file_legacy": 1,
        },
    }


def test_retrieval_development_corpus_rejects_batch_a_case_overlap():
    corpus, batch_a = _load_retrieval_development_inputs()
    corpus["cases"][0]["case_id"] = batch_a["cases"][0]["case_id"]
    _refresh_corpus_digest(corpus)

    with pytest.raises(ValueError, match="overlaps Batch A case"):
        context_selection.validate_retrieval_development_corpus(
            corpus,
            confirmatory_selection=batch_a,
        )


def test_retrieval_development_corpus_rejects_batch_a_path_overlap():
    corpus, batch_a = _load_retrieval_development_inputs()
    old_path = corpus["cases"][0]["context_files"][0]["path"]
    batch_a_path = batch_a["cases"][0]["context_candidate_paths"][0]
    corpus["cases"][0]["context_files"][0]["path"] = batch_a_path
    corpus["cases"][0]["context_labels"][0]["path"] = batch_a_path
    assert old_path != batch_a_path
    _refresh_corpus_digest(corpus)

    with pytest.raises(ValueError, match="overlaps Batch A path"):
        context_selection.validate_retrieval_development_corpus(
            corpus,
            confirmatory_selection=batch_a,
        )


def test_retrieval_development_corpus_rejects_rehashed_content_change():
    corpus, batch_a = _load_retrieval_development_inputs()
    corpus["cases"][0]["request"] = "변조된 개발 요청"
    _refresh_corpus_digest(corpus)

    with pytest.raises(ValueError, match="frozen corpus"):
        context_selection.validate_retrieval_development_corpus(
            corpus,
            confirmatory_selection=batch_a,
        )


def test_retrieval_development_corpus_requires_frozen_batch_a_selection():
    corpus, _ = _load_retrieval_development_inputs()
    forged_selection = {
        "selection_sha256": context_selection.canonical_digest([]),
        "cases": [],
    }

    with pytest.raises(ValueError, match="frozen Batch A selection"):
        context_selection.validate_retrieval_development_corpus(
            corpus,
            confirmatory_selection=forged_selection,
        )


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("surface", "surface quota"),
        ("provenance", "forbidden provenance"),
        ("digest", "corpus hash"),
    ],
)
def test_retrieval_development_corpus_rejects_invalid_contract(
    mutation, message
):
    corpus, batch_a = _load_retrieval_development_inputs()
    if mutation == "surface":
        corpus["cases"][0]["surface"] = "api_payload"
        _refresh_corpus_digest(corpus)
    elif mutation == "provenance":
        corpus["cases"][0]["followup_commit"] = "a" * 40
        _refresh_corpus_digest(corpus)
    else:
        corpus["corpus_sha256"] = "0" * 64

    with pytest.raises(ValueError, match=message):
        context_selection.validate_retrieval_development_corpus(
            corpus,
            confirmatory_selection=batch_a,
        )


def test_baseline_only_shortlist_is_deterministic_and_ignores_gold_labels():
    corpus, _ = _load_retrieval_development_inputs()
    case = corpus["cases"][0]

    first = context_selection.build_baseline_only_shortlist(
        case,
        maximum_selected_files=2,
    )
    changed_labels = deepcopy(case)
    changed_labels["context_labels"] = [{
        "path": "src/ui/StatusBadge.tsx",
        "weight": 99,
        "critical": True,
    }]
    second = context_selection.build_baseline_only_shortlist(
        changed_labels,
        maximum_selected_files=2,
    )

    assert first == second
    assert first == {
        "case_id": "retrieval-dev-ui-exit-guard",
        "selected_paths": [
            "src/ui/ExitGuardPanel.tsx",
            "src/ui/ExitGuardPanel.test.tsx",
        ],
    }


def test_retrieval_development_measurement_passes_frozen_quality_gate():
    corpus, batch_a = _load_retrieval_development_inputs()

    report = context_selection.measure_retrieval_development_corpus(
        corpus,
        confirmatory_selection=batch_a,
        maximum_selected_files=2,
    )

    assert report == {
        "case_count": 5,
        "candidate_file_count": 15,
        "selected_file_count": 10,
        "critical_path_recall": 1.0,
        "weighted_path_recall": 1.0,
        "file_count_reduction": 1 / 3,
        "development_gate_passed": True,
    }


def test_retrieval_development_measurement_fails_when_shortlist_is_too_small():
    corpus, batch_a = _load_retrieval_development_inputs()

    report = context_selection.measure_retrieval_development_corpus(
        corpus,
        confirmatory_selection=batch_a,
        maximum_selected_files=1,
    )

    assert report["critical_path_recall"] == 1.0
    assert report["weighted_path_recall"] < 1.0
    assert report["development_gate_passed"] is False


def test_retrieval_development_measurement_cli_writes_reproducible_report(
    tmp_path,
):
    output = tmp_path / "retrieval-development-report.json"
    completed = subprocess.run(
        [
            "python3",
            str(Path(context_selection.__file__)),
            "measure-development",
            str(FIXTURES / "omc_plan_retrieval_development.json"),
            str(FIXTURES / "omc_plan_confirmatory_batch_a_selection.json"),
            "--maximum-selected-files",
            "2",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text())
    assert report["development_gate_passed"] is True
    assert report["critical_path_recall"] == 1.0
    assert report["weighted_path_recall"] == 1.0


def test_retrieval_development_v2_is_frozen_and_realistically_bilingual():
    corpus, batch_a = _load_retrieval_development_v2_inputs()

    summary = context_selection.validate_retrieval_development_corpus(
        corpus,
        confirmatory_selection=batch_a,
    )

    assert summary["case_count"] == 5
    assert all(
        not any("가" <= char <= "힣" for char in item["content_utf8"])
        for case in corpus["cases"]
        for item in case["context_files"]
    )


def test_retrieval_development_v2_passes_quality_and_privacy_gate():
    corpus, batch_a = _load_retrieval_development_v2_inputs()

    report = context_selection.measure_retrieval_development_corpus(
        corpus,
        confirmatory_selection=batch_a,
        maximum_selected_files=2,
    )

    assert report == {
        "case_count": 5,
        "candidate_file_count": 25,
        "eligible_file_count": 24,
        "sensitive_file_count": 1,
        "selected_file_count": 10,
        "critical_path_recall": 1.0,
        "weighted_path_recall": 1.0,
        "file_count_reduction": 0.6,
        "development_gate_passed": True,
    }


def test_baseline_only_shortlist_excludes_sensitive_high_overlap_candidate():
    corpus, _ = _load_retrieval_development_v2_inputs()
    retry_case = next(
        case for case in corpus["cases"] if case["surface"] == "backend_rules"
    )

    shortlist = context_selection.build_baseline_only_shortlist(
        retry_case,
        maximum_selected_files=2,
    )

    assert shortlist["selected_paths"] == [
        "src/rules/retry_budget.py",
        "tests/rules/test_retry_budget.py",
    ]
    assert shortlist["sensitive_file_count"] == 1
    assert "src/rules/retry_budget_credentials.py" not in shortlist["selected_paths"]


def test_baseline_only_shortlist_keeps_bilingual_expansion_in_mixed_repository():
    shortlist = context_selection.build_baseline_only_shortlist(
        {
            "case_id": "mixed-language-order-dialog",
            "request": "문의 작성 화면에서 주문을 선택하는 다이얼로그를 추가한다",
            "context_files": [
                {
                    "path": "docs/개발도구.md",
                    "content_utf8": "작성 화면에서 도구를 선택하는 방법을 설명한다.",
                },
                {
                    "path": "src/order/OrderSelectionDialog.tsx",
                    "content_utf8": (
                        "export function OrderSelectionDialog() { "
                        "return openOrderSelector(); }"
                    ),
                },
                {
                    "path": "src/editor/AiBlock.constants.ts",
                    "content_utf8": "export const labels = {};\n" * 10_000,
                },
            ],
        },
        maximum_selected_files=1,
    )

    assert shortlist["selected_paths"] == [
        "src/order/OrderSelectionDialog.tsx"
    ]


def test_baseline_only_shortlist_keeps_unpaired_test_as_top_candidate():
    shortlist = context_selection.build_baseline_only_shortlist(
        {
            "case_id": "test-only-retry-regression",
            "request": "retry budget test regression",
            "context_files": [
                {
                    "path": "tests/test_retry_budget.py",
                    "content_utf8": "def test_retry_budget_regression(): pass\n",
                },
                {
                    "path": "src/catalog.py",
                    "content_utf8": "def list_catalog(): return []\n",
                },
            ],
        },
        maximum_selected_files=1,
    )

    assert shortlist["selected_paths"] == ["tests/test_retry_budget.py"]


def test_term_frequencies_compact_repeated_tokens():
    frequencies, term_count = context_selection._term_frequencies(
        "retry " * 10_000
    )

    assert frequencies == {"retry": 10_000}
    assert term_count == 10_000


def test_acronym_pascal_case_terms_remain_searchable():
    assert context_selection._lexical_terms("GTMEventTracker") == [
        "gtm",
        "event",
        "tracker",
    ]

    shortlist = context_selection.build_baseline_only_shortlist(
        {
            "case_id": "acronym-event-tracking",
            "request": "GTM event tracking",
            "context_files": [
                {
                    "path": "src/GTMEventTracker.ts",
                    "content_utf8": "export class GTMEventTracker {}\n",
                },
                {
                    "path": "src/EventList.ts",
                    "content_utf8": "export const EventList = [];\n",
                },
            ],
        },
        maximum_selected_files=1,
    )

    assert shortlist["selected_paths"] == ["src/GTMEventTracker.ts"]


def test_concept_coverage_bonus_can_overturn_base_rank(monkeypatch):
    score_batches = iter(([10.0, 9.0, 7.0], [0.0, 0.0, 0.0]))
    monkeypatch.setattr(
        context_selection,
        "_bm25_scores",
        lambda *args, **kwargs: next(score_batches),
    )
    shortlist = context_selection.build_baseline_only_shortlist(
        {
            "case_id": "unpaired-concept-coverage",
            "request": "alpha beta",
            "context_files": [
                {
                    "path": "src/alpha/A.py",
                    "content_utf8": "alpha\n",
                },
                {
                    "path": "src/alpha/B.py",
                    "content_utf8": "alpha\n",
                },
                {
                    "path": "src/beta/C.py",
                    "content_utf8": "beta\n",
                },
            ],
        },
        maximum_selected_files=2,
    )

    assert shortlist["selected_paths"] == [
        "src/alpha/A.py",
        "src/beta/C.py",
    ]


def test_baseline_workspace_shortlist_builds_unsigned_packet_bound_draft(
    tmp_path, monkeypatch
):
    (tmp_path / "source").mkdir()
    repo_roots, selection, prior_commits, packet = _prepare(
        tmp_path / "source",
        monkeypatch,
        request_by_index={0: "retry budget 차단 규칙을 강화한다"},
        text_files_by_index={
            0: {
                "src/retry_budget.py": "def stop_when_budget_exceeded(): pass\n",
                "src/catalog.py": "def list_products(): return []\n",
            },
        },
    )
    workspace_root = tmp_path / "baseline-workspaces"
    context_selection.materialize_baseline_workspaces(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        output_root=workspace_root,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
    )

    draft = context_selection.build_baseline_workspace_shortlists(
        packet,
        workspace_root=workspace_root,
        maximum_selected_files=2,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
    )

    assert draft["status"] == "draft"
    assert draft["packet_sha256"] == packet["packet_sha256"]
    assert draft["retrieval_policy_sha256"] == packet["retrieval_policy_sha256"]
    assert draft["cases"][0]["selected_paths"][0] == "src/retry_budget.py"
    assert "signoff" not in draft
    assert "followup_commit" not in json.dumps(draft)


def test_baseline_workspace_shortlist_rejects_tampered_packet(
    tmp_path, monkeypatch
):
    (tmp_path / "source").mkdir()
    repo_roots, selection, prior_commits, packet = _prepare(
        tmp_path / "source",
        monkeypatch,
    )
    workspace_root = tmp_path / "baseline-workspaces"
    context_selection.materialize_baseline_workspaces(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=prior_commits,
        output_root=workspace_root,
        trusted_selection_public_keys=_trusted_selection_keys(packet),
    )
    packet["cases"][0]["request"] = "tampered"

    with pytest.raises(ValueError, match="packet hash"):
        context_selection.build_baseline_workspace_shortlists(
            packet,
            workspace_root=workspace_root,
            maximum_selected_files=2,
            trusted_selection_public_keys=_trusted_selection_keys(packet),
        )


def test_retrieval_development_v3_passes_concept_coverage_gate():
    corpus, batch_a = _load_retrieval_development_v3_inputs()

    report = context_selection.measure_retrieval_development_corpus(
        corpus,
        confirmatory_selection=batch_a,
        maximum_selected_files=2,
    )

    assert report == {
        "case_count": 5,
        "candidate_file_count": 30,
        "eligible_file_count": 30,
        "sensitive_file_count": 0,
        "selected_file_count": 10,
        "critical_path_recall": 1.0,
        "weighted_path_recall": 1.0,
        "file_count_reduction": 2 / 3,
        "development_gate_passed": True,
        "candidate_input_token_upper_bound": 54_926,
        "selected_input_token_upper_bound": 52_385,
        "input_token_upper_bound_reduction": 2_541 / 54_926,
    }
