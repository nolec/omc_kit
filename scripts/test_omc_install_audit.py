#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import omc_install_audit as _audit


class TestInstallAudit(unittest.TestCase):
    def test_audit_target_reports_legacy_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "project"
            (target / "omc_kit" / "templates").mkdir(parents=True)
            metadata = target / ".omc" / "install-source.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                json.dumps(
                    {
                        "source_kind": "external",
                        "source_path": "/tmp/omc_kit",
                    }
                ),
                encoding="utf-8",
            )

            result = _audit.audit_target(target)

            self.assertEqual(result["target"], str(target.resolve()))
            self.assertTrue(result["has_legacy_embedded_omc_kit"])
            self.assertTrue(result["has_install_source"])
            self.assertEqual(result["source_kind"], "external")
            self.assertEqual(result["source_path"], "/tmp/omc_kit")
            self.assertEqual(result["status"], "warn")

    def test_audit_target_reports_ok_when_metadata_exists_without_legacy_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "project"
            metadata = target / ".omc" / "install-source.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                json.dumps(
                    {
                        "source_kind": "external",
                        "source_path": "/work/omc_kit",
                    }
                ),
                encoding="utf-8",
            )

            result = _audit.audit_target(target)

            self.assertFalse(result["has_legacy_embedded_omc_kit"])
            self.assertTrue(result["has_install_source"])
            self.assertEqual(result["status"], "ok")

    def test_audit_target_reports_install_receipt_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            metadata = target / ".omc"
            metadata.mkdir(parents=True)
            (metadata / "install-source.json").write_text(
                json.dumps({"source_kind": "external", "source_path": "/kit", "source_sha256": "src"}),
                encoding="utf-8",
            )
            (metadata / "install-receipt.json").write_text(
                json.dumps({"source_sha256": "src", "entries": {
                    "scripts/omc.py": {"status": "updated"},
                    "AGENTS.md": {"status": "preserved"},
                }}),
                encoding="utf-8",
            )

            result = _audit.audit_target(target)

            self.assertTrue(result["has_install_receipt"])
            self.assertEqual(result["install_source_sha256"], "src")
            self.assertEqual(result["install_entry_counts"], {"updated": 1, "preserved": 1})

    def test_audit_target_reports_invalid_receipt_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            receipt_dir = target / ".omc"
            receipt_dir.mkdir(parents=True)
            (receipt_dir / "install-receipt.json").write_text("[]", encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertFalse(result["has_install_receipt"])
            self.assertEqual(result["receipt_error"], "invalid-json-shape")

    def test_cli_json_outputs_all_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a"
            second = root / "b"
            (first / ".omc").mkdir(parents=True)
            (first / ".omc" / "install-source.json").write_text(
                json.dumps({"source_kind": "external", "source_path": "/kit"}),
                encoding="utf-8",
            )
            second.mkdir()

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent / "omc_install_audit.py"),
                    "--json",
                    str(first),
                    str(second),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["target"], str(first.resolve()))
            self.assertEqual(data[1]["target"], str(second.resolve()))
            self.assertEqual(data[1]["status"], "missing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
