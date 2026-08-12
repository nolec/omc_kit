#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _metadata_path(target: Path) -> Path:
    return target / ".omc" / "install-source.json"


def _receipt_path(target: Path) -> Path:
    return target / ".omc" / "install-receipt.json"


def audit_target(target: Path) -> dict[str, object]:
    resolved = target.resolve()
    legacy_dir = resolved / "omc_kit" / "templates"
    metadata_path = _metadata_path(resolved)
    receipt_path = _receipt_path(resolved)
    has_metadata = metadata_path.exists()

    source_kind = None
    source_path = None
    metadata_error = None
    if has_metadata:
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                source_kind = data.get("source_kind")
                source_path = data.get("source_path")
            else:
                metadata_error = "invalid-json-shape"
        except (OSError, json.JSONDecodeError):
            metadata_error = "invalid-json"

    has_receipt = receipt_path.exists()
    receipt_source_sha256 = None
    receipt_entry_counts: dict[str, int] = {}
    receipt_error = None
    if has_receipt:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or not isinstance(receipt.get("entries"), dict):
                has_receipt = False
                receipt_error = "invalid-json-shape"
            else:
                receipt_source_sha256 = receipt.get("source_sha256")
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

    return {
        "target": str(resolved),
        "has_legacy_embedded_omc_kit": legacy_dir.exists(),
        "has_install_source": has_metadata,
        "source_kind": source_kind,
        "source_path": source_path,
        "metadata_error": metadata_error,
        "has_install_receipt": has_receipt,
        "install_source_sha256": receipt_source_sha256,
        "install_entry_counts": receipt_entry_counts,
        "receipt_error": receipt_error,
        "status": status,
    }


def _render_text(results: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for item in results:
        lines.append(f"== {item['target']} ==")
        lines.append(f"status: {item['status']}")
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
    args = ap.parse_args()

    results = [audit_target(Path(target)) for target in args.targets]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(_render_text(results), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
