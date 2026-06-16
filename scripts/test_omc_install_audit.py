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
