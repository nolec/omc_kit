#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import install as _install
import omc_install_audit as _audit
from omc_source_hash import source_sha256


def _write_source_kit(source: Path) -> None:
    (source / "templates").mkdir(parents=True)
    (source / "scripts").mkdir(parents=True)
    (source / "prompts").mkdir(parents=True)
    (source / "scripts" / "install.py").write_text("# installer\n", encoding="utf-8")
    (source / "prompts" / "team.json").write_text("{}\n", encoding="utf-8")
    (source / "templates" / "marker.txt").write_text("v1\n", encoding="utf-8")


class TestSourceHash(unittest.TestCase):
    def test_consumers_share_source_hash_contract(self):
        self.assertIs(_install._source_sha256, source_sha256)
        self.assertIs(_audit._source_sha256, source_sha256)

    def test_runtime_artifacts_do_not_change_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "kit"
            _write_source_kit(source)
            expected = source_sha256(source)
            (source / "scripts" / "__pycache__").mkdir()
            (source / "scripts" / "__pycache__" / "install.cpython-311.pyc").write_bytes(b"cache")
            (source / ".pytest_cache").mkdir()
            (source / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")
            (source / ".DS_Store").write_bytes(b"finder")
            (source / ".coverage").write_bytes(b"coverage")
            (source / ".coverage.worker-1").write_bytes(b"parallel coverage")
            for cache_dir in ("htmlcov", ".mypy_cache", ".ruff_cache", ".tox", ".nox"):
                path = source / cache_dir
                path.mkdir()
                (path / "runtime-state").write_text("cache\n", encoding="utf-8")

            self.assertEqual(source_sha256(source), expected)

    def test_managed_source_change_updates_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "kit"
            _write_source_kit(source)
            before = source_sha256(source)

            (source / "templates" / "marker.txt").write_text("v2\n", encoding="utf-8")

            self.assertNotEqual(source_sha256(source), before)

    def test_non_deployed_document_does_not_change_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "kit"
            _write_source_kit(source)
            before = source_sha256(source)

            docs = source / "docs"
            docs.mkdir()
            (docs / "automatic_model_routing_roadmap.md").write_text(
                "roadmap update\n",
                encoding="utf-8",
            )

            self.assertEqual(source_sha256(source), before)

    def test_deployed_document_change_updates_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "kit"
            _write_source_kit(source)
            docs = source / "docs"
            docs.mkdir()
            deployed = docs / "omc_quality_gates.md"
            deployed.write_text("v1\n", encoding="utf-8")
            before = source_sha256(source)

            deployed.write_text("v2\n", encoding="utf-8")

            self.assertNotEqual(source_sha256(source), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
