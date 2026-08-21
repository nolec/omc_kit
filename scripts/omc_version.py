#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omc_source_hash import source_sha256


VERSION_FILE = "VERSION"
INSTALL_RECEIPT = Path(".omc") / "install-receipt.json"
_STABLE_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class VersionContractError(ValueError):
    pass


@dataclass(frozen=True)
class SourceIdentity:
    version: str
    sha256: str
    revision: str | None


def parse_version(raw: str) -> tuple[int, int, int]:
    value = raw.strip()
    match = _STABLE_SEMVER.fullmatch(value)
    if match is None:
        raise VersionContractError(
            f"unsupported OMC version {value!r}; expected MAJOR.MINOR.PATCH"
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def read_source_version(source_kit: Path) -> str:
    version_path = source_kit / VERSION_FILE
    try:
        value = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise VersionContractError(f"OMC VERSION is unavailable: {version_path}") from exc
    parse_version(value)
    return value


def source_revision(source_kit: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_kit), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    revision = proc.stdout.strip()
    return revision if re.fullmatch(r"[0-9a-fA-F]{40,64}", revision) else None


def capture_source_identity(source_kit: Path) -> SourceIdentity:
    return SourceIdentity(
        version=read_source_version(source_kit),
        sha256=source_sha256(source_kit),
        revision=source_revision(source_kit),
    )


def _looks_like_source_kit(path: Path) -> bool:
    return (
        (path / VERSION_FILE).is_file()
        and (path / "templates").is_dir()
        and (path / "scripts" / "install.py").is_file()
        and (path / "prompts" / "team.json").is_file()
    )


def _read_receipt(target: Path) -> tuple[dict[str, Any] | None, str]:
    path = target / INSTALL_RECEIPT
    if not path.is_file():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "invalid"
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        return None, "invalid"
    schema = payload.get("schema_version")
    if schema == 1:
        return payload, "legacy"
    if schema != 2:
        return None, "invalid"
    version = payload.get("omc_version")
    if not isinstance(version, str):
        return None, "invalid"
    try:
        parse_version(version)
    except VersionContractError:
        return None, "invalid"
    return payload, "current"


def _integrity_status(raw: str) -> str:
    return {
        "ok": "clean",
        "failed": "drifted",
        "missing": "missing",
    }.get(raw, "invalid")


def _overall_status(
    *,
    receipt_status: str,
    release_status: str,
    source_status: str,
    install_integrity: str,
) -> str:
    if receipt_status in {"missing", "invalid"} or install_integrity == "invalid":
        return "invalid"
    if install_integrity in {"drifted", "missing"}:
        return "install_drifted"
    if release_status == "upgrade_available":
        return "upgrade_available"
    if release_status == "newer_than_source":
        return "newer_than_source"
    if source_status == "modified":
        return "source_modified"
    if receipt_status == "legacy":
        return "legacy_receipt"
    if release_status == "source_unavailable":
        return "source_unavailable"
    if release_status == "invalid" or source_status == "invalid":
        return "invalid"
    return "up_to_date"


def version_readiness(
    target: Path,
    *,
    source_path: str | None,
    install_integrity_status: str,
) -> dict[str, str | None]:
    resolved = target.resolve()
    receipt, receipt_status = _read_receipt(resolved)
    installed_version = (
        str(receipt.get("omc_version"))
        if receipt_status == "current" and receipt is not None
        else None
    )
    install_integrity = _integrity_status(install_integrity_status)

    source_version: str | None = None
    release_status = "legacy_receipt" if receipt_status == "legacy" else "invalid"
    source_status = "unavailable"
    source: Path | None = None
    if isinstance(source_path, str) and source_path.strip():
        try:
            candidate = Path(source_path).expanduser().resolve()
        except (OSError, RuntimeError):
            candidate = None
        if candidate is not None and _looks_like_source_kit(candidate):
            source = candidate

    if source is None:
        if receipt_status == "current":
            release_status = "source_unavailable"
    else:
        try:
            source_version = read_source_version(source)
            current_source_hash = source_sha256(source)
        except (OSError, VersionContractError):
            release_status = "invalid"
            source_status = "invalid"
        else:
            installed_hash = receipt.get("source_sha256") if receipt else None
            source_status = (
                "unchanged"
                if isinstance(installed_hash, str) and installed_hash == current_source_hash
                else "modified"
            )
            if receipt_status == "current" and installed_version is not None:
                installed_key = parse_version(installed_version)
                source_key = parse_version(source_version)
                if installed_key < source_key:
                    release_status = "upgrade_available"
                elif installed_key > source_key:
                    release_status = "newer_than_source"
                else:
                    release_status = "up_to_date"

    return {
        "installed_version": installed_version,
        "source_version": source_version,
        "receipt_status": receipt_status,
        "release_status": release_status,
        "source_status": source_status,
        "install_integrity": install_integrity,
        "overall_status": _overall_status(
            receipt_status=receipt_status,
            release_status=release_status,
            source_status=source_status,
            install_integrity=install_integrity,
        ),
    }
