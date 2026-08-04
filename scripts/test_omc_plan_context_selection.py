import base64
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


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def _batch(tmp_path: Path) -> tuple[dict[str, Path], dict, list[str]]:
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
        source.write_text("def run():\n    return None\n")
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
            "request": f"서비스 {index} 실행 계획을 작성한다",
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


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_roots, selection, prior_commits = _batch(tmp_path)
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
