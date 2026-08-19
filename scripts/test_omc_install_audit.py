#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import omc_install_audit as _audit


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_verified_install(target: Path) -> None:
    managed = target / "scripts" / "omc.py"
    managed.parent.mkdir(parents=True)
    managed.write_text("# installed\n", encoding="utf-8")
    metadata = target / ".omc"
    metadata.mkdir(parents=True)
    (metadata / "install-source.json").write_text(
        json.dumps(
            {
                "source_kind": "external",
                "source_path": "/kit",
                "source_sha256": "source-digest",
            }
        ),
        encoding="utf-8",
    )
    (metadata / "install-receipt.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha256": "source-digest",
                "target": str(target.resolve()),
                "entries": {
                    "scripts/omc.py": {
                        "policy": "managed_exact",
                        "status": "updated",
                        "target_sha256": _sha256(managed),
                    }
                },
            }
        ),
        encoding="utf-8",
    )


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

    def test_verify_target_accepts_matching_install_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)

            result = _audit.audit_target(target)

            self.assertEqual(result["verification_status"], "ok")
            self.assertEqual(result["verification_errors"], [])

    def test_verify_target_rejects_managed_file_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)
            (target / "scripts" / "omc.py").write_text("# drifted\n", encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertEqual(result["verification_status"], "failed")
            self.assertEqual(result["verification_errors"], ["drift:scripts/omc.py"])

    def test_verify_target_reports_unreadable_managed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)

            with patch.object(_audit, "_sha256_file", side_effect=OSError("unreadable")):
                result = _audit.audit_target(target)

            self.assertEqual(result["verification_status"], "failed")
            self.assertEqual(result["verification_errors"], ["unreadable:scripts/omc.py"])

    def test_verify_target_rejects_managed_file_through_parent_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "project"
            _write_verified_install(target)
            managed = target / "scripts" / "omc.py"
            managed.unlink()
            managed.parent.rmdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "omc.py").write_text("# installed\n", encoding="utf-8")
            (target / "scripts").symlink_to(outside, target_is_directory=True)

            result = _audit.audit_target(target)

            self.assertEqual(result["verification_status"], "failed")
            self.assertEqual(
                result["verification_errors"],
                ["outside-target:scripts/omc.py"],
            )

    def test_verify_target_rejects_source_mismatch_and_blocked_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)
            receipt_path = target / ".omc" / "install-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["source_sha256"] = "different-source"
            receipt["entries"]["scripts/omc.py"]["status"] = "blocked"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertEqual(result["verification_status"], "failed")
            self.assertEqual(
                result["verification_errors"],
                ["receipt:source-mismatch", "manifest-policy-error:scripts/omc.py"],
            )
            self.assertEqual(
                result["verification_issue_counts"],
                {"manifest_policy_error": 1, "receipt_error": 1},
            )

    def test_verify_target_reports_preserved_local_as_notice_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)
            receipt_path = target / ".omc" / "install-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["entries"]["scripts/project_task.py"] = {
                "policy": "preserve",
                "status": "preserved",
                "verification_mode": "preserved_existing",
                "source_sha256": "",
                "target_sha256": "local",
            }
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertEqual(result["verification_status"], "ok")
            self.assertEqual(
                result["verification_notices"],
                ["preserved-local:scripts/project_task.py"],
            )
            self.assertEqual(result["verification_issue_counts"], {})

    def test_omc_verify_install_command_returns_nonzero_for_missing_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            target.mkdir()

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent / "omc.py"),
                    "verify-install",
                    "--target",
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("verification_status: failed", proc.stdout)

    def test_omc_verify_install_command_accepts_matching_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent / "omc.py"),
                    "verify-install",
                    "--target",
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("verification_status: ok", proc.stdout)

    def test_omc_setup_runs_strict_post_install_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            target.mkdir()

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent / "omc.py"),
                    "setup",
                    "--target",
                    str(target),
                    "--force",
                    "--skip-session-start",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("verification_status: ok", proc.stdout)
            self.assertIn('"scan_strategy": "bounded_manifest"', proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
