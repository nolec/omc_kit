#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def _metadata_path(target: Path) -> Path:
    return target / ".omc" / "install-source.json"


def _receipt_path(target: Path) -> Path:
    return target / ".omc" / "install-receipt.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_target(target: Path) -> dict[str, object]:
    resolved = target.resolve()
    legacy_dir = resolved / "omc_kit" / "templates"
    metadata_path = _metadata_path(resolved)
    receipt_path = _receipt_path(resolved)
    has_metadata = metadata_path.exists()

    source_kind = None
    source_path = None
    metadata_source_sha256 = None
    metadata_error = None
    if has_metadata:
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                source_kind = data.get("source_kind")
                source_path = data.get("source_path")
                metadata_source_sha256 = data.get("source_sha256")
            else:
                metadata_error = "invalid-json-shape"
        except (OSError, json.JSONDecodeError):
            metadata_error = "invalid-json"

    has_receipt = receipt_path.exists()
    receipt_source_sha256 = None
    receipt_target = None
    receipt_schema_version = None
    receipt_entry_counts: dict[str, int] = {}
    receipt_entries: dict[str, object] = {}
    receipt_error = None
    if has_receipt:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or not isinstance(receipt.get("entries"), dict):
                has_receipt = False
                receipt_error = "invalid-json-shape"
            else:
                receipt_source_sha256 = receipt.get("source_sha256")
                receipt_target = receipt.get("target")
                receipt_schema_version = receipt.get("schema_version")
                receipt_entries = receipt["entries"]
                for entry in receipt["entries"].values():
                    if isinstance(entry, dict):
                        status = entry.get("status")
                        if isinstance(status, str):
                            receipt_entry_counts[status] = receipt_entry_counts.get(status, 0) + 1
        except (OSError, json.JSONDecodeError):
            has_receipt = False
            receipt_error = "invalid-json"

    status = "missing"
    if has_metadata and not legacy_dir.exists() and metadata_error is None:
        status = "ok"
    elif has_metadata or legacy_dir.exists():
        status = "warn"

    verification_errors: list[str] = []
    verification_notices: list[str] = []
    if status != "ok":
        verification_errors.append(f"audit:{status}")
    if not has_receipt:
        verification_errors.append("receipt:missing-or-invalid")
    elif receipt_schema_version != 1:
        verification_errors.append("receipt:unsupported-schema")
    elif receipt_target != str(resolved):
        verification_errors.append("receipt:target-mismatch")
    if (
        not isinstance(metadata_source_sha256, str)
        or not metadata_source_sha256
        or receipt_source_sha256 != metadata_source_sha256
    ):
        verification_errors.append("receipt:source-mismatch")
    if has_receipt and not receipt_entries:
        verification_errors.append("receipt:empty-entries")

    for rel, entry in sorted(receipt_entries.items()):
        rel_path = Path(rel)
        if (
            not isinstance(entry, dict)
            or rel_path.is_absolute()
            or ".." in rel_path.parts
        ):
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        policy = entry.get("policy")
        entry_status = entry.get("status")
        if policy not in {"managed_exact", "managed_generated", "preserve"}:
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        if policy == "preserve" and entry_status != "preserved":
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        if policy != "preserve" and entry_status == "blocked":
            source_hash = entry.get("source_sha256")
            target_hash = entry.get("target_sha256")
            if (
                not isinstance(source_hash, str)
                or not source_hash
                or source_hash == target_hash
            ):
                verification_errors.append(f"manifest-policy-error:{rel}")
            else:
                verification_errors.append(f"managed-drift:{rel}")
        elif policy != "preserve" and entry_status != "updated":
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        if policy == "preserve":
            verification_notices.append(f"preserved-local:{rel}")
            continue
        installed = resolved / rel_path
        try:
            resolved_installed = installed.resolve()
        except (OSError, RuntimeError):
            verification_errors.append(f"unreadable:{rel}")
            continue
        if not resolved_installed.is_relative_to(resolved):
            verification_errors.append(f"outside-target:{rel}")
            continue
        current = resolved
        has_symlink_component = False
        for part in rel_path.parts:
            current /= part
            if current.is_symlink():
                has_symlink_component = True
                break
        expected_hash = entry.get("target_sha256")
        if (
            not isinstance(expected_hash, str)
            or not expected_hash
            or not installed.is_file()
            or installed.is_symlink()
            or has_symlink_component
        ):
            verification_errors.append(f"drift:{rel}")
            continue
        try:
            actual_hash = _sha256_file(installed)
        except OSError:
            verification_errors.append(f"unreadable:{rel}")
            continue
        if actual_hash != expected_hash:
            verification_errors.append(f"drift:{rel}")

    issue_counts = Counter()
    for issue in verification_errors:
        prefix = issue.split(":", 1)[0]
        if prefix in {"receipt", "audit"}:
            issue_counts["receipt_error"] += 1
        elif prefix in {"entry-invalid", "manifest-policy-error"}:
            issue_counts["manifest_policy_error"] += 1
        else:
            issue_counts["managed_drift"] += 1

    return {
        "target": str(resolved),
        "has_legacy_embedded_omc_kit": legacy_dir.exists(),
        "has_install_source": has_metadata,
        "source_kind": source_kind,
        "source_path": source_path,
        "metadata_source_sha256": metadata_source_sha256,
        "metadata_error": metadata_error,
        "has_install_receipt": has_receipt,
        "install_source_sha256": receipt_source_sha256,
        "install_entry_counts": receipt_entry_counts,
        "receipt_error": receipt_error,
        "status": status,
        "verification_status": "ok" if not verification_errors else "failed",
        "verification_errors": verification_errors,
        "verification_notices": verification_notices,
        "verification_issue_counts": dict(sorted(issue_counts.items())),
    }


def _render_text(results: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for item in results:
        lines.append(f"== {item['target']} ==")
        lines.append(f"status: {item['status']}")
        lines.append(f"verification_status: {item['verification_status']}")
        if item["verification_errors"]:
            lines.append(f"verification_errors: {item['verification_errors']}")
        if item["verification_notices"]:
            lines.append(f"verification_notices: {item['verification_notices']}")
        if item["verification_issue_counts"]:
            lines.append(
                f"verification_issue_counts: {item['verification_issue_counts']}"
            )
        lines.append(f"legacy_embedded_omc_kit: {item['has_legacy_embedded_omc_kit']}")
        lines.append(f"install_source: {item['has_install_source']}")
        if item["source_kind"] is not None or item["source_path"] is not None:
            lines.append(f"source_kind: {item['source_kind']}")
            lines.append(f"source_path: {item['source_path']}")
        if item["metadata_error"] is not None:
            lines.append(f"metadata_error: {item['metadata_error']}")
        lines.append(f"install_receipt: {item['has_install_receipt']}")
        if item["receipt_error"] is not None:
            lines.append(f"receipt_error: {item['receipt_error']}")
        if item["install_entry_counts"]:
            lines.append(f"install_entry_counts: {item['install_entry_counts']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit OMC install-source metadata and legacy embedded omc_kit state.")
    ap.add_argument("targets", nargs="+", help="Project roots to inspect.")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero unless every target has a verified installation.",
    )
    args = ap.parse_args()

    results = [audit_target(Path(target)) for target in args.targets]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(_render_text(results), end="")
    if args.strict and any(item["verification_status"] != "ok" for item in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
