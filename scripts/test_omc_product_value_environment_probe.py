from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

import omc_product_value_acceptance as acceptance
import omc_product_value_environment_probe as probe


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _readonly_cache(tmp_path: Path, member_name: str = "node_modules/pkg/index.js") -> Path:
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / "workspace-cache.tar.gz"
    payload = b"module.exports = true;\n"
    with tarfile.open(archive, "w:gz") as stream:
        info = tarfile.TarInfo(member_name)
        info.size = len(payload)
        info.mode = 0o444
        stream.addfile(info, io.BytesIO(payload))
    archive.chmod(0o444)
    cache.chmod(0o555)
    return cache


def _request(tmp_path: Path, cache: Path) -> dict[str, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lock = workspace / "environment.lock"
    lock.write_text("dependency==1.0\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.write_text("runtime-v1\n", encoding="utf-8")
    return {
        "workspace": str(workspace),
        "source_commit": "1" * 40,
        "dependency_lock_path": "environment.lock",
        "dependency_lock_sha256": _sha(lock),
        "cache_path": str(cache),
        "cache_sha256": acceptance.canonical_cache_inventory_sha256(cache),
        "runtime_identity_path": str(runtime),
        "runtime_identity_sha256": _sha(runtime),
    }


def test_probe_materializes_hash_bound_workspace_cache(tmp_path: Path) -> None:
    cache = _readonly_cache(tmp_path)
    request = _request(tmp_path, cache)

    result = probe.probe_environment(**request)

    workspace = Path(request["workspace"])
    assert (workspace / "node_modules/pkg/index.js").read_text() == (
        "module.exports = true;\n"
    )
    assert result == {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": "1" * 40,
        "dependency_lock_sha256": request["dependency_lock_sha256"],
        "cache_sha256": request["cache_sha256"],
        "runtime_identity_sha256": request["runtime_identity_sha256"],
        "cache_path": request["cache_path"],
        "cache_readonly": True,
    }


def test_probe_rejects_archive_path_escape(tmp_path: Path) -> None:
    cache = _readonly_cache(tmp_path, "../escaped.txt")
    request = _request(tmp_path, cache)

    with pytest.raises(ValueError, match="environment_cache_archive_unsafe"):
        probe.probe_environment(**request)

    assert not (tmp_path / "escaped.txt").exists()


def test_probe_materializes_safe_internal_symlink(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / "workspace-cache.tar.gz"
    payload = b"#!/usr/bin/env node\n"
    with tarfile.open(archive, "w:gz") as stream:
        target = tarfile.TarInfo("node_modules/pkg/bin/cli.js")
        target.size = len(payload)
        target.mode = 0o555
        stream.addfile(target, io.BytesIO(payload))
        link = tarfile.TarInfo("node_modules/.bin/pkg")
        link.type = tarfile.SYMTYPE
        link.linkname = "../pkg/bin/cli.js"
        stream.addfile(link)
    archive.chmod(0o444)
    cache.chmod(0o555)
    request = _request(tmp_path, cache)

    probe.probe_environment(**request)

    materialized = Path(request["workspace"]) / "node_modules/.bin/pkg"
    assert materialized.is_symlink()
    assert materialized.resolve().read_bytes() == payload


def test_probe_rejects_symlink_escape(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / "workspace-cache.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        link = tarfile.TarInfo("node_modules/.bin/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../escaped.txt"
        stream.addfile(link)
    archive.chmod(0o444)
    cache.chmod(0o555)
    request = _request(tmp_path, cache)

    with pytest.raises(ValueError, match="environment_cache_archive_unsafe"):
        probe.probe_environment(**request)


def test_probe_rejects_cache_drift_before_materialization(tmp_path: Path) -> None:
    cache = _readonly_cache(tmp_path)
    request = _request(tmp_path, cache)
    request["cache_sha256"] = "f" * 64

    with pytest.raises(ValueError, match="environment_probe_mismatch"):
        probe.probe_environment(**request)

    assert not (Path(request["workspace"]) / "node_modules").exists()


def test_probe_rejects_archive_entry_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = _readonly_cache(tmp_path)
    request = _request(tmp_path, cache)
    monkeypatch.setattr(probe, "MAX_ARCHIVE_ENTRIES", 0)

    with pytest.raises(ValueError, match="environment_cache_archive_limit"):
        probe.probe_environment(**request)


def test_probe_enforces_entry_limit_without_loading_all_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = _readonly_cache(tmp_path)
    request = _request(tmp_path, cache)

    class StreamingArchive:
        def __enter__(self) -> "StreamingArchive":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def getmembers(self) -> list[tarfile.TarInfo]:
            raise AssertionError("archive members must not be loaded eagerly")

        def __iter__(self):
            one = tarfile.TarInfo("node_modules/one")
            one.type = tarfile.DIRTYPE
            two = tarfile.TarInfo("node_modules/two")
            two.type = tarfile.DIRTYPE
            yield one
            yield two

    monkeypatch.setattr(probe, "MAX_ARCHIVE_ENTRIES", 1)
    monkeypatch.setattr(probe.tarfile, "open", lambda *_args, **_kwargs: StreamingArchive())

    with pytest.raises(ValueError, match="environment_cache_archive_limit"):
        probe.probe_environment(**request)


def test_cli_emits_machine_readable_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cache = _readonly_cache(tmp_path)
    request = _request(tmp_path, cache)

    exit_code = probe.main([
        "--source-commit",
        request["source_commit"],
        "--dependency-lock-path",
        request["dependency_lock_path"],
        "--dependency-lock-sha256",
        "0" * 64,
        "--cache-path",
        request["cache_path"],
        "--cache-sha256",
        request["cache_sha256"],
        "--runtime-identity-path",
        request["runtime_identity_path"],
        "--runtime-identity-sha256",
        request["runtime_identity_sha256"],
        "--workspace",
        request["workspace"],
    ])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "reason_code": "environment_probe_mismatch",
    }
