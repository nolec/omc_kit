from __future__ import annotations

from pathlib import Path

from omc_scope import canonical_scope_sha256, canonicalize_child_scopes


def _children(*scope_paths: str) -> list[dict[str, object]]:
    return [
        {
            "child_id": f"child-{index}",
            "depends_on": [],
            "scope_paths": [scope_path],
        }
        for index, scope_path in enumerate(scope_paths, start=1)
    ]


def test_canonicalize_child_scopes_binds_normalized_paths_to_trusted_target(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "api").mkdir(parents=True)

    result = canonicalize_child_scopes(tmp_path, _children("src/api/"))

    assert result["status"] == "ready"
    assert result["reason_code"] == "scope_ready"
    assert result["scope_policy_version"] == "omc-scope/v1"
    assert result["children"][0]["scope_paths"] == ["src/api"]
    assert result["children"][0]["scope_hash"] == canonical_scope_sha256(
        ["src/api"]
    )
    assert isinstance(result["target_identity_sha256"], str)


def test_canonicalize_child_scopes_rejects_symlinked_existing_prefix(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)

    result = canonicalize_child_scopes(tmp_path, _children("linked/new-file.py"))

    assert result == {
        "status": "blocked",
        "reason_code": "scope_symlink_forbidden",
        "execution_allowed": False,
    }


def test_canonicalize_child_scopes_rejects_symlink_in_trusted_target(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    result = canonicalize_child_scopes(linked, _children("src/api"))

    assert result["reason_code"] == "scope_target_invalid"


def test_canonicalize_child_scopes_rejects_escape_and_glob(tmp_path: Path) -> None:
    escape = canonicalize_child_scopes(tmp_path, _children("../outside"))
    glob = canonicalize_child_scopes(tmp_path, _children("src/**/*.py"))

    assert escape["reason_code"] == "scope_path_invalid"
    assert glob["reason_code"] == "scope_glob_forbidden"


def test_canonicalize_child_scopes_rejects_casefold_and_unicode_aliases(
    tmp_path: Path,
) -> None:
    case_alias = canonicalize_child_scopes(
        tmp_path,
        _children("src/API", "src/api"),
    )
    unicode_alias = canonicalize_child_scopes(
        tmp_path,
        _children("docs/caf\u00e9", "docs/cafe\u0301"),
    )

    assert case_alias["reason_code"] == "scope_case_collision"
    assert unicode_alias["reason_code"] == "scope_case_collision"


def test_canonicalize_child_scopes_rejects_parent_child_overlap(
    tmp_path: Path,
) -> None:
    result = canonicalize_child_scopes(
        tmp_path,
        _children("src/api", "src/api/routes"),
    )

    assert result["reason_code"] == "scope_overlap"
