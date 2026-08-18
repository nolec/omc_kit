#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import stat
import sys
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_plan_candidate_universe as candidate_universe


CONFIG_ENV = "OMC_WORK_CLASS_LOCK_CONFIG_FILE"
REQUIRED_ENV = "OMC_REQUIRE_WORK_CLASS_LOCK"
PRIVATE_KEY_ENV = "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE"
PUBLIC_KEY_ENV = "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY"


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{REQUIRED_ENV} must be a boolean")


def default_config_path(environ: Mapping[str, str] = os.environ) -> Path:
    override = environ.get(CONFIG_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    config_home = environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return (root / "omc" / "work-class-lock.json").resolve()


def _read_config(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("work class lock config is invalid") from error
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or not isinstance(document.get("enabled"), bool)
        or not isinstance(document.get("private_key_file"), str)
        or not isinstance(document.get("trusted_public_key"), str)
    ):
        raise ValueError("work class lock config is invalid")
    return document


def resolve_configuration(
    project_root: str | Path,
    *,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, str] | None:
    env_names = {REQUIRED_ENV, PRIVATE_KEY_ENV, PUBLIC_KEY_ENV}
    present_env_names = {name for name in env_names if name in environ}
    env_mode = bool(present_env_names)
    if env_mode:
        if REQUIRED_ENV not in present_env_names:
            raise ValueError("work class lock environment configuration is incomplete")
        if not _parse_boolean(environ[REQUIRED_ENV]):
            return None
        if present_env_names != env_names:
            raise ValueError("work class lock environment configuration is incomplete")
        key_path = environ.get(PRIVATE_KEY_ENV, "").strip()
        public_key = environ.get(PUBLIC_KEY_ENV, "").strip()
        source = "environment"
    else:
        config_path = default_config_path(environ)
        if not config_path.is_file():
            return None
        document = _read_config(config_path)
        if not document["enabled"]:
            return None
        key_path = str(document["private_key_file"]).strip()
        public_key = str(document["trusted_public_key"]).strip()
        source = "config"
    if not key_path or not public_key:
        raise ValueError("work class lock key configuration is required")
    resolved_key = Path(key_path).expanduser().resolve()
    key_mode = stat.S_IMODE(resolved_key.stat().st_mode)
    if key_mode != 0o600:
        raise ValueError("work class lock private key permissions must be 0600")
    candidate_universe.load_work_class_lock_private_key(
        resolved_key,
        project_root=project_root,
        trusted_public_key=public_key,
    )
    return {
        "private_key_file": str(resolved_key),
        "trusted_public_key": public_key,
        "source": source,
    }


def load_private_key(project_root: str | Path):
    configuration = resolve_configuration(project_root)
    if configuration is None:
        return None
    return candidate_universe.load_work_class_lock_private_key(
        configuration["private_key_file"],
        project_root=project_root,
        trusted_public_key=configuration["trusted_public_key"],
    )


def _exclusive_write(path: Path, content: str) -> None:
    missing_parents: list[Path] = []
    current = path.parent
    while not current.exists():
        missing_parents.append(current)
        current = current.parent
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for created in missing_parents:
        created.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def initialize(project_root: Path, config_path: Path, private_key_path: Path) -> dict[str, object]:
    repository = project_root.resolve()
    resolved_config = config_path.expanduser().resolve()
    resolved_key = private_key_path.expanduser().resolve()
    for path in (resolved_config, resolved_key):
        try:
            path.relative_to(repository)
        except ValueError:
            pass
        else:
            raise ValueError("work class lock custody files must be outside the repository")
        if path.exists():
            raise ValueError(f"work class lock custody file already exists: {path}")

    private_key = Ed25519PrivateKey.generate()
    raw_private_key = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_key = candidate_universe.public_key_text(private_key)
    _exclusive_write(resolved_key, base64.b64encode(raw_private_key).decode("ascii") + "\n")
    document: dict[str, object] = {
        "schema_version": 1,
        "enabled": True,
        "private_key_file": str(resolved_key),
        "trusted_public_key": public_key,
    }
    try:
        _exclusive_write(
            resolved_config,
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    except Exception:
        resolved_key.unlink(missing_ok=True)
        raise
    return document


def preflight(project_root: Path) -> dict[str, str]:
    configuration = resolve_configuration(project_root)
    if configuration is None:
        raise ValueError("work class lock is not enabled")
    return {
        "status": "ready",
        "source": configuration["source"],
        "private_key_file": configuration["private_key_file"],
        "trusted_public_key": configuration["trusted_public_key"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage repository-external work-class lock custody.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="Create a new external signer key and config.")
    init.add_argument("--target", type=Path, default=Path.cwd())
    init.add_argument("--config-file", type=Path)
    init.add_argument("--private-key-file", type=Path)
    preflight_parser = subparsers.add_parser("preflight", help="Validate signer custody without state mutation.")
    preflight_parser.add_argument("--target", type=Path, default=Path.cwd())
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "init":
            config_path = (args.config_file or default_config_path()).expanduser().resolve()
            key_path = (
                args.private_key_file or config_path.with_name("work-class-lock.key")
            ).expanduser().resolve()
            result = initialize(args.target, config_path, key_path)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        print(json.dumps(preflight(args.target), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"[OMC-WORK-CLASS-LOCK] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
