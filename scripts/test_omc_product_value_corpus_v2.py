from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import omc_product_value_corpus_v2 as corpus


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "product-value-batch-v1"
    (root / "packets").mkdir(parents=True)
    (root / "sources").mkdir()
    workloads = []
    private_selection = []
    source_roots = {}
    for index in range(1, 7):
        workload_id = f"pv-{index:02d}"
        alias = f"source-{chr(96 + index)}"
        source = root / "sources" / alias
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        _git(source, "config", "user.email", "corpus@example.com")
        _git(source, "config", "user.name", "Corpus Test")
        (source / "tracked.txt").write_text(f"source {index}\n", encoding="utf-8")
        _git(source, "add", "tracked.txt")
        _git(source, "commit", "-qm", "snapshot")
        commit = _git(source, "rev-parse", "HEAD")
        identity = hashlib.sha256(f"identity-{index}".encode()).hexdigest()
        packet = {
            "schema_version": "omc-product-value-execution-packet/v1",
            "workload_id": workload_id,
            "repo_alias": alias,
            "source_commit": commit,
            "request": f"request {index}",
            "dod": f"dod {index}",
            "verification": {"argv": ["python3", "-m", "pytest"]},
            "arms": {
                "omc": {"prompt": f"request {index}", "mode": "bounded_n_child"},
                "baseline": {"prompt": f"request {index}", "mode": "single_agent"},
            },
        }
        (root / "packets" / f"{workload_id}.json").write_text(
            json.dumps(packet), encoding="utf-8"
        )
        workloads.append({
            "workload_id": workload_id,
            "repo_alias": alias,
            "repository_identity_sha256": identity,
            "source_commit": commit,
            "request_sha256": _canonical_sha(packet["request"]),
            "dod_sha256": _canonical_sha(packet["dod"]),
            "verification_sha256": _canonical_sha(packet["verification"]),
            "execution_packet_sha256": _canonical_sha(packet),
        })
        private_selection.append({
            "workload_id": workload_id,
            "repo_alias": alias,
            "original_repository": f"/private/source-{index}",
            "original_baseline_commit": hashlib.sha1(str(index).encode()).hexdigest(),
            "snapshot_commit": commit,
            "repository_identity_sha256": identity,
        })
        source_roots[alias] = {
            "path": str(source),
            "identity_sha256": identity,
        }
    (root / "workloads.json").write_text(json.dumps(workloads), encoding="utf-8")
    (root / "private-selection.json").write_text(
        json.dumps(private_selection), encoding="utf-8"
    )
    (root / "source-roots.json").write_text(json.dumps(source_roots), encoding="utf-8")
    (root / "selection-receipt.json").write_text(
        json.dumps({
            "schema_version": "omc-product-value-selection/v1",
            "workload_count": 6,
            "public_payload_sha256": "a" * 64,
        }),
        encoding="utf-8",
    )
    return root


def test_build_corpus_v2_preserves_v1_and_rebinds_changed_sources(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)
    before = corpus.corpus_source_digest(source)
    output = tmp_path / "product-value-batch-v2"

    receipt = corpus.build_corpus_v2(
        source,
        output,
        batch_id="product-value-batch-v2",
        expected_parent_source_digest=before,
        dependency_locks={
            "source-e": "pytest==9.0.2\n",
            "source-f": "pytest==9.0.2\njsonschema==4.25.1\n",
        },
    )

    assert corpus.corpus_source_digest(source) == before
    assert receipt["schema_version"] == "omc-product-value-selection/v2"
    assert receipt["batch_id"] == "product-value-batch-v2"
    assert receipt["parent_source_digest"] == before
    assert corpus.corpus_source_digest(output)
    workloads = json.loads((output / "workloads.json").read_text())
    original = json.loads((source / "workloads.json").read_text())
    for index in range(4):
        assert workloads[index]["source_commit"] == original[index]["source_commit"]
        assert workloads[index]["repository_identity_sha256"] == original[index]["repository_identity_sha256"]
        alias = workloads[index]["repo_alias"]
        assert not (source / "sources" / alias / "benchmark-dependencies.lock").exists()
    for index in (4, 5):
        assert workloads[index]["source_commit"] != original[index]["source_commit"]
        assert workloads[index]["repository_identity_sha256"] != original[index]["repository_identity_sha256"]
        alias = workloads[index]["repo_alias"]
        assert not (source / "sources" / alias / "benchmark-dependencies.lock").exists()
        lock = output / "sources" / alias / "benchmark-dependencies.lock"
        assert lock.is_file()
        assert _git(output / "sources" / alias, "status", "--short") == ""
        packet = json.loads(
            (output / "packets" / f"{workloads[index]['workload_id']}.json").read_text()
        )
        assert packet["source_commit"] == workloads[index]["source_commit"]
        assert _canonical_sha(packet) == workloads[index]["execution_packet_sha256"]


def test_build_corpus_v2_rejects_incomplete_locks_and_existing_output(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)
    output = tmp_path / "product-value-batch-v2"
    approved_digest = corpus.corpus_source_digest(source)

    with pytest.raises(ValueError, match="corpus_v2_dependency_locks_invalid"):
        corpus.build_corpus_v2(
            source,
            output,
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={"source-e": "pytest==9.0.2\n"},
        )

    output.mkdir()
    with pytest.raises(ValueError, match="corpus_v2_output_exists"):
        corpus.build_corpus_v2(
            source,
            output,
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )


def test_build_corpus_v2_rejects_packet_tampering(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    approved_digest = corpus.corpus_source_digest(source)
    packet_path = source / "packets" / "pv-01.json"
    packet = json.loads(packet_path.read_text())
    packet["request"] = "tampered request"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    with pytest.raises(ValueError, match="corpus_v2_packet_binding_invalid"):
        corpus.build_corpus_v2(
            source,
            tmp_path / "product-value-batch-v2",
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )


def test_build_corpus_v2_rejects_duplicate_workload_ids(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    approved_digest = corpus.corpus_source_digest(source)
    workloads_path = source / "workloads.json"
    workloads = json.loads(workloads_path.read_text())
    workloads[-1]["workload_id"] = workloads[-2]["workload_id"]
    workloads_path.write_text(json.dumps(workloads), encoding="utf-8")

    with pytest.raises(ValueError, match="corpus_v2_input_invalid"):
        corpus.build_corpus_v2(
            source,
            tmp_path / "product-value-batch-v2",
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )


def test_build_corpus_v2_rejects_path_escaping_repo_alias(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    approved_digest = corpus.corpus_source_digest(source)
    workloads_path = source / "workloads.json"
    workloads = json.loads(workloads_path.read_text())
    packet_path = source / "packets" / "pv-01.json"
    packet = json.loads(packet_path.read_text())
    selection_path = source / "private-selection.json"
    selection = json.loads(selection_path.read_text())
    roots_path = source / "source-roots.json"
    roots = json.loads(roots_path.read_text())

    packet["repo_alias"] = "../../escaped"
    workloads[0]["repo_alias"] = packet["repo_alias"]
    workloads[0]["execution_packet_sha256"] = _canonical_sha(packet)
    selection[0]["repo_alias"] = packet["repo_alias"]
    roots[packet["repo_alias"]] = roots.pop("source-a")
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    workloads_path.write_text(json.dumps(workloads), encoding="utf-8")
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    roots_path.write_text(json.dumps(roots), encoding="utf-8")

    with pytest.raises(ValueError, match="corpus_v2_input_invalid"):
        corpus.build_corpus_v2(
            source,
            tmp_path / "product-value-batch-v2",
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )


def test_build_corpus_v2_rejects_coordinated_parent_tampering(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)
    approved_digest = corpus.corpus_source_digest(source)
    packet_path = source / "packets" / "pv-01.json"
    packet = json.loads(packet_path.read_text())
    workloads_path = source / "workloads.json"
    workloads = json.loads(workloads_path.read_text())

    packet["request"] = "coordinated tamper"
    workloads[0]["request_sha256"] = _canonical_sha(packet["request"])
    workloads[0]["execution_packet_sha256"] = _canonical_sha(packet)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    workloads_path.write_text(json.dumps(workloads), encoding="utf-8")

    with pytest.raises(ValueError, match="corpus_v2_parent_digest_mismatch"):
        corpus.build_corpus_v2(
            source,
            tmp_path / "product-value-batch-v2",
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )


def test_build_corpus_v2_rejects_invalid_parent_receipt(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    receipt_path = source / "selection-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["public_payload_sha256"] = "not-a-sha256"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    current_digest = corpus.corpus_source_digest(source)

    with pytest.raises(ValueError, match="corpus_v2_parent_receipt_invalid"):
        corpus.build_corpus_v2(
            source,
            tmp_path / "product-value-batch-v2",
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=current_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )


def test_build_corpus_v2_uses_the_approved_snapshot_without_rereading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _fixture(tmp_path)
    approved_digest = corpus.corpus_source_digest(source)
    real_inputs = corpus._inputs
    input_reads = 0

    def counted_inputs(root: Path):
        nonlocal input_reads
        input_reads += 1
        return real_inputs(root)

    monkeypatch.setattr(corpus, "_inputs", counted_inputs)
    corpus.build_corpus_v2(
        source,
        tmp_path / "product-value-batch-v2",
        batch_id="product-value-batch-v2",
        expected_parent_source_digest=approved_digest,
        dependency_locks={
            "source-e": "pytest==9.0.2\n",
            "source-f": "pytest==9.0.2\n",
        },
    )

    assert input_reads == 2


def test_build_corpus_v2_normalizes_final_validation_failure_as_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _fixture(tmp_path)
    approved_digest = corpus.corpus_source_digest(source)
    output = tmp_path / "product-value-batch-v2"

    def invalid_final_snapshot(_root: Path) -> str:
        raise ValueError("corpus_v2_packet_binding_invalid")

    monkeypatch.setattr(corpus, "corpus_source_digest", invalid_final_snapshot)
    with pytest.raises(ValueError, match="^corpus_v2_source_mutated$"):
        corpus.build_corpus_v2(
            source,
            output,
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )

    assert not output.exists()


def test_build_corpus_v2_does_not_replace_late_output_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _fixture(tmp_path)
    approved_digest = corpus.corpus_source_digest(source)
    output = tmp_path / "product-value-batch-v2"
    real_assert_unchanged = corpus._assert_source_unchanged
    reserved = False

    def reserve_output(root: Path, expected_digest: str) -> None:
        nonlocal reserved
        real_assert_unchanged(root, expected_digest)
        if not reserved:
            output.mkdir()
            reserved = True

    monkeypatch.setattr(corpus, "_assert_source_unchanged", reserve_output)
    with pytest.raises(ValueError, match="^corpus_v2_output_exists$"):
        corpus.build_corpus_v2(
            source,
            output,
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )

    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_build_corpus_v2_removes_its_reservation_when_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _fixture(tmp_path)
    approved_digest = corpus.corpus_source_digest(source)
    output = tmp_path / "product-value-batch-v2"
    real_replace = corpus.os.replace

    def fail_publish(source_path: Path, destination_path: Path) -> None:
        if Path(destination_path).parent == output:
            raise OSError("publish failed")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(corpus.os, "replace", fail_publish)
    with pytest.raises(OSError, match="^publish failed$"):
        corpus.build_corpus_v2(
            source,
            output,
            batch_id="product-value-batch-v2",
            expected_parent_source_digest=approved_digest,
            dependency_locks={
                "source-e": "pytest==9.0.2\n",
                "source-f": "pytest==9.0.2\n",
            },
        )

    assert not output.exists()
    assert list(tmp_path.glob(".product-value-batch-v2-*")) == []
