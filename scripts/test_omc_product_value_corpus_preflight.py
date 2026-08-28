from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import omc_product_value_corpus_preflight as preflight
import omc_product_value_acceptance as acceptance


def _sha(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "corpus"
    packets = root / "packets"
    sources = root / "sources"
    packets.mkdir(parents=True)
    sources.mkdir()
    workloads = []
    source_roots = {}
    environments = {}
    runtime = tmp_path / "runtime_probe.py"
    runtime.write_text("# runtime identity\n", encoding="utf-8")
    runtime_sha = hashlib.sha256(runtime.read_bytes()).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    cache_entry = cache / "runtime.txt"
    cache_entry.write_text("cached runtime\n", encoding="utf-8")
    cache_entry.chmod(0o444)
    cache.chmod(0o555)
    cache_sha = acceptance.canonical_cache_inventory_sha256(
        cache, require_readonly=True
    )

    for index in range(1, 7):
        workload_id = f"pv-{index:02d}"
        alias = f"source-{chr(96 + index)}"
        source = sources / alias
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        _git(source, "config", "user.email", "preflight@example.com")
        _git(source, "config", "user.name", "Preflight Test")
        (source / "tracked.txt").write_text(f"source {index}\n", encoding="utf-8")
        (source / "requirements.lock").write_text(
            "dependency==1.0\n", encoding="utf-8"
        )
        (source / "tests").mkdir()
        surface = source / "tests" / "test_surface.py"
        surface.write_text("def test_surface(): pass\n", encoding="utf-8")
        _git(source, "add", ".")
        _git(source, "commit", "-qm", "snapshot")
        commit = _git(source, "rev-parse", "HEAD")
        identity = hashlib.sha256(alias.encode()).hexdigest()
        packet = {
            "schema_version": "omc-product-value-execution-packet/v1",
            "workload_id": workload_id,
            "repo_alias": alias,
            "source_commit": commit,
            "request": f"request {index}",
            "dod": f"dod {index}",
            "verification": {"argv": ["python3", "-m", "pytest"]},
        }
        (packets / f"{workload_id}.json").write_text(
            json.dumps(packet), encoding="utf-8"
        )
        workloads.append(
            {
                "workload_id": workload_id,
                "repo_alias": alias,
                "repository_identity_sha256": identity,
                "source_commit": commit,
                "request_sha256": _sha(packet["request"]),
                "dod_sha256": _sha(packet["dod"]),
                "verification_sha256": _sha(packet["verification"]),
                "execution_packet_sha256": _sha(packet),
            }
        )
        source_roots[alias] = {
            "path": str(source),
            "identity_sha256": identity,
        }
        environments[workload_id] = {
            "dependency_lock_path": "requirements.lock",
            "direct_surface_verification_path": "tests/test_surface.py",
            "direct_surface_verification_sha256": hashlib.sha256(
                surface.read_bytes()
            ).hexdigest(),
            "cache_path": str(cache),
            "runtime_identity_path": str(runtime),
            "readiness": {
                "argv": [
                    str(runtime),
                    "--source-commit",
                    commit,
                    "--dependency-lock-path",
                    "requirements.lock",
                    "--dependency-lock-sha256",
                    hashlib.sha256(
                        (source / "requirements.lock").read_bytes()
                    ).hexdigest(),
                    "--cache-path",
                    str(cache),
                    "--cache-sha256",
                    cache_sha,
                    "--runtime-identity-path",
                    str(runtime),
                    "--runtime-identity-sha256",
                    runtime_sha,
                ]
            },
        }

    (root / "workloads.json").write_text(json.dumps(workloads), encoding="utf-8")
    (root / "source-roots.json").write_text(
        json.dumps(source_roots), encoding="utf-8"
    )
    environment_path = tmp_path / "environments.json"
    environment_path.write_text(json.dumps(environments), encoding="utf-8")
    return root, environment_path


def test_preflight_reports_six_ready_workloads_without_provider_calls(
    tmp_path: Path,
) -> None:
    root, environments = _fixture(tmp_path)

    report = preflight.preflight_corpus(root, environments)

    assert report["status"] == "ready"
    assert report["workload_count"] == 6
    assert report["ready_count"] == 6
    assert report["provider_call_count"] == 0
    assert [item["workload_id"] for item in report["workloads"]] == [
        f"pv-{index:02d}" for index in range(1, 7)
    ]
    assert all(item["status"] == "ready" for item in report["workloads"])
    assert report["report_sha256"] == preflight.report_sha256(report)


def test_preflight_fail_closes_missing_environment_and_packet_input(
    tmp_path: Path,
) -> None:
    root, environments_path = _fixture(tmp_path)
    environments = json.loads(environments_path.read_text())
    del environments["pv-02"]
    environments_path.write_text(json.dumps(environments), encoding="utf-8")
    packet_path = root / "packets" / "pv-03.json"
    packet = json.loads(packet_path.read_text())
    packet["request"] = ""
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    report = preflight.preflight_corpus(root, environments_path)

    by_id = {item["workload_id"]: item for item in report["workloads"]}
    assert report["status"] == "blocked"
    assert report["ready_count"] == 4
    assert by_id["pv-02"]["reason_codes"] == ["environment_spec_missing"]
    assert "request_missing" in by_id["pv-03"]["reason_codes"]
    assert "execution_packet_binding_mismatch" in by_id["pv-03"]["reason_codes"]
    assert report["provider_call_count"] == 0


def test_preflight_fail_closes_dirty_and_commit_mismatched_sources(
    tmp_path: Path,
) -> None:
    root, environments = _fixture(tmp_path)
    (root / "sources" / "source-d" / "untracked.txt").write_text(
        "dirty\n", encoding="utf-8"
    )
    source = root / "sources" / "source-e"
    (source / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-qm", "new head")

    report = preflight.preflight_corpus(root, environments)

    by_id = {item["workload_id"]: item for item in report["workloads"]}
    assert report["status"] == "blocked"
    assert report["ready_count"] == 4
    assert by_id["pv-04"]["reason_codes"] == ["source_dirty"]
    assert by_id["pv-05"]["reason_codes"] == ["source_commit_mismatch"]
    assert report["provider_call_count"] == 0


def test_preflight_is_deterministic_and_does_not_mutate_inputs(tmp_path: Path) -> None:
    root, environments = _fixture(tmp_path)
    before = {
        path: path.read_bytes()
        for path in [*root.rglob("*.json"), environments]
    }

    first = preflight.preflight_corpus(root, environments)
    second = preflight.preflight_corpus(root, environments)

    assert first == second
    assert {path: path.read_bytes() for path in before} == before


def test_preflight_rejects_unexpected_top_level_entries(tmp_path: Path) -> None:
    root, environments_path = _fixture(tmp_path)
    workloads = json.loads((root / "workloads.json").read_text())
    workloads.append({"workload_id": "pv-07", "repo_alias": "source-g"})
    (root / "workloads.json").write_text(json.dumps(workloads), encoding="utf-8")
    source_roots = json.loads((root / "source-roots.json").read_text())
    source_roots["source-g"] = source_roots["source-a"]
    (root / "source-roots.json").write_text(
        json.dumps(source_roots), encoding="utf-8"
    )
    environments = json.loads(environments_path.read_text())
    environments["pv-07"] = environments["pv-01"]
    environments_path.write_text(json.dumps(environments), encoding="utf-8")

    report = preflight.preflight_corpus(root, environments_path)

    assert report["status"] == "blocked"
    assert report["ready_count"] == 6
    assert report["reason_codes"] == [
        "workload_collection_invalid",
        "unexpected_workload_ids",
        "unexpected_source_aliases",
        "unexpected_environment_ids",
    ]


def test_preflight_rejects_environment_artifacts_that_are_not_available(
    tmp_path: Path,
) -> None:
    root, environments_path = _fixture(tmp_path)
    environments = json.loads(environments_path.read_text())
    environment = environments["pv-01"]
    environment["dependency_lock_path"] = "missing.lock"
    environment["readiness"]["argv"][0] = str(tmp_path / "missing-runtime")
    environments_path.write_text(json.dumps(environments), encoding="utf-8")

    report = preflight.preflight_corpus(root, environments_path)

    first = report["workloads"][0]
    assert report["status"] == "blocked"
    assert first["status"] == "blocked"
    assert "environment_artifact_mismatch" in first["reason_codes"]


def test_preflight_report_hash_binds_distinct_inputs(tmp_path: Path) -> None:
    root_a, environments_a = _fixture(tmp_path / "a")
    root_b, environments_b = _fixture(tmp_path / "b")

    report_a = preflight.preflight_corpus(root_a, environments_a)
    report_b = preflight.preflight_corpus(root_b, environments_b)

    assert report_a["status"] == report_b["status"] == "ready"
    assert report_a["input_binding_sha256"] != report_b["input_binding_sha256"]
    assert report_a["report_sha256"] != report_b["report_sha256"]


def test_preflight_rejects_malformed_extra_workload_entry(tmp_path: Path) -> None:
    root, environments = _fixture(tmp_path)
    workloads = json.loads((root / "workloads.json").read_text())
    workloads.append({"unexpected": True})
    (root / "workloads.json").write_text(json.dumps(workloads), encoding="utf-8")

    report = preflight.preflight_corpus(root, environments)

    assert report["status"] == "blocked"
    assert report["reason_codes"] == ["workload_collection_invalid"]


@pytest.mark.parametrize("identity", [None, "not-a-sha"])
def test_preflight_requires_valid_repository_identity(
    tmp_path: Path,
    identity: str | None,
) -> None:
    root, environments = _fixture(tmp_path)
    workloads = json.loads((root / "workloads.json").read_text())
    source_roots = json.loads((root / "source-roots.json").read_text())
    if identity is None:
        workloads[0].pop("repository_identity_sha256")
        source_roots["source-a"].pop("identity_sha256")
    else:
        workloads[0]["repository_identity_sha256"] = identity
        source_roots["source-a"]["identity_sha256"] = identity
    (root / "workloads.json").write_text(json.dumps(workloads), encoding="utf-8")
    (root / "source-roots.json").write_text(
        json.dumps(source_roots), encoding="utf-8"
    )

    report = preflight.preflight_corpus(root, environments)

    assert report["status"] == "blocked"
    assert report["workloads"][0]["reason_codes"] == [
        "source_identity_invalid"
    ]


def test_preflight_requires_source_path_to_be_git_root(tmp_path: Path) -> None:
    root, environments_path = _fixture(tmp_path)
    source = root / "sources" / "source-a"
    nested = source / "nested"
    nested.mkdir()
    shutil.move(str(source / "requirements.lock"), nested / "requirements.lock")
    shutil.move(str(source / "tests"), nested / "tests")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "move inputs below nested source")
    commit = _git(source, "rev-parse", "HEAD")

    packet_path = root / "packets" / "pv-01.json"
    packet = json.loads(packet_path.read_text())
    packet["source_commit"] = commit
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    workloads = json.loads((root / "workloads.json").read_text())
    workloads[0]["source_commit"] = commit
    workloads[0]["execution_packet_sha256"] = _sha(packet)
    (root / "workloads.json").write_text(json.dumps(workloads), encoding="utf-8")
    source_roots = json.loads((root / "source-roots.json").read_text())
    source_roots["source-a"]["path"] = str(nested)
    (root / "source-roots.json").write_text(
        json.dumps(source_roots), encoding="utf-8"
    )
    environments = json.loads(environments_path.read_text())
    readiness = environments["pv-01"]["readiness"]["argv"]
    readiness[readiness.index("--source-commit") + 1] = commit
    environments_path.write_text(json.dumps(environments), encoding="utf-8")

    report = preflight.preflight_corpus(root, environments_path)

    assert report["status"] == "blocked"
    assert report["workloads"][0]["reason_codes"] == [
        "source_repository_root_mismatch"
    ]


def test_preflight_requires_environment_artifacts_in_source_commit(
    tmp_path: Path,
) -> None:
    root, environments_path = _fixture(tmp_path)
    source = root / "sources" / "source-a"
    (source / ".gitignore").write_text(
        "ignored.lock\nignored_surface.py\n", encoding="utf-8"
    )
    _git(source, "add", ".gitignore")
    _git(source, "commit", "-qm", "ignore uncommitted environment inputs")
    commit = _git(source, "rev-parse", "HEAD")
    lock = source / "ignored.lock"
    lock.write_text("ignored==1\n", encoding="utf-8")
    surface = source / "ignored_surface.py"
    surface.write_text("def test_ignored(): pass\n", encoding="utf-8")

    packet_path = root / "packets" / "pv-01.json"
    packet = json.loads(packet_path.read_text())
    packet["source_commit"] = commit
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    workloads = json.loads((root / "workloads.json").read_text())
    workloads[0]["source_commit"] = commit
    workloads[0]["execution_packet_sha256"] = _sha(packet)
    (root / "workloads.json").write_text(json.dumps(workloads), encoding="utf-8")
    environments = json.loads(environments_path.read_text())
    environment = environments["pv-01"]
    environment["dependency_lock_path"] = "ignored.lock"
    environment["direct_surface_verification_path"] = "ignored_surface.py"
    environment["direct_surface_verification_sha256"] = hashlib.sha256(
        surface.read_bytes()
    ).hexdigest()
    readiness = environment["readiness"]["argv"]
    readiness[readiness.index("--source-commit") + 1] = commit
    readiness[readiness.index("--dependency-lock-path") + 1] = "ignored.lock"
    readiness[readiness.index("--dependency-lock-sha256") + 1] = hashlib.sha256(
        lock.read_bytes()
    ).hexdigest()
    environments_path.write_text(json.dumps(environments), encoding="utf-8")

    assert _git(source, "status", "--porcelain") == ""
    report = preflight.preflight_corpus(root, environments_path)

    assert report["status"] == "blocked"
    assert report["workloads"][0]["reason_codes"] == [
        "environment_artifact_not_commit_bound"
    ]
