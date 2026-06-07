from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "test_omc_setup_smoke.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("test_omc_setup_smoke_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()
RESOLVE_INSTALL_SCRIPT = MODULE._resolve_install_script


def test_prefers_root_install_script_when_both_root_and_nested_exist(tmp_path: Path):
    root_install = tmp_path / "scripts" / "install.py"
    nested_install = tmp_path / "omc_kit" / "scripts" / "install.py"
    root_install.parent.mkdir(parents=True)
    nested_install.parent.mkdir(parents=True)
    root_install.write_text("# root install\n", encoding="utf-8")
    nested_install.write_text("# nested install\n", encoding="utf-8")

    resolved = RESOLVE_INSTALL_SCRIPT(tmp_path)

    assert resolved == root_install.resolve()


def test_falls_back_to_nested_install_script_when_root_missing(tmp_path: Path):
    nested_install = tmp_path / "omc_kit" / "scripts" / "install.py"
    nested_install.parent.mkdir(parents=True)
    nested_install.write_text("# nested install\n", encoding="utf-8")

    resolved = RESOLVE_INSTALL_SCRIPT(tmp_path)

    assert resolved == nested_install.resolve()


def test_base_setup_smoke_required_paths_exclude_optional_test_scripts_without_executor():
    text = MODULE_PATH.read_text(encoding="utf-8")

    assert '"scripts/test_omc_headless_smoke.py"' in text
    assert '"scripts/test_omc_chat_headless_smoke.py"' in text
    assert 'if args.executor:' in text

    base_section = text.split('for rel in [', 1)[1].split(']:', 1)[0]
    assert 'scripts/test_omc_headless_smoke.py' not in base_section
    assert 'scripts/test_omc_chat_headless_smoke.py' not in base_section
    assert 'scripts/test_omc_setup_smoke.py' not in base_section
