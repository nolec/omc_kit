#!/usr/bin/env python3
"""Prepare and apply an externally signed OMC Plan gold approval."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from omc_plan_benchmark import gold_signoff_payload, sign_off_gold_document


_PREPARED_FIELDS = {
    "schema_version",
    "signer",
    "signer_public_key",
    "approved_at",
    "evidence",
    "corpus_sha256",
    "gold_sha256",
    "payload_base64",
    "payload_sha256",
}


def prepare_gold_signoff(
    gold_document: dict[str, Any],
    *,
    signer: str,
    approved_at: str,
    evidence: dict[str, Any],
    signer_public_key: str,
) -> dict[str, Any]:
    """Build the canonical payload without receiving the signer's private key."""
    payload = gold_signoff_payload(
        gold_document,
        signer=signer,
        approved_at=approved_at,
        evidence=evidence,
        signer_public_key=signer_public_key,
    )
    return {
        "schema_version": 1,
        "signer": signer.strip(),
        "signer_public_key": signer_public_key,
        "approved_at": approved_at,
        "evidence": evidence,
        "corpus_sha256": gold_document.get("corpus_sha256"),
        "gold_sha256": gold_document.get("gold_sha256"),
        "payload_base64": base64.b64encode(payload).decode("ascii"),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def apply_gold_signoff(
    gold_document: dict[str, Any],
    *,
    prepared: dict[str, Any],
    signature: str,
    trusted_signer_public_keys: set[str],
) -> dict[str, Any]:
    """Verify one prepared payload and embed its trusted external signature."""
    if not isinstance(prepared, dict) or set(prepared) != _PREPARED_FIELDS:
        raise ValueError("prepared sign-off fields are invalid")
    if prepared.get("schema_version") != 1:
        raise ValueError("prepared sign-off schema_version must be 1")
    if prepared.get("corpus_sha256") != gold_document.get("corpus_sha256"):
        raise ValueError("prepared corpus hash mismatch")
    if prepared.get("gold_sha256") != gold_document.get("gold_sha256"):
        raise ValueError("prepared gold hash mismatch")

    payload = gold_signoff_payload(
        gold_document,
        signer=prepared["signer"],
        approved_at=prepared["approved_at"],
        evidence=prepared["evidence"],
        signer_public_key=prepared["signer_public_key"],
    )
    if base64.b64encode(payload).decode("ascii") != prepared["payload_base64"]:
        raise ValueError("prepared payload mismatch")
    if hashlib.sha256(payload).hexdigest() != prepared["payload_sha256"]:
        raise ValueError("prepared payload hash mismatch")

    return sign_off_gold_document(
        gold_document,
        signer=prepared["signer"],
        approved_at=prepared["approved_at"],
        evidence=prepared["evidence"],
        signer_public_key=prepared["signer_public_key"],
        signature=signature,
        trusted_signer_public_keys=trusted_signer_public_keys,
    )


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json_atomic(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("gold")
    prepare.add_argument("evidence")
    prepare.add_argument("--signer", required=True)
    prepare.add_argument("--approved-at", required=True)
    prepare.add_argument("--signer-public-key", required=True)
    prepare.add_argument("--output", required=True)

    apply = subparsers.add_parser("apply")
    apply.add_argument("gold")
    apply.add_argument("prepared")
    apply.add_argument("signature")
    apply.add_argument("--trusted-signer-public-key", action="append", required=True)
    apply.add_argument("--output", required=True)

    args = parser.parse_args()
    gold = _load_json(args.gold)
    if args.command == "prepare":
        result = prepare_gold_signoff(
            gold,
            signer=args.signer,
            approved_at=args.approved_at,
            evidence=_load_json(args.evidence),
            signer_public_key=args.signer_public_key,
        )
    else:
        result = apply_gold_signoff(
            gold,
            prepared=_load_json(args.prepared),
            signature=Path(args.signature).read_text(encoding="utf-8").strip(),
            trusted_signer_public_keys=set(args.trusted_signer_public_key),
        )
    _write_json_atomic(args.output, result)
    print(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
