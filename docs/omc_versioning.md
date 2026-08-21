# OMC Versioning

## Version source

The source kit root `VERSION` file is the single source of truth for the OMC
release version. It uses stable `MAJOR.MINOR.PATCH` values such as `0.1.0`.
The file is source-only and is never copied to a target repository root.

Installed targets record the release in `.omc/install-receipt.json` together
with the source hash and, when available, the Git revision. The hash detects
changes between releases; the version communicates compatibility.

## Compatibility policy

- `PATCH`: compatible fixes that do not change an installed contract.
- `MINOR`: backward-compatible OMC features or new optional contracts.
- `MAJOR`: breaking setup, receipt, CLI, hook, or machine-output contracts.

Changing source files does not require changing `VERSION` until a release is
cut. Do not reuse a released version for a different release artifact.

## Install receipt migration

New installs write receipt schema v2. Schema v1 remains readable as a legacy
receipt and is upgraded by the next successful setup or conflict-free automatic
update. Unknown schemas and malformed v2 versions fail closed.

## Status model

`omc version` reports three independent dimensions:

- `release_status`: installed release compared with the available source.
- `source_status`: source hash unchanged, modified, unavailable, or invalid.
- `install_integrity`: installed managed files clean, drifted, missing, or invalid.

An unavailable source does not invalidate an otherwise readable installation;
it only prevents an update comparison. `verify-install --strict` retains the
stronger installation and source-freshness gate.

```bash
python3 scripts/omc.py version --target .
python3 scripts/omc.py version --target . --json
python3 scripts/omc.py verify-install --target .
```
