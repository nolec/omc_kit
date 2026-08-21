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
import omc_quality_gate as _quality_gate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_source_kit(source: Path, *, marker: str = "v1") -> str:
    (source / "templates").mkdir(parents=True, exist_ok=True)
    (source / "scripts").mkdir(parents=True, exist_ok=True)
    (source / "prompts").mkdir(parents=True, exist_ok=True)
    (source / "scripts" / "install.py").write_text("# installer\n", encoding="utf-8")
    (source / "prompts" / "team.json").write_text("{}\n", encoding="utf-8")
    (source / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (source / "templates" / "marker.txt").write_text(marker + "\n", encoding="utf-8")
    return _audit._source_sha256(source)


def _write_verified_install(target: Path) -> None:
    source = target.parent / "kit"
    source_sha256 = _write_source_kit(source)
    managed = target / "scripts" / "omc.py"
    managed.parent.mkdir(parents=True)
    managed.write_text("# installed\n", encoding="utf-8")
    metadata = target / ".omc"
    metadata.mkdir(parents=True)
    (metadata / "install-source.json").write_text(
        json.dumps(
            {
                "source_kind": "external",
                "source_path": str(source),
                "source_sha256": source_sha256,
            }
        ),
        encoding="utf-8",
    )
    (metadata / "install-receipt.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha256": source_sha256,
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
            self.assertEqual(result["version_readiness"]["receipt_status"], "legacy")
            self.assertEqual(result["version_readiness"]["overall_status"], "legacy_receipt")

    def test_verify_target_reports_v2_version_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)
            receipt_path = target / ".omc" / "install-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt.update(
                schema_version=2,
                omc_version="0.1.0",
                source_revision=None,
                installed_at="2026-08-01T00:00:00+00:00",
                updated_at="2026-08-21T00:00:00+00:00",
            )
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertEqual(result["verification_status"], "ok")
            self.assertEqual(
                result["version_readiness"],
                {
                    "installed_version": "0.1.0",
                    "source_version": "0.1.0",
                    "receipt_status": "current",
                    "release_status": "up_to_date",
                    "source_status": "unchanged",
                    "install_integrity": "clean",
                    "overall_status": "up_to_date",
                },
            )

    def test_verify_target_rejects_v2_receipt_without_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)
            receipt_path = target / ".omc" / "install-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["schema_version"] = 2
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertEqual(result["verification_status"], "failed")
            self.assertIn("version:invalid-receipt", result["verification_errors"])

    def test_verify_target_reports_matching_source_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "project"
            source = root / "kit"
            source_hash = _write_source_kit(source)
            _write_verified_install(target)
            metadata_path = target / ".omc" / "install-source.json"
            receipt_path = target / ".omc" / "install-receipt.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            metadata.update(source_path=str(source), source_sha256=source_hash)
            receipt["source_sha256"] = source_hash
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertEqual(result["installed_integrity_status"], "ok")
            self.assertEqual(result["source_freshness_status"], "up_to_date")
            self.assertEqual(result["current_source_sha256"], source_hash)
            self.assertEqual(result["verification_status"], "ok")

    def test_verify_target_rejects_known_stale_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "project"
            source = root / "kit"
            installed_source_hash = _write_source_kit(source)
            _write_verified_install(target)
            metadata_path = target / ".omc" / "install-source.json"
            receipt_path = target / ".omc" / "install-receipt.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            metadata.update(source_path=str(source), source_sha256=installed_source_hash)
            receipt["source_sha256"] = installed_source_hash
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            (source / "templates" / "marker.txt").write_text("v2\n", encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertEqual(result["installed_integrity_status"], "ok")
            self.assertEqual(result["source_freshness_status"], "update_available")
            self.assertNotEqual(result["current_source_sha256"], installed_source_hash)
            self.assertEqual(result["verification_status"], "failed")
            self.assertEqual(result["verification_errors"], ["source:update-available"])

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
            managed = target / "scripts" / "omc.py"
            original_sha256_file = _audit._sha256_file

            def hash_or_raise(path: Path) -> str:
                if path.resolve() == managed.resolve():
                    raise OSError("unreadable")
                return original_sha256_file(path)

            with patch.object(_audit, "_sha256_file", side_effect=hash_or_raise):
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
            self.assertIn("installed_integrity_status: ok", proc.stdout)
            self.assertIn("source_freshness_status: up_to_date", proc.stdout)
            self.assertIn("quality_gate_readiness: missing", proc.stdout)
            self.assertIn("verification_status: ok", proc.stdout)

    def test_install_audit_reports_invalid_quality_gate_without_failing_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)
            config = target / ".omc" / "quality-gates.json"
            config.write_text("{}", encoding="utf-8")

            result = _audit.audit_target(target)

            self.assertEqual(result["quality_gate_readiness"], "invalid")
            self.assertEqual(result["verification_status"], "ok")

    def test_omc_verify_install_command_rejects_unknown_source_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            _write_verified_install(target)
            metadata_path = target / ".omc" / "install-source.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["source_path"] = str(Path(tmp) / "missing-kit")
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

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
            self.assertIn("installed_integrity_status: ok", proc.stdout)
            self.assertIn("source_freshness_status: unknown", proc.stdout)
            self.assertIn("source:freshness-unknown", proc.stdout)
            self.assertIn("verification_status: failed", proc.stdout)

    def test_omc_setup_runs_strict_post_install_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            target.mkdir()
            evidence = target / "project-manifest"
            evidence.write_text("project-owned gate\n", encoding="utf-8")
            config_path = target / ".omc" / "quality-gates.json"
            config_path.parent.mkdir(parents=True)
            config = {
                "schema_version": "omc-quality-gates/v1",
                "base_ref": "HEAD~1",
                "evidence": [
                    {"path": "project-manifest", "sha256": _quality_gate.file_sha256(evidence)}
                ],
                "gates": [
                    {
                        "id": "test",
                        "purpose": "test",
                        "argv": ["true", "{changed_files}"],
                        "scope": "changed",
                        "required": True,
                        "timeout_sec": 30,
                    }
                ],
            }
            original_config = json.dumps(config, sort_keys=True) + "\n"
            config_path.write_text(original_config, encoding="utf-8")
            _quality_gate.approve(
                target,
                expected_config_sha256=_quality_gate.canonical_file_sha256(config_path),
            )

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
            self.assertIn("quality_gate_readiness: ready", proc.stdout)
            self.assertEqual(config_path.read_text(encoding="utf-8"), original_config)
            self.assertIn('"scan_strategy": "bounded_manifest"', proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
