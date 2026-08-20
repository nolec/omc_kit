#!/usr/bin/env python3
"""install.py 핵심 함수 테스트 — stdlib only (pytest 불필요)"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import install as _install
import omc_doctor as _doctor
import omc_hooks as _hooks
import omc_hook_contract as _hook_contract


class TestCopy(unittest.TestCase):
    def test_copy_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.txt"
            dst = Path(tmp) / "sub" / "dst.txt"
            src.write_text("hello", encoding="utf-8")
            _install._copy(src, dst, force=False)
            self.assertEqual(dst.read_text(encoding="utf-8"), "hello")

    def test_copy_skips_existing_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.txt"
            dst = Path(tmp) / "dst.txt"
            src.write_text("new", encoding="utf-8")
            dst.write_text("old", encoding="utf-8")
            _install._copy(src, dst, force=False)
            self.assertEqual(dst.read_text(encoding="utf-8"), "old")

    def test_copy_overwrites_with_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.txt"
            dst = Path(tmp) / "dst.txt"
            src.write_text("new", encoding="utf-8")
            dst.write_text("old", encoding="utf-8")
            _install._copy(src, dst, force=True)
            self.assertEqual(dst.read_text(encoding="utf-8"), "new")

    def test_copy_raises_if_src_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "missing.txt"
            dst = Path(tmp) / "dst.txt"
            with self.assertRaises(FileNotFoundError):
                _install._copy(src, dst, force=False)


class TestInstallManifest(unittest.TestCase):
    def test_auto_update_status_is_up_to_date_for_matching_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            (source / "templates").mkdir(parents=True)
            target.mkdir()
            (source / "templates" / "skill.md").write_text("v1\n", encoding="utf-8")
            source_hash = _install._source_sha256(source)
            _install._write_install_source_metadata(target, source)
            _install._write_install_receipt(
                target,
                source_sha256=source_hash,
                entries={},
            )

            result = _install._classify_auto_update(source, target)

            self.assertEqual(result["status"], "up_to_date")
            self.assertEqual(result["source_sha256"], source_hash)

    def test_auto_update_status_detects_local_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            source_file = source / "managed.md"
            target_file = target / "managed.md"
            source_file.write_text("new\n", encoding="utf-8")
            target_file.write_text("installed\n", encoding="utf-8")
            _install._write_install_source_metadata(target, source)
            _install._write_install_receipt(
                target,
                source_sha256="old-source",
                entries={
                    "managed.md": {
                        "policy": "managed_exact",
                        "source_sha256": "old-file",
                        "target_sha256": _install._sha256_file(target_file),
                    }
                },
            )
            target_file.write_text("user edit\n", encoding="utf-8")

            result = _install._classify_auto_update(source, target)

            self.assertEqual(result["status"], "local_conflict")
            self.assertEqual(result["conflicts"], ["managed.md"])

    def test_auto_update_status_reports_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "missing-source"
            target = root / "target"
            target.mkdir()
            _install._write_install_source_metadata(target, source)

            result = _install._classify_auto_update(source, target)

            self.assertEqual(result["status"], "source_missing")

    def test_auto_update_status_blocks_unrecorded_managed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            (source / "scripts").mkdir(parents=True)
            (target / "scripts").mkdir(parents=True)
            (source / "scripts" / "omc_new.py").write_text("new\n", encoding="utf-8")
            (target / "scripts" / "omc_new.py").write_text("local\n", encoding="utf-8")
            _install._write_install_source_metadata(target, source)
            _install._write_install_receipt(target, source_sha256="old-source", entries={})

            result = _install._classify_auto_update(source, target)

            self.assertEqual(result["status"], "local_conflict")
            self.assertEqual(result["conflicts"], ["scripts/omc_new.py"])

    def test_source_hash_ignores_runtime_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "scripts" / "omc.py").write_text("source\n", encoding="utf-8")
            first = _install._source_sha256(source)
            (source / ".omc").mkdir()
            (source / ".omc" / "state.json").write_text("runtime-1\n", encoding="utf-8")
            second = _install._source_sha256(source)
            (source / ".omc" / "state.json").write_text("runtime-2\n", encoding="utf-8")
            third = _install._source_sha256(source)

            self.assertEqual(first, second)
            self.assertEqual(second, third)

    def test_manifest_policy_controls_force_update_scope(self):
        self.assertTrue(_install._should_update_install_entry("scripts/omc.py", force=True))
        self.assertTrue(_install._should_update_install_entry("docs/omc_workflow.md", force=True))
        self.assertTrue(_install._should_update_install_entry("docs/quickstart_kr.md", force=True))
        self.assertTrue(_install._should_update_install_entry(".agent-hooks/omc.sh", force=True))
        self.assertTrue(_install._should_update_install_entry(".agent/skills/omc-task/SKILL.md", force=True))
        self.assertFalse(_install._should_update_install_entry("src/app.py", force=True))

    def test_work_class_lock_cli_is_deployed_without_external_custody(self):
        names = _install._deployed_script_names(Path(__file__).parent.parent)

        self.assertIn("omc_work_class_lock.py", names)
        self.assertNotIn("work-class-lock.key", names)
        self.assertNotIn("work-class-lock.json", names)

    def test_manifest_registers_source_managed_files_without_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            (source / "scripts").mkdir(parents=True)
            (target / "scripts").mkdir(parents=True)
            (source / "scripts" / "omc.py").write_text("new\n", encoding="utf-8")
            (target / "scripts" / "omc.py").write_text("old\n", encoding="utf-8")
            (target / "src").mkdir()
            (target / "src" / "app.py").write_text("user\n", encoding="utf-8")

            manifest = _install._build_install_manifest(source, target)

            self.assertEqual(manifest["scripts/omc.py"]["policy"], "managed_exact")
            self.assertNotIn("src/app.py", manifest)
            self.assertNotEqual(manifest["scripts/omc.py"]["source_sha256"], "")

    def test_manifest_tracks_stale_deployment_root_from_previous_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            (source / "scripts").mkdir(parents=True)
            (target / "docs").mkdir(parents=True)
            (target / ".omc").mkdir()
            (source / "scripts" / "omc.py").write_text("source\n", encoding="utf-8")
            workflow = target / "docs" / "omc_workflow.md"
            workflow.write_text("custom\n", encoding="utf-8")
            (target / ".omc" / "install-receipt.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "entries": {
                            "docs/omc_workflow.md": {
                                "policy": "managed_exact",
                                "source_sha256": "old-source",
                                "target_sha256": _install._sha256_file(workflow),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            manifest = _install._build_install_manifest(source, target)

            self.assertEqual(manifest["docs/omc_workflow.md"]["policy"], "managed_exact")
            self.assertTrue(manifest["docs/omc_workflow.md"]["previously_managed"])

    def test_manifest_does_not_scan_or_register_unmanaged_target_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            (source / "scripts").mkdir(parents=True)
            (target / "scripts").mkdir(parents=True)
            (target / "src").mkdir()
            (source / "scripts" / "omc.py").write_text("new\n", encoding="utf-8")
            (target / "scripts" / "project_task.py").write_text(
                "project\n", encoding="utf-8"
            )
            (target / "src" / "app.py").write_text("user\n", encoding="utf-8")

            with patch.object(
                Path,
                "rglob",
                side_effect=AssertionError("target-wide scan is forbidden"),
            ):
                manifest = _install._build_install_manifest(source, target)

            self.assertIn("scripts/omc.py", manifest)
            self.assertNotIn("scripts/project_task.py", manifest)
            self.assertNotIn("src/app.py", manifest)

    def test_manifest_carries_only_previous_managed_entries_for_stale_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            (source / "scripts").mkdir(parents=True)
            (target / ".omc").mkdir(parents=True)
            (target / "docs").mkdir()
            (source / "scripts" / "omc.py").write_text("new\n", encoding="utf-8")
            stale = target / "docs" / "omc_removed.md"
            stale.write_text("old managed\n", encoding="utf-8")
            (target / "docs" / "product.md").write_text("product\n", encoding="utf-8")
            (target / ".omc" / "install-receipt.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "entries": {
                        "docs/omc_removed.md": {
                            "policy": "managed_exact",
                            "source_sha256": "old-source",
                            "target_sha256": _install._sha256_file(stale),
                        },
                        "docs/product.md": {
                            "policy": "preserve",
                            "source_sha256": "",
                            "target_sha256": "ignored",
                        },
                    },
                }),
                encoding="utf-8",
            )

            manifest = _install._build_install_manifest(source, target)

            self.assertIn("docs/omc_removed.md", manifest)
            self.assertNotIn("docs/product.md", manifest)
            self.assertTrue(manifest["docs/omc_removed.md"]["previously_managed"])

    def test_manifest_drops_legacy_broad_scope_receipt_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            (source / "scripts").mkdir(parents=True)
            (target / ".omc").mkdir(parents=True)
            (source / "scripts" / "omc.py").write_text("new\n", encoding="utf-8")
            entries = {
                "scripts/project.py": {"policy": "managed_exact"},
                "scripts/__pycache__/omc.pyc": {"policy": "managed_exact"},
                ".agent/.DS_Store": {"policy": "managed_exact"},
                "docs/omc_removed.md": {"policy": "managed_exact"},
                "prompts/ROLE_RETIRED_ASSISTANT.md": {"policy": "managed_exact"},
            }
            (target / ".omc" / "install-receipt.json").write_text(
                json.dumps({"schema_version": 1, "entries": entries}),
                encoding="utf-8",
            )

            manifest = _install._build_install_manifest(source, target)

            self.assertNotIn("scripts/project.py", manifest)
            self.assertNotIn("scripts/__pycache__/omc.pyc", manifest)
            self.assertNotIn(".agent/.DS_Store", manifest)
            self.assertIn("docs/omc_removed.md", manifest)
            self.assertIn("prompts/ROLE_RETIRED_ASSISTANT.md", manifest)

    def test_force_prunes_only_bounded_stale_managed_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            stale = target / ".cursor" / "rules" / "omc-retired.mdc"
            stale.parent.mkdir(parents=True)
            stale.write_text("retired\n", encoding="utf-8")
            manifest = {
                ".cursor/rules/omc-retired.mdc": {
                    "policy": "managed_exact",
                    "previously_managed": True,
                    "registered_current_install": False,
                },
                "scripts/project.py": {
                    "policy": "managed_exact",
                    "previously_managed": True,
                    "registered_current_install": False,
                },
            }

            removed = _install._prune_stale_managed_outputs(
                target, manifest, force=True
            )

            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertNotIn(".cursor/rules/omc-retired.mdc", manifest)
            self.assertIn("scripts/project.py", manifest)

    def test_non_force_keeps_stale_managed_output_for_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            stale = target / "docs" / "omc_retired.md"
            stale.parent.mkdir(parents=True)
            stale.write_text("retired\n", encoding="utf-8")
            manifest = {
                "docs/omc_retired.md": {
                    "policy": "managed_exact",
                    "previously_managed": True,
                    "registered_current_install": False,
                }
            }

            removed = _install._prune_stale_managed_outputs(
                target, manifest, force=False
            )

            self.assertEqual(removed, 0)
            self.assertTrue(stale.exists())
            self.assertIn("docs/omc_retired.md", manifest)

    def test_manifest_rejects_damaged_previous_receipt_without_target_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            (source / "scripts").mkdir(parents=True)
            (target / ".omc").mkdir(parents=True)
            (source / "scripts" / "omc.py").write_text("new\n", encoding="utf-8")
            (target / ".omc" / "install-receipt.json").write_text(
                "{damaged", encoding="utf-8"
            )

            with patch.object(
                Path,
                "rglob",
                side_effect=AssertionError("target-wide scan is forbidden"),
            ):
                with self.assertRaisesRegex(
                    ValueError, "manifest_policy_error:invalid-install-receipt"
                ):
                    _install._build_install_manifest(source, target)

    def test_install_cli_reports_damaged_receipt_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            (target / ".omc").mkdir(parents=True)
            (target / ".omc" / "install-receipt.json").write_text(
                "{damaged", encoding="utf-8"
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent / "install.py"),
                    "--target",
                    str(target),
                    "--force",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 2)
            self.assertIn(
                "manifest_policy_error:invalid-install-receipt", proc.stderr
            )
            self.assertNotIn("Traceback", proc.stderr)

    def test_copy_respects_manifest_preserve_policy_even_with_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source.txt"
            dst = root / "src" / "app.py"
            src.write_text("omc\n", encoding="utf-8")
            dst.parent.mkdir(parents=True)
            dst.write_text("user\n", encoding="utf-8")
            previous_root = _install._INSTALL_POLICY_ROOT
            previous_manifest = _install._INSTALL_POLICY_MANIFEST
            try:
                _install._INSTALL_POLICY_ROOT = root
                _install._INSTALL_POLICY_MANIFEST = {
                    "src/app.py": {"policy": "preserve"},
                }
                _install._copy(src, dst, force=True)
            finally:
                _install._INSTALL_POLICY_ROOT = previous_root
                _install._INSTALL_POLICY_MANIFEST = previous_manifest

            self.assertEqual(dst.read_text(encoding="utf-8"), "user\n")

    def test_copy_registers_managed_source_hash_for_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "templates" / "omc.md"
            dst = root / "docs" / "omc.md"
            src.parent.mkdir(parents=True)
            src.write_text("managed\n", encoding="utf-8")
            _install._INSTALL_POLICY_ROOT = root
            _install._INSTALL_POLICY_MANIFEST = {}
            try:
                _install._copy(src, dst, force=True)
                entry = _install._INSTALL_POLICY_MANIFEST["docs/omc.md"]
            finally:
                _install._INSTALL_POLICY_ROOT = None
                _install._INSTALL_POLICY_MANIFEST = {}

            self.assertEqual(entry["policy"], "managed_exact")
            self.assertEqual(entry["source_sha256"], _install._sha256_file(src))

    def test_finalize_manifest_marks_generated_entries_with_target_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "generated.json"
            target.write_text("generated\n", encoding="utf-8")
            manifest = {
                "generated.json": {
                    "policy": "managed_exact",
                    "source_sha256": "",
                    "target_sha256": "",
                    "registered_generated": True,
                }
            }

            finalized = _install._finalize_install_manifest(root, manifest)

            self.assertEqual(finalized["generated.json"]["policy"], "managed_generated")
            self.assertEqual(finalized["generated.json"]["source_sha256"], _install._sha256_file(target))

    def test_finalize_manifest_refreshes_existing_generated_entry_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "AGENTS.md"
            target.write_text("new generated content\n", encoding="utf-8")
            manifest = {
                "AGENTS.md": {
                    "policy": "managed_generated",
                    "source_sha256": "previous-generated-hash",
                    "target_sha256": "previous-generated-hash",
                    "registered_generated": True,
                }
            }

            finalized = _install._finalize_install_manifest(root, manifest)

            expected_hash = _install._sha256_file(target)
            self.assertEqual(finalized["AGENTS.md"]["policy"], "managed_generated")
            self.assertEqual(finalized["AGENTS.md"]["source_sha256"], expected_hash)
            self.assertEqual(finalized["AGENTS.md"]["target_sha256"], expected_hash)

    def test_finalize_manifest_preserves_unregistered_exact_entry(self):
        manifest = {
            "docs/not-copied.md": {
                "policy": "managed_exact",
                "source_sha256": "",
                "target_sha256": "",
            }
        }

        finalized = _install._finalize_install_manifest(Path("/tmp"), manifest)

        self.assertEqual(finalized["docs/not-copied.md"]["policy"], "managed_exact")

    def test_receipt_uses_install_manifest_entries_not_rebuilt_target_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            target = root / "docs" / "omc.md"
            source.write_text("source\n", encoding="utf-8")
            target.parent.mkdir(parents=True)
            target.write_text("source\n", encoding="utf-8")
            manifest = {
                "docs/omc.md": {
                    "policy": "managed_exact",
                    "source_sha256": _install._sha256_file(source),
                    "target_sha256": _install._sha256_file(target),
                }
            }

            finalized = _install._finalize_install_manifest(root, manifest)

            self.assertEqual(finalized["docs/omc.md"]["policy"], "managed_exact")
            self.assertNotEqual(finalized["docs/omc.md"]["source_sha256"], "")

    def test_receipt_distinguishes_generated_verification_mode(self):
        generated = _install._receipt_entry(
            policy="managed_generated",
            source_sha256="same",
            target_sha256="same",
        )
        exact = _install._receipt_entry(
            policy="managed_exact",
            source_sha256="same",
            target_sha256="same",
        )

        self.assertEqual(generated["verification_mode"], "generated_output")
        self.assertEqual(exact["verification_mode"], "source_exact")

    def test_receipt_attributes_setup_changes(self):
        created = _install._receipt_entry(
            policy="managed_exact",
            source_sha256="new",
            target_sha256="new",
            previous_target_sha256="",
        )
        changed = _install._receipt_entry(
            policy="managed_exact",
            source_sha256="new",
            target_sha256="new",
            previous_target_sha256="old",
        )
        unchanged = _install._receipt_entry(
            policy="managed_exact",
            source_sha256="same",
            target_sha256="same",
            previous_target_sha256="same",
        )

        self.assertEqual(created["change"], "created")
        self.assertEqual(changed["change"], "changed_by_setup")
        self.assertEqual(unchanged["change"], "unchanged")

    def test_install_summary_counts_changes_and_reports_bounded_scan(self):
        summary = _install._install_summary(
            {
                "a": {"change": "created"},
                "b": {"change": "changed_by_setup"},
                "c": {"change": "unchanged"},
                "d": {"change": "preserved"},
            },
            stage_timings_ms={"manifest": 3, "apply": 8, "receipt": 2},
        )

        self.assertEqual(summary["scan_strategy"], "bounded_manifest")
        self.assertEqual(
            summary["change_counts"],
            {
                "changed_by_setup": 1,
                "created": 1,
                "preserved": 1,
                "unchanged": 1,
            },
        )
        self.assertEqual(summary["stage_timings_ms"]["apply"], 8)

    def test_reset_install_policy_context_clears_global_state(self):
        _install._INSTALL_POLICY_ROOT = Path("/tmp/omc-target")
        _install._INSTALL_POLICY_MANIFEST = {"scripts/omc.py": {"policy": "managed_exact"}}

        _install._reset_install_policy_context()

        self.assertIsNone(_install._INSTALL_POLICY_ROOT)
        self.assertEqual(_install._INSTALL_POLICY_MANIFEST, {})

    def test_non_force_existing_managed_file_is_preserved_in_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source.txt"
            dst = root / "scripts" / "omc.py"
            src.write_text("new\n", encoding="utf-8")
            dst.parent.mkdir(parents=True)
            dst.write_text("old\n", encoding="utf-8")
            _install._INSTALL_POLICY_ROOT = root
            _install._INSTALL_POLICY_MANIFEST = {}
            try:
                _install._copy(src, dst, force=False)
                entry = _install._INSTALL_POLICY_MANIFEST["scripts/omc.py"]
            finally:
                _install._reset_install_policy_context()

            self.assertEqual(entry["policy"], "preserve")
            self.assertEqual(entry["target_sha256"], _install._sha256_file(dst))

    def test_non_force_preserve_updates_prebuilt_manifest_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source.txt"
            dst = root / "scripts" / "omc.py"
            src.write_text("new\n", encoding="utf-8")
            dst.parent.mkdir(parents=True)
            dst.write_text("old\n", encoding="utf-8")
            _install._INSTALL_POLICY_ROOT = root
            _install._INSTALL_POLICY_MANIFEST = {
                "scripts/omc.py": {
                    "policy": "managed_exact",
                    "source_sha256": "old-source",
                    "target_sha256": _install._sha256_file(dst),
                    "previously_managed": True,
                }
            }
            try:
                _install._copy(src, dst, force=False)
                entry = _install._INSTALL_POLICY_MANIFEST["scripts/omc.py"]
            finally:
                _install._reset_install_policy_context()

            self.assertEqual(entry["policy"], "preserve")
            self.assertEqual(entry["source_sha256"], "")

    def test_direct_generated_writer_registers_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dst = root / ".gemini" / "settings.json"
            _install._INSTALL_POLICY_ROOT = root
            _install._INSTALL_POLICY_MANIFEST = {}
            try:
                dst.parent.mkdir(parents=True)
                dst.write_text("{}\n", encoding="utf-8")
                _install._register_generated_output(dst)
                entry = _install._INSTALL_POLICY_MANIFEST[".gemini/settings.json"]
            finally:
                _install._reset_install_policy_context()

            self.assertTrue(entry["registered_generated"])

    def test_generated_writer_helper_registers_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dst = root / ".claude" / "settings.json"
            _install._INSTALL_POLICY_ROOT = root
            _install._INSTALL_POLICY_MANIFEST = {}
            try:
                _install._write_generated_file(dst, "{}", force=True)
                entry = _install._INSTALL_POLICY_MANIFEST[".claude/settings.json"]
            finally:
                _install._reset_install_policy_context()

            self.assertEqual(dst.read_text(encoding="utf-8"), "{}\n")
            self.assertTrue(entry["registered_generated"])

    def test_install_receipt_records_source_and_target_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = _install._write_install_receipt(
                root,
                source_sha256="source-hash",
                entries={"scripts/omc.py": {"status": "updated"}},
            )

            self.assertEqual(receipt["source_sha256"], "source-hash")
            self.assertEqual(receipt["entries"]["scripts/omc.py"]["status"], "updated")
            self.assertTrue((root / ".omc" / "install-receipt.json").exists())

    def test_receipt_marks_hash_mismatch_as_blocked(self):
        entry = _install._receipt_entry(
            policy="managed_exact",
            source_sha256="source",
            target_sha256="different",
        )

        self.assertEqual(entry["status"], "blocked")


class TestPostCommitHookInstall(unittest.TestCase):
    def test_existing_hook_is_untouched_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template"
            hook = root / "hooks" / "post-commit"
            hook.parent.mkdir()
            template.write_text("#!/bin/sh\n# OMC:POST_COMMIT:V1\n", encoding="utf-8")
            custom = "#!/bin/sh\nprintf 'custom hook\\n'\n"
            hook.write_text(custom, encoding="utf-8")

            _install._install_post_commit_hook(template, hook, force=False)

            self.assertEqual(hook.read_text(encoding="utf-8"), custom)
            self.assertFalse(hook.with_name("post-commit.omc-original").exists())

    def test_existing_hook_is_backed_up_and_chained_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template"
            hook = root / "hooks" / "post-commit"
            hook.parent.mkdir()
            template.write_text(
                "#!/bin/sh\n# OMC:POST_COMMIT:V1\n"
                'original="$(dirname "$0")/post-commit.omc-original"\n',
                encoding="utf-8",
            )
            hook.write_text("#!/bin/sh\nprintf 'custom hook\\n'\n", encoding="utf-8")

            _install._install_post_commit_hook(template, hook, force=True)
            backup = hook.with_name("post-commit.omc-original")
            self.assertEqual(backup.read_text(encoding="utf-8"), "#!/bin/sh\nprintf 'custom hook\\n'\n")
            self.assertIn("OMC:POST_COMMIT:V1", hook.read_text(encoding="utf-8"))

            _install._install_post_commit_hook(template, hook, force=True)
            self.assertEqual(backup.read_text(encoding="utf-8"), "#!/bin/sh\nprintf 'custom hook\\n'\n")


class TestAutoUpdateHook(unittest.TestCase):
    def test_auto_update_exit_code_preserves_up_to_date_success(self):
        self.assertEqual(_install._auto_update_exit_code("up_to_date"), 0)
        self.assertEqual(_install._auto_update_exit_code("update_available"), 0)
        self.assertEqual(_install._auto_update_exit_code("local_conflict"), 10)
        self.assertEqual(_install._auto_update_exit_code("source_missing"), 11)

    def test_auto_update_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {}, clear=True):
                result = _hooks._auto_update(Path(tmp))

            self.assertEqual(result["status"], "skipped")

    def test_auto_update_invokes_installer_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installer = root / "scripts" / "install.py"
            installer.parent.mkdir()
            installer.write_text("# installer\n", encoding="utf-8")
            with patch.dict("os.environ", {"OMC_AUTO_UPDATE": "1"}, clear=True):
                with patch.object(_hooks.subprocess, "run") as run:
                    run.return_value.returncode = 0
                    result = _hooks._auto_update(root)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(run.call_args.args[0][-1], "--auto-update")

    def test_auto_update_reports_blocked_when_installer_rejects_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installer = root / "scripts" / "install.py"
            installer.parent.mkdir()
            installer.write_text("# installer\n", encoding="utf-8")
            with patch.dict("os.environ", {"OMC_AUTO_UPDATE": "1"}, clear=True):
                with patch.object(_hooks.subprocess, "run") as run:
                    run.return_value.returncode = 10
                    result = _hooks._auto_update(root)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "local_conflict")

    def test_auto_update_reports_blocked_when_source_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installer = root / "scripts" / "install.py"
            installer.parent.mkdir()
            installer.write_text("# installer\n", encoding="utf-8")
            with patch.dict("os.environ", {"OMC_AUTO_UPDATE": "1"}, clear=True):
                with patch.object(_hooks.subprocess, "run") as run:
                    run.return_value.returncode = 11
                    result = _hooks._auto_update(root)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "source_missing")


class TestWrite(unittest.TestCase):
    def test_write_creates_file_with_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "out.txt"
            _install._write(dst, "content", force=True)
            self.assertEqual(dst.read_text(encoding="utf-8"), "content\n")

    def test_write_skips_existing_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "out.txt"
            dst.write_text("old\n", encoding="utf-8")
            _install._write(dst, "new", force=False)
            self.assertEqual(dst.read_text(encoding="utf-8"), "old\n")


class TestLegacyOverlayCleanup(unittest.TestCase):
    def test_remove_legacy_overlay_deletes_matching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CLAUDE.md"
            path.write_text("## OMC Overlay For Claude\n", encoding="utf-8")
            _install._remove_legacy_overlay(path, "## OMC Overlay For Claude")
            self.assertFalse(path.exists())

    def test_remove_legacy_overlay_keeps_custom_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CLAUDE.md"
            path.write_text("# Team CLAUDE Notes\n", encoding="utf-8")
            _install._remove_legacy_overlay(path, "## OMC Overlay For Claude")
            self.assertEqual(path.read_text(encoding="utf-8"), "# Team CLAUDE Notes\n")


class TestAgentsMerge(unittest.TestCase):
    def test_force_preserves_custom_agents_and_appends_omc_block_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            agents = target / "AGENTS.md"
            agents.write_text("# Project Rules\n\nKeep me.\n", encoding="utf-8")

            _install._merge_agents_template(
                agents,
                "# OMC BEGIN\n## OMC — Orchestrated Multi-agent Craft\nomc\n# OMC END\n",
            )

            text = agents.read_text(encoding="utf-8")
            self.assertIn("# Project Rules", text)
            self.assertIn("Keep me.", text)
            self.assertIn("## OMC — Orchestrated Multi-agent Craft", text)

    def test_force_replaces_existing_omc_block_without_touching_custom_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            agents = target / "AGENTS.md"
            agents.write_text(
                "# Project Rules\n\n"
                "# OMC BEGIN\n"
                "## OMC — Orchestrated Multi-agent Craft\n"
                "old omc\n"
                "# OMC END\n\n"
                "Tail note.\n",
                encoding="utf-8",
            )

            _install._merge_agents_template(
                agents,
                "# OMC BEGIN\n## OMC — Orchestrated Multi-agent Craft\nnew omc\n# OMC END\n",
            )

            text = agents.read_text(encoding="utf-8")
            self.assertIn("# Project Rules", text)
            self.assertIn("Tail note.", text)
            self.assertIn("new omc", text)
            self.assertNotIn("old omc", text)
            self.assertEqual(text.count("# OMC BEGIN"), 1)

    def test_force_is_idempotent_when_same_omc_block_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            agents = target / "AGENTS.md"
            block = "# OMC BEGIN\n## OMC — Orchestrated Multi-agent Craft\nsame omc\n# OMC END\n"
            agents.write_text("# Project Rules\n\n" + block, encoding="utf-8")

            _install._merge_agents_template(agents, block)
            _install._merge_agents_template(agents, block)

            text = agents.read_text(encoding="utf-8")
            self.assertEqual(text.count("# OMC BEGIN"), 1)
            self.assertEqual(text.count("same omc"), 1)

    def test_force_preserves_legacy_tail_when_replacing_legacy_omc_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            agents = target / "AGENTS.md"
            agents.write_text(
                "Project intro\n\n"
                "## Engineering Ethos\n"
                "legacy ethos\n\n"
                "## OMC — Orchestrated Multi-agent Craft\n"
                'legacy omc\n\n'
                '> "PROCEED 판정입니다. 바로 플랜을 작성하겠습니다. [plan 내용 시작]..."\n\n'
                "Project tail rule.\n",
                encoding="utf-8",
            )

            _install._merge_agents_template(
                agents,
                "<!-- OMC:BEGIN -->\n## OMC — Orchestrated Multi-agent Craft\nnew omc\n<!-- OMC:END -->\n",
            )

            text = agents.read_text(encoding="utf-8")
            self.assertIn("Project intro", text)
            self.assertIn("Project tail rule.", text)
            self.assertIn("new omc", text)
            self.assertNotIn("legacy omc", text)

    def test_force_preserves_realistic_custom_agents_sections_with_managed_block_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            agents = target / "AGENTS.md"
            agents.write_text(
                "# AGENTS.md\n\n"
                "프로젝트 소개 문구.\n\n"
                "## FE 팀원용 빠른 시작\n\n"
                "1. 먼저 읽기: AGENTS.md\n"
                "2. 바로 멈출 변경: auth, payment\n\n"
                "<!-- OMC:BEGIN -->\n"
                "<!-- OMC:AGENTS:V1 -->\n"
                "## OMC — Orchestrated Multi-agent Craft\n"
                "old managed block\n"
                "<!-- OMC:END -->\n\n"
                "## Git and Pull Request Rules\n\n"
                "- base는 develop\n\n"
                "## 13. 완료 전 체크리스트\n\n"
                "- 요청한 동작만 변경했습니다.\n",
                encoding="utf-8",
            )

            _install._merge_agents_template(
                agents,
                "<!-- OMC:BEGIN -->\n"
                "<!-- OMC:AGENTS:V1 -->\n"
                "## OMC — Orchestrated Multi-agent Craft\n"
                "new managed block\n"
                "<!-- OMC:END -->\n",
            )

            text = agents.read_text(encoding="utf-8")
            self.assertIn("## FE 팀원용 빠른 시작", text)
            self.assertIn("## Git and Pull Request Rules", text)
            self.assertIn("## 13. 완료 전 체크리스트", text)
            self.assertIn("new managed block", text)
            self.assertNotIn("old managed block", text)
            self.assertEqual(text.count("<!-- OMC:BEGIN -->"), 1)
            self.assertEqual(text.count("<!-- OMC:END -->"), 1)

    def test_extract_agents_omc_block_uses_managed_markers(self):
        template = (
            "Project intro\n"
            "<!-- OMC:BEGIN -->\n"
            "<!-- OMC:AGENTS:V1 -->\n"
            "## OMC — Orchestrated Multi-agent Craft\n"
            "managed\n"
            "<!-- OMC:END -->\n"
            "tail\n"
        )
        block = _install._extract_agents_omc_block(template)
        self.assertEqual(
            block,
            "<!-- OMC:BEGIN -->\n<!-- OMC:AGENTS:V1 -->\n## OMC — Orchestrated Multi-agent Craft\nmanaged\n<!-- OMC:END -->\n",
        )


class TestDoctorAgentsBlock(unittest.TestCase):
    def test_doctor_requires_latest_agents_block_not_just_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "<!-- OMC:BEGIN -->\nold block\n<!-- OMC:END -->\n",
                encoding="utf-8",
            )
            (root / ".claude").mkdir(parents=True)
            (root / ".gemini").mkdir(parents=True)
            (root / ".claude" / "CLAUDE.md").write_text("OMC Overlay\n", encoding="utf-8")
            (root / ".gemini" / "GEMINI.md").write_text("OMC Overlay\n", encoding="utf-8")

            checks = _doctor._build_checks(root)
            labels = {check.label: check.ok for check in checks}

            self.assertFalse(labels["AGENTS.md (최신 OMC 블록 포함)"])


class TestTemplateRootResolution(unittest.TestCase):
    def test_templates_root_prefers_direct_templates_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            direct = root / "templates"
            direct.mkdir(parents=True)

            self.assertEqual(_install._templates_root(root), direct)


class TestRepositoryGitignore(unittest.TestCase):
    def test_gitignore_excludes_install_source_metadata(self):
        gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".omc/install-source.json", gitignore)

    def test_gitignore_keeps_claude_command_docs_trackable(self):
        gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text(gitignore, encoding="utf-8")
            (root / ".claude" / "commands").mkdir(parents=True)
            (root / "templates" / ".claude" / "commands").mkdir(parents=True)
            (root / ".claude" / "CLAUDE.md").write_text("overlay\n", encoding="utf-8")
            (root / ".claude" / "commands" / "qa.md").write_text("# /qa\n", encoding="utf-8")
            (root / "templates" / ".claude" / "commands" / "qa.md").write_text("# /qa\n", encoding="utf-8")

            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)

            ignored_overlay = subprocess.run(
                ["git", "check-ignore", ".claude/CLAUDE.md"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            tracked_live_command = subprocess.run(
                ["git", "check-ignore", ".claude/commands/qa.md"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            tracked_template_command = subprocess.run(
                ["git", "check-ignore", "templates/.claude/commands/qa.md"],
                cwd=root,
                capture_output=True,
                text=True,
            )

            self.assertEqual(ignored_overlay.returncode, 0, ignored_overlay.stderr)
            self.assertNotEqual(tracked_live_command.returncode, 0, tracked_live_command.stdout)
            self.assertNotEqual(tracked_template_command.returncode, 0, tracked_template_command.stdout)

    def test_install_writes_source_metadata_and_skips_embedded_kit_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit = root / "kit"
            target = root / "target"
            target.mkdir()

            (kit / "scripts").mkdir(parents=True)
            (kit / "scripts" / "install.py").write_text("# install\n", encoding="utf-8")
            templates = kit / "templates"
            templates.mkdir(parents=True)
            (templates / ".DS_Store").write_text("junk", encoding="utf-8")
            (templates / "AGENTS.md").write_text("## OMC — Orchestrated Multi-agent Craft\n", encoding="utf-8")
            (templates / "ETHOS.md").write_text("## Engineering Ethos\n___\n", encoding="utf-8")
            (kit / "prompts").mkdir(parents=True)
            (kit / "prompts" / "README.md").write_text("prompts\n", encoding="utf-8")
            for name in [
                "team.json",
                "ROLE_ORCHESTRATOR.md",
                "MODE_AUTOPILOT.md",
                "MODE_TEAM.md",
                "MODE_ULTRAWORK.md",
                "MODE_RALPH.md",
                "MODE_DEEP_INTERVIEW.md",
                "ROLE_SEARCH_ASSISTANT.md",
                "ROLE_ANALYSIS_ASSISTANT.md",
                "ROLE_CODE_REVIEW_ASSISTANT.md",
                "ROLE_SENIOR_CODING_ASSISTANT.md",
            ]:
                (kit / "prompts" / name).write_text("x\n", encoding="utf-8")
            (kit / "docs").mkdir(parents=True)
            for name in [
                "omc_workflow.md",
                "quickstart_kr.md",
                "kit_map.md",
                "next_project_pack.md",
                "agent_behavior.md",
                "verification_checklist.md",
            ]:
                (kit / "docs" / name).write_text("doc\n", encoding="utf-8")
            (templates / ".claude").mkdir(parents=True)
            (templates / ".gemini").mkdir(parents=True)
            (templates / ".claude" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")
            (templates / ".gemini" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")

            with patch.object(_install, "_kit_root", return_value=kit), \
                 patch.object(_install, "_assert_claude_hook_contract"), \
                 patch.object(_install, "_assert_gemini_hook_contract"), \
                 patch.object(_install, "_install_claude_settings"), \
                 patch.object(_install, "_install_gemini_settings"), \
                 patch.object(_install, "_install_shared_lessons"), \
                 patch.object(_install, "_ensure_executable"), \
                 patch.object(_install, "_setup_ethos_section5"), \
                 patch.object(_install, "_check_force_regression", return_value=True), \
                 patch.object(_install, "_deployed_script_names", return_value=[]), \
                 patch("sys.argv", ["install.py", "--target", str(target)]):
                _install.main()

            self.assertFalse((target / "omc_kit").exists())
            metadata = json.loads((target / ".omc" / "install-source.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["source_path"], str(kit.resolve()))
            self.assertEqual(metadata["source_kind"], "external")
            self.assertFalse((target / "omc_kit" / "templates" / ".DS_Store").exists())


class TestInstallSourceResolution(unittest.TestCase):
    def test_resolve_source_kit_rejects_non_omc_local_structure_even_with_install_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "project"
            (current / "templates").mkdir(parents=True)
            (current / "scripts").mkdir(parents=True)
            (current / "scripts" / "install.py").write_text("# unrelated install\n", encoding="utf-8")
            source = root / "source-kit"
            (source / "templates").mkdir(parents=True)
            (source / "scripts").mkdir(parents=True)
            (source / "scripts" / "install.py").write_text("# install\n", encoding="utf-8")
            (source / "prompts").mkdir(parents=True)
            (source / "prompts" / "team.json").write_text("{}", encoding="utf-8")
            _install._write_install_source_metadata(current, source)

            self.assertEqual(_install._resolve_source_kit(current, current), source.resolve())

    def test_resolve_source_kit_ignores_unrelated_local_templates_when_metadata_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "project"
            (current / "templates").mkdir(parents=True)
            source = root / "source-kit"
            (source / "templates").mkdir(parents=True)
            (source / "scripts").mkdir(parents=True)
            (source / "scripts" / "install.py").write_text("# install\n", encoding="utf-8")
            (source / "prompts").mkdir(parents=True)
            (source / "prompts" / "team.json").write_text("{}", encoding="utf-8")
            _install._write_install_source_metadata(current, source)

            self.assertEqual(_install._resolve_source_kit(current, current), source.resolve())

    def test_resolve_source_kit_prefers_metadata_path_when_templates_missing_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "project"
            current.mkdir()
            source = root / "source-kit"
            (source / "templates").mkdir(parents=True)
            (source / "scripts").mkdir(parents=True)
            (source / "scripts" / "install.py").write_text("# install\n", encoding="utf-8")
            (source / "prompts").mkdir(parents=True)
            (source / "prompts" / "team.json").write_text("{}", encoding="utf-8")
            _install._write_install_source_metadata(current, source)

            self.assertEqual(_install._resolve_source_kit(current, current), source.resolve())

    def test_resolve_source_kit_errors_when_metadata_path_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "project"
            current.mkdir()
            missing = root / "missing-kit"
            _install._write_install_source_metadata(current, missing)

            with self.assertRaisesRegex(SystemExit, "install source path is missing"):
                _install._resolve_source_kit(current, current)

    def test_resolve_source_kit_errors_when_only_legacy_embedded_kit_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "project"
            (current / "omc_kit" / "templates").mkdir(parents=True)

            with self.assertRaisesRegex(SystemExit, "legacy embedded omc_kit"):
                _install._resolve_source_kit(current, current)


class TestCheckForceRegression(unittest.TestCase):
    def _make_kit(self, tmp: str, rules: dict[str, str], scripts: dict[str, str]) -> Path:
        kit = Path(tmp) / "kit"
        tmpl_rules = kit / "templates" / ".cursor" / "rules"
        tmpl_rules.mkdir(parents=True)
        for name, content in rules.items():
            (tmpl_rules / name).write_text(content, encoding="utf-8")
        kit_scripts = kit / "scripts"
        kit_scripts.mkdir(parents=True)
        for name, content in scripts.items():
            (kit_scripts / name).write_text(content, encoding="utf-8")
        return kit

    def _make_tgt(self, tmp: str, rules: dict[str, str], scripts: dict[str, str]) -> Path:
        tgt = Path(tmp) / "tgt"
        live_rules = tgt / ".cursor" / "rules"
        live_rules.mkdir(parents=True)
        for name, content in rules.items():
            (live_rules / name).write_text(content, encoding="utf-8")
        tgt_scripts = tgt / "scripts"
        tgt_scripts.mkdir(parents=True)
        for name, content in scripts.items():
            (tgt_scripts / name).write_text(content, encoding="utf-8")
        return tgt

    def test_no_diff_returns_true_silently(self):
        """템플릿 ↔ live 동일하면 True, 경고 없음."""
        with tempfile.TemporaryDirectory() as tmp:
            same = "line1\nline2\n"
            kit = self._make_kit(tmp, {"omc-always.md": same}, {})
            tgt = self._make_tgt(tmp, {"omc-always.md": same}, {})
            captured = io.StringIO()
            with patch("sys.stdin", io.StringIO()), patch("sys.stdout", captured):
                result = _install._check_force_regression(kit, tgt)
            self.assertTrue(result)
            self.assertNotIn("WARN", captured.getvalue())

    def test_ssot_diff_prints_warning(self):
        """live가 templates보다 많으면 SSOT 불일치 경고 출력."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpl_content = "line1\nline2\n"
            live_content = "line1\nline2\nextra line\nextra2\n"
            kit = self._make_kit(tmp, {"omc-always.md": tmpl_content}, {})
            tgt = self._make_tgt(tmp, {"omc-always.md": live_content}, {})
            captured = io.StringIO()
            with patch("sys.stdin", io.StringIO()), patch("sys.stdout", captured):
                result = _install._check_force_regression(kit, tgt)
            self.assertTrue(result)
            self.assertIn("SSOT 불일치", captured.getvalue())
            self.assertIn("omc-always.md", captured.getvalue())

    def test_ssot_diff_shows_positive_line_count(self):
        """경고 메시지에 live 줄 수 차이(+N)가 포함된다."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpl_content = "a\nb\n"
            live_content = "a\nb\nc\nd\ne\n"
            kit = self._make_kit(tmp, {"omc-always.md": tmpl_content}, {})
            tgt = self._make_tgt(tmp, {"omc-always.md": live_content}, {})
            captured = io.StringIO()
            with patch("sys.stdin", io.StringIO()), patch("sys.stdout", captured):
                _install._check_force_regression(kit, tgt)
            self.assertIn("+3", captured.getvalue())

    def test_script_regression_prints_warning(self):
        """scripts/ 회귀 감지 시 경고 출력."""
        base = "x\n" * 5
        live_extra = base + "y\n" * 15
        with tempfile.TemporaryDirectory() as tmp:
            kit = self._make_kit(tmp, {}, {"omc_tdd_check.py": base})
            tgt = self._make_tgt(tmp, {}, {"omc_tdd_check.py": live_extra})
            captured = io.StringIO()
            with patch("sys.stdin", io.StringIO()), patch("sys.stdout", captured):
                result = _install._check_force_regression(kit, tgt)
            self.assertTrue(result)
            self.assertIn("WARN", captured.getvalue())

    def test_no_scripts_dir_returns_true(self):
        """scripts/ 디렉토리 없어도 True 반환 (설치 첫 실행 시나리오)."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            (kit / "templates" / ".cursor" / "rules").mkdir(parents=True)
            (kit / "scripts").mkdir(parents=True)
            tgt = Path(tmp) / "tgt"
            tgt.mkdir()
            result = _install._check_force_regression(kit, tgt)
            self.assertTrue(result)


class TestSharedLessons(unittest.TestCase):
    def test_shared_lessons_copied_to_target(self):
        """install 실행 시 templates/shared_lessons/ 가 .omc/lessons/ 에 복사된다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_lessons"
            shared.mkdir(parents=True)
            (shared / "lesson-a.md").write_text("# lesson a", encoding="utf-8")
            (shared / "lesson-b.md").write_text("# lesson b", encoding="utf-8")

            tgt = Path(tmp) / "tgt"
            tgt.mkdir()

            _install._install_shared_lessons(kit, tgt)

            lessons_dir = tgt / ".omc" / "lessons"
            self.assertTrue((lessons_dir / "lesson-a.md").exists())
            self.assertTrue((lessons_dir / "lesson-b.md").exists())

    def test_shared_lessons_skips_existing(self):
        """이미 존재하는 교훈 파일은 덮어쓰지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_lessons"
            shared.mkdir(parents=True)
            (shared / "lesson-a.md").write_text("template version", encoding="utf-8")

            tgt = Path(tmp) / "tgt"
            lessons_dir = tgt / ".omc" / "lessons"
            lessons_dir.mkdir(parents=True)
            (lessons_dir / "lesson-a.md").write_text("custom version", encoding="utf-8")

            _install._install_shared_lessons(kit, tgt)

            self.assertEqual(
                (lessons_dir / "lesson-a.md").read_text(encoding="utf-8"),
                "custom version",
            )

    def test_shared_lessons_no_dir_is_noop(self):
        """templates/shared_lessons/ 가 없으면 조용히 통과한다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            (kit / "templates").mkdir(parents=True)
            tgt = Path(tmp) / "tgt"
            tgt.mkdir()
            _install._install_shared_lessons(kit, tgt)
            self.assertFalse((tgt / ".omc" / "lessons").exists())


class TestSharedTasks(unittest.TestCase):
    def test_shared_tasks_migration_contract_is_explicit(self):
        """migration 옵션의 force 선행 조건과 함수 정책이 문서화되어 있다."""
        help_result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "install.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("requires --force", help_result.stdout.lower())
        self.assertIn("migrate", _install._install_shared_tasks.__doc__.lower())

    def test_shared_tasks_copied_to_target(self):
        """install 실행 시 templates/shared_tasks/ 가 .omc/tasks/ 에 복사된다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_tasks"
            shared.mkdir(parents=True)
            (shared / "observed-collect.json").write_text(
                '{"id":"observed-collect","require_clean_scope":true}\n',
                encoding="utf-8",
            )

            tgt = Path(tmp) / "tgt"
            tgt.mkdir()

            _install._install_shared_tasks(kit, tgt)

            tasks_dir = tgt / ".omc" / "tasks"
            self.assertTrue((tasks_dir / "observed-collect.json").exists())
            self.assertEqual(
                json.loads((tasks_dir / "observed-collect.json").read_text(encoding="utf-8"))["require_clean_scope"],
                True,
            )

    def test_shared_tasks_skips_existing(self):
        """이미 존재하는 태스크 파일은 덮어쓰지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_tasks"
            shared.mkdir(parents=True)
            (shared / "observed-collect.json").write_text(
                '{"id":"observed-collect","require_clean_scope":true}\n',
                encoding="utf-8",
            )

            tgt = Path(tmp) / "tgt"
            tasks_dir = tgt / ".omc" / "tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "observed-collect.json").write_text(
                '{"id":"observed-collect","require_clean_scope":false}\n',
                encoding="utf-8",
            )

            _install._install_shared_tasks(kit, tgt)

            self.assertEqual(
                json.loads((tasks_dir / "observed-collect.json").read_text(encoding="utf-8"))["require_clean_scope"],
                False,
            )

    def test_shared_tasks_force_refreshes_matching_omc_task_only(self):
        """force는 OMC 관리 task만 갱신하고 사용자 task는 보존한다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_tasks"
            shared.mkdir(parents=True)
            (shared / "pilot.json").write_text(
                '{"id":"pilot","omc_managed":true,"version":2}\n',
                encoding="utf-8",
            )
            (shared / "custom.json").write_text(
                '{"id":"custom","omc_managed":true,"version":2}\n',
                encoding="utf-8",
            )
            (shared / "collision.json").write_text(
                '{"id":"collision","omc_managed":true,"version":2}\n',
                encoding="utf-8",
            )
            (shared / "legacy.json").write_text(
                '{"id":"legacy","omc_managed":true,"executor":"auto","version":2}\n',
                encoding="utf-8",
            )

            tgt = Path(tmp) / "tgt"
            tasks_dir = tgt / ".omc" / "tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "pilot.json").write_text(
                '{"id":"pilot","omc_managed":true,"version":1}\n',
                encoding="utf-8",
            )
            (tasks_dir / "custom.json").write_text(
                '{"id":"local-custom","version":1}\n',
                encoding="utf-8",
            )
            (tasks_dir / "collision.json").write_text(
                '{"id":"collision","custom_field":"keep-me","version":1}\n',
                encoding="utf-8",
            )
            (tasks_dir / "legacy.json").write_text(
                '{"id":"legacy","executor":"custom","version":1}\n',
                encoding="utf-8",
            )

            _install._install_shared_tasks(kit, tgt, force=True)

            self.assertEqual(
                json.loads((tasks_dir / "pilot.json").read_text(encoding="utf-8"))["version"],
                2,
            )
            self.assertEqual(
                json.loads((tasks_dir / "custom.json").read_text(encoding="utf-8"))["id"],
                "local-custom",
            )
            self.assertEqual(
                json.loads((tasks_dir / "collision.json").read_text(encoding="utf-8"))["custom_field"],
                "keep-me",
            )
            self.assertEqual(
                json.loads((tasks_dir / "legacy.json").read_text(encoding="utf-8"))["executor"],
                "custom",
            )

    def test_shared_tasks_force_skips_non_object_json_without_crashing(self):
        """force는 배열/null task를 보존하고 설치를 중단하지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_tasks"
            shared.mkdir(parents=True)
            (shared / "pilot.json").write_text(
                '{"id":"pilot","omc_managed":true,"version":2}\n',
                encoding="utf-8",
            )

            tgt = Path(tmp) / "tgt"
            tasks_dir = tgt / ".omc" / "tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "pilot.json").write_text("[]\n", encoding="utf-8")

            _install._install_shared_tasks(kit, tgt, force=True)

            self.assertEqual(
                json.loads((tasks_dir / "pilot.json").read_text(encoding="utf-8")),
                [],
            )

    def test_shared_tasks_migrate_refreshes_markerless_matching_task(self):
        """명시적 migrate만 legacy markerless OMC task를 갱신한다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_tasks"
            shared.mkdir(parents=True)
            (shared / "legacy.json").write_text(
                '{"id":"legacy","omc_managed":true,"executor":"auto","version":2}\n',
                encoding="utf-8",
            )

            tgt = Path(tmp) / "tgt"
            tasks_dir = tgt / ".omc" / "tasks"
            tasks_dir.mkdir(parents=True)
            legacy_path = tasks_dir / "legacy.json"
            legacy_path.write_text(
                '{"id":"legacy","executor":"custom","version":1}\n',
                encoding="utf-8",
            )

            _install._install_shared_tasks(kit, tgt, force=True)
            self.assertEqual(
                json.loads(legacy_path.read_text(encoding="utf-8"))["executor"],
                "custom",
            )

            _install._install_shared_tasks(kit, tgt, force=True, migrate=True)
            migrated = json.loads(legacy_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["version"], 2)
            self.assertTrue(migrated["omc_managed"])

    def test_observed_collect_template_matches_runtime_task(self):
        """설치 템플릿과 로컬 runtime task가 drift 없이 동일해야 한다."""
        runtime_task = Path(__file__).parent.parent / ".omc" / "tasks" / "observed-collect.json"
        template_task = Path(__file__).parent.parent / "templates" / "shared_tasks" / "observed-collect.json"

        self.assertEqual(
            json.loads(template_task.read_text(encoding="utf-8")),
            json.loads(runtime_task.read_text(encoding="utf-8")),
        )


class TestInstallGeminiSettings(unittest.TestCase):
    def _settings_path(self, tmp: str) -> Path:
        p = Path(tmp) / ".gemini" / "settings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def test_creates_with_beforetool_when_no_existing(self):
        """기존 파일 없을 때 BeforeTool 포함 settings.json 생성."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._settings_path(tmp)
            _install._install_gemini_settings(path, force=False)
            import json
            data = json.loads(path.read_text())
            self.assertIn("BeforeTool", data["hooks"])
            matcher = data["hooks"]["BeforeTool"][0]["matcher"]
            self.assertEqual(matcher, "write_file|replace")

    def test_adds_beforetool_to_existing_without_it(self):
        """기존 파일에 BeforeTool 없을 때 추가 (non-force)."""
        with tempfile.TemporaryDirectory() as tmp:
            import json
            path = self._settings_path(tmp)
            path.write_text(json.dumps({"hooks": {"SessionStart": []}}), encoding="utf-8")
            _install._install_gemini_settings(path, force=False)
            data = json.loads(path.read_text())
            self.assertIn("BeforeTool", data["hooks"])
            self.assertIn("SessionStart", data["hooks"])  # 기존 키 보존

    def test_skips_beforetool_if_already_present(self):
        """기존 파일에 BeforeTool 있으면 덮어쓰지 않는다 (non-force)."""
        with tempfile.TemporaryDirectory() as tmp:
            import json
            path = self._settings_path(tmp)
            custom = {"hooks": {"BeforeTool": [{"matcher": "custom_matcher"}]}}
            path.write_text(json.dumps(custom), encoding="utf-8")
            _install._install_gemini_settings(path, force=False)
            data = json.loads(path.read_text())
            self.assertEqual(data["hooks"]["BeforeTool"][0]["matcher"], "custom_matcher")

    def test_force_overwrites_with_beforetool(self):
        """force=True 시 전체 덮어쓰기 — BeforeTool 포함."""
        with tempfile.TemporaryDirectory() as tmp:
            import json
            path = self._settings_path(tmp)
            path.write_text(json.dumps({"hooks": {"BeforeTool": [{"matcher": "old"}]}}),
                            encoding="utf-8")
            _install._install_gemini_settings(path, force=True)
            data = json.loads(path.read_text())
            self.assertEqual(data["hooks"]["BeforeTool"][0]["matcher"], "write_file|replace")


class TestHookContractMarkers(unittest.TestCase):
    def test_codex_hook_contract_exposes_canonical_specs(self):
        self.assertEqual(
            _hook_contract.CODEX_HOOK_CONTRACT["session_context"]["label"],
            ".codex/hooks.json (SessionStart + UserPromptSubmit hook)",
        )
        self.assertIn(
            "SessionStart",
            _hook_contract.CODEX_HOOK_CONTRACT["session_context"]["required_hooks"],
        )
        self.assertEqual(
            _hook_contract.CODEX_HOOK_CONTRACT["post_mutate_soft_guard"]["command"],
            ".agent-hooks/omc-post-file-check.sh",
        )

    def test_claude_and_gemini_hook_contract_expose_canonical_specs(self):
        self.assertEqual(
            _hook_contract.CLAUDE_HOOK_CONTRACT["session_context"]["label"],
            ".claude/settings.json (SessionStart + UserPromptSubmit hook)",
        )
        self.assertEqual(
            _hook_contract.GEMINI_HOOK_CONTRACT["pre_mutate_guard"]["label"],
            ".gemini/settings.json (BeforeTool hook)",
        )

    def test_install_source_declares_shared_hook_contract_markers(self):
        text = (Path(__file__).parent / "omc_hook_contract.py").read_text(encoding="utf-8")
        for marker in (
            "session bootstrap",
            "pre-mutate guard",
            "post-mutate soft guard",
            "install/doctor verification",
        ):
            self.assertIn(marker, text, f"omc_hook_contract.py에 공통 훅 계약 marker 누락: {marker}")

    def test_install_imports_shared_hook_contract(self):
        text = (Path(__file__).parent / "install.py").read_text(encoding="utf-8")
        self.assertIn("from omc_hook_contract import", text)

    def test_install_doc_uses_runtime_hook_contract_summary(self):
        doc = _install._install_claude_settings.__doc__ or ""
        self.assertIn(
            _install.HOOK_CONTRACT_SUMMARY,
            doc,
            "HOOK_CONTRACT_SUMMARY 실제 값이 install docstring에 반영되지 않았습니다",
        )
        self.assertNotIn(
            "HOOK_CONTRACT_SUMMARY",
            doc,
            "상수 이름만 남아 있고 실제 값으로 치환되지 않았습니다",
        )

    def test_install_validates_codex_template_against_shared_contract(self):
        _install._assert_codex_hook_contract(
            Path(__file__).parent.parent / "templates" / ".codex" / "hooks.json"
        )

    def test_install_validates_claude_template_against_shared_contract(self):
        _install._assert_claude_hook_contract(
            Path(__file__).parent.parent / "templates" / ".claude" / "settings.json"
        )

    def test_install_validates_gemini_template_against_shared_contract(self):
        _install._assert_gemini_hook_contract(
            Path(__file__).parent.parent / "templates" / ".gemini" / "settings.json"
        )

    def test_install_main_calls_claude_and_gemini_validators_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit = root / "kit"
            target = root / "target"
            target.mkdir()

            (kit / "scripts").mkdir(parents=True)
            (kit / "scripts" / "install.py").write_text("# install\n", encoding="utf-8")
            (kit / "prompts").mkdir(parents=True)
            (kit / "prompts" / "team.json").write_text("{}", encoding="utf-8")
            (kit / "templates" / ".claude").mkdir(parents=True)
            (kit / "templates" / ".gemini").mkdir(parents=True)
            (kit / "templates" / ".codex").mkdir(parents=True)
            (kit / "templates" / ".claude" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")
            (kit / "templates" / ".gemini" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")
            (kit / "templates" / ".codex" / "hooks.json").write_text('{"hooks":{}}', encoding="utf-8")

            called: list[tuple[str, str]] = []

            def _record(label: str):
                def _inner(path: Path) -> None:
                    called.append((label, path.name))
                return _inner

            shared_tasks_mock = patch.object(_install, "_install_shared_tasks")
            shared_lessons_mock = patch.object(_install, "_install_shared_lessons")

            with patch.object(_install, "_kit_root", return_value=kit), \
                 patch.object(_install, "_assert_claude_hook_contract", side_effect=_record("claude")), \
                 patch.object(_install, "_assert_gemini_hook_contract", side_effect=_record("gemini")), \
                 patch.object(_install, "_assert_codex_hook_contract", side_effect=_record("codex")), \
                 patch.object(_install, "_copy"), \
                 patch.object(_install, "_install_claude_settings"), \
                 patch.object(_install, "_install_gemini_settings"), \
                 patch.object(_install, "_ensure_executable"), \
                 patch.object(_install, "_setup_ethos_section5"), \
                 shared_tasks_mock as shared_tasks, \
                 shared_lessons_mock as shared_lessons, \
                 patch.object(_install, "_check_force_regression", return_value=True), \
                 patch(
                     "sys.argv",
                     [
                         "install.py",
                         "--target",
                         str(target),
                         "--force",
                         "--migrate-shared-tasks",
                     ],
                 ):
                _install.main()

            self.assertIn(("claude", "settings.json"), called)
            self.assertIn(("gemini", "settings.json"), called)
            self.assertIn(("codex", "hooks.json"), called)
            shared_tasks.assert_called_once()
            shared_lessons.assert_called_once()
            self.assertEqual(
                tuple(path.resolve() for path in shared_tasks.call_args.args),
                (kit.resolve(), target.resolve()),
            )
            self.assertEqual(
                shared_tasks.call_args.kwargs,
                {"force": True, "migrate": True},
            )
            self.assertEqual(
                tuple(path.resolve() for path in shared_lessons.call_args.args),
                (kit.resolve(), target.resolve()),
            )

    def test_install_rejects_codex_template_missing_soft_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks_path = Path(tmp) / "hooks.json"
            hooks_path.write_text(
                (
                    '{"hooks":{"SessionStart":[{"hooks":[{"command":".agent-hooks/omc-session-start.sh codex"}]}],'
                    '"UserPromptSubmit":[{"hooks":[{"command":".agent-hooks/omc-prompt-inject.sh"}]}],'
                    '"PostToolUse":[]}}'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "post-mutate soft guard"):
                _install._assert_codex_hook_contract(hooks_path)


class TestClaudeOverlayInstall(unittest.TestCase):
    def test_install_copies_claude_overlay_into_dot_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit = root / "kit"
            target = root / "target"
            target.mkdir()

            (kit / "scripts").mkdir(parents=True)
            (kit / "scripts" / "install.py").write_text("# install\n", encoding="utf-8")
            (kit / "prompts").mkdir(parents=True)
            (kit / "prompts" / "team.json").write_text("{}", encoding="utf-8")
            templates = kit / "templates"
            templates.mkdir(parents=True)
            (templates / "CLAUDE.md").write_text("## OMC Overlay For Claude\n", encoding="utf-8")
            (templates / "AGENTS.md").write_text("## OMC — Orchestrated Multi-agent Craft\n", encoding="utf-8")
            (templates / "ETHOS.md").write_text("## Engineering Ethos\n___\n", encoding="utf-8")
            (templates / ".claude").mkdir(parents=True)
            (templates / ".claude" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")

            copied: list[tuple[Path, Path, bool]] = []

            def _record_copy(src: Path, dst: Path, *, force: bool) -> None:
                copied.append((src, dst, force))
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.exists():
                    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            with patch.object(_install, "_kit_root", return_value=kit), \
                 patch.object(_install, "_assert_claude_hook_contract"), \
                 patch.object(_install, "_install_claude_settings"), \
                 patch.object(_install, "_install_gemini_settings"), \
                 patch.object(_install, "_install_shared_lessons"), \
                 patch.object(_install, "_ensure_executable"), \
                 patch.object(_install, "_setup_ethos_section5"), \
                 patch.object(_install, "_check_force_regression", return_value=True), \
                 patch.object(_install, "_deployed_script_names", return_value=[]), \
                 patch.object(_install, "_copy", side_effect=_record_copy), \
                 patch("sys.argv", ["install.py", "--target", str(target)]):
                _install.main()

            self.assertFalse((target / "CLAUDE.md").exists())
            self.assertEqual(
                (target / ".claude" / "CLAUDE.md").read_text(encoding="utf-8"),
                "## OMC Overlay For Claude\n",
            )
            self.assertNotIn(
                (templates / "CLAUDE.md", target / "CLAUDE.md", False),
                copied,
            )


class TestPersonalOverlayInstall(unittest.TestCase):
    def test_install_copies_claude_and_gemini_overlays_into_personal_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit = root / "kit"
            target = root / "target"
            target.mkdir()

            (kit / "scripts").mkdir(parents=True)
            (kit / "scripts" / "install.py").write_text("# install\n", encoding="utf-8")
            templates = kit / "templates"
            templates.mkdir(parents=True)
            (templates / "CLAUDE.md").write_text("## OMC Overlay For Claude\n", encoding="utf-8")
            (templates / "GEMINI.md").write_text("## OMC Overlay For Gemini\n", encoding="utf-8")
            (templates / "AGENTS.md").write_text("## OMC — Orchestrated Multi-agent Craft\n", encoding="utf-8")
            (templates / "ETHOS.md").write_text("## Engineering Ethos\n___\n", encoding="utf-8")
            (templates / ".claude").mkdir(parents=True)
            (templates / ".gemini").mkdir(parents=True)
            (kit / "prompts").mkdir(parents=True)
            (kit / "prompts" / "README.md").write_text("prompts\n", encoding="utf-8")
            (kit / "prompts" / "team.json").write_text("{}", encoding="utf-8")
            (templates / ".claude" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")
            (templates / ".gemini" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")

            copied: list[tuple[Path, Path, bool]] = []

            def _record_copy(src: Path, dst: Path, *, force: bool) -> None:
                copied.append((src, dst, force))
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.exists():
                    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            with patch.object(_install, "_kit_root", return_value=kit), \
                 patch.object(_install, "_assert_claude_hook_contract"), \
                 patch.object(_install, "_assert_gemini_hook_contract"), \
                 patch.object(_install, "_install_claude_settings"), \
                 patch.object(_install, "_install_gemini_settings"), \
                 patch.object(_install, "_install_shared_lessons"), \
                 patch.object(_install, "_ensure_executable"), \
                 patch.object(_install, "_setup_ethos_section5"), \
                 patch.object(_install, "_check_force_regression", return_value=True), \
                 patch.object(_install, "_deployed_script_names", return_value=[]), \
                 patch.object(_install, "_copy", side_effect=_record_copy), \
                 patch("sys.argv", ["install.py", "--target", str(target)]):
                _install.main()

            self.assertFalse((target / "CLAUDE.md").exists())
            self.assertFalse((target / "GEMINI.md").exists())
            self.assertEqual(
                (target / ".claude" / "CLAUDE.md").read_text(encoding="utf-8"),
                "## OMC Overlay For Claude\n",
            )
            self.assertEqual(
                (target / ".gemini" / "GEMINI.md").read_text(encoding="utf-8"),
                "## OMC Overlay For Gemini\n",
            )
            self.assertNotIn(
                (templates / "CLAUDE.md", target / "CLAUDE.md", False),
                copied,
            )
            self.assertNotIn(
                (templates / "GEMINI.md", target / "GEMINI.md", False),
                copied,
            )

    def test_install_removes_legacy_root_overlays_when_markers_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit = root / "kit"
            target = root / "target"
            target.mkdir()

            (target / "CLAUDE.md").write_text("## OMC Overlay For Claude\n", encoding="utf-8")
            (target / "GEMINI.md").write_text("## OMC Overlay For Gemini\n", encoding="utf-8")

            (kit / "scripts").mkdir(parents=True)
            (kit / "scripts" / "install.py").write_text("# install\n", encoding="utf-8")
            (kit / "prompts").mkdir(parents=True)
            (kit / "prompts" / "team.json").write_text("{}", encoding="utf-8")
            templates = kit / "templates"
            templates.mkdir(parents=True)
            (templates / "CLAUDE.md").write_text("## OMC Overlay For Claude\n", encoding="utf-8")
            (templates / "GEMINI.md").write_text("## OMC Overlay For Gemini\n", encoding="utf-8")
            (templates / "AGENTS.md").write_text("## OMC — Orchestrated Multi-agent Craft\n", encoding="utf-8")
            (templates / "ETHOS.md").write_text("## Engineering Ethos\n___\n", encoding="utf-8")
            (templates / ".claude").mkdir(parents=True)
            (templates / ".gemini").mkdir(parents=True)
            (templates / ".claude" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")
            (templates / ".gemini" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")

            def _record_copy(src: Path, dst: Path, *, force: bool) -> None:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.exists():
                    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            with patch.object(_install, "_kit_root", return_value=kit), \
                 patch.object(_install, "_assert_claude_hook_contract"), \
                 patch.object(_install, "_assert_gemini_hook_contract"), \
                 patch.object(_install, "_install_claude_settings"), \
                 patch.object(_install, "_install_gemini_settings"), \
                 patch.object(_install, "_install_shared_lessons"), \
                 patch.object(_install, "_ensure_executable"), \
                 patch.object(_install, "_setup_ethos_section5"), \
                 patch.object(_install, "_check_force_regression", return_value=True), \
                 patch.object(_install, "_deployed_script_names", return_value=[]), \
                 patch.object(_install, "_copy", side_effect=_record_copy), \
                 patch("sys.argv", ["install.py", "--target", str(target)]):
                _install.main()

            self.assertFalse((target / "CLAUDE.md").exists())
            self.assertFalse((target / "GEMINI.md").exists())
            self.assertTrue((target / ".claude" / "CLAUDE.md").exists())
            self.assertTrue((target / ".gemini" / "GEMINI.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
