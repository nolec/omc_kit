#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def _default_team_paths() -> list[Path]:
    cwd = Path.cwd()
    project_team = cwd / "prompts" / "team.json"
    if project_team.exists():
        return [project_team.resolve()]
    root = Path(__file__).resolve().parents[1]
    return [(root / "prompts" / "team.json").resolve()]


def _default_base_paths(context_mode: str = "full") -> list[Path]:
    cwd = Path.cwd()
    out: list[Path] = []
    common_name = "PROMPT_COMMON_LEAN.md" if context_mode == "lean" else "PROMPT_COMMON.md"
    common = cwd / common_name
    if not common.exists() and context_mode == "lean":
        common = cwd / "PROMPT_COMMON.md"
    if common.exists():
        out.append(common.resolve())
    else:
        kit_common = Path(__file__).resolve().parents[1] / "templates" / common_name
        if kit_common.exists():
            out.append(kit_common.resolve())
        elif context_mode == "lean":
            kit_common_fb = Path(__file__).resolve().parents[1] / "templates" / "PROMPT_COMMON.md"
            if kit_common_fb.exists():
                out.append(kit_common_fb.resolve())
    project_candidates = sorted(cwd.glob("PROMPT_PROJECT*.md"))
    project = project_candidates[0] if project_candidates else (cwd / "PROMPT_1.md")
    if project.exists():
        out.append(project.resolve())
    return out


def _default_project_overlay_team() -> list[Path]:
    cwd = Path.cwd()
    p = cwd / "project_prompts" / "team.local.json"
    return [p.resolve()] if p.exists() else []


def _load_team(team_path: Path) -> dict:
    raw = team_path.read_text(encoding="utf-8")
    return json.loads(raw)


def _merge_teams(team_paths: list[Path]) -> tuple[dict[str, Path], dict[str, list[str]]]:
    """
    Merge multiple team files (later files override earlier ones by id).

    Returns:
      - roles: role_id -> resolved file path
      - profiles: profile_id -> role_id list
    """
    roles: dict[str, Path] = {}
    profiles: dict[str, list[str]] = {}

    for team_path in team_paths:
        team_path = team_path.resolve()
        team = _load_team(team_path)
        base_dir = team_path.parent

        for r in team.get("roles", []):
            rid = r.get("id")
            p = r.get("path")
            if not rid or not p:
                continue
            rp = Path(p)
            roles[str(rid)] = rp if rp.is_absolute() else (base_dir / rp).resolve()

        for p in team.get("profiles", []):
            pid = p.get("id")
            role_ids = p.get("role_ids")
            if not pid or not isinstance(role_ids, list):
                continue
            profiles[str(pid)] = [str(x) for x in role_ids]

    return roles, profiles


def _role_paths(roles: dict[str, Path], role_ids: list[str]) -> list[Path]:
    unknown = [r for r in role_ids if r not in roles]
    if unknown:
        known = ", ".join(sorted(roles.keys()))
        raise ValueError(f"Unknown role id(s): {unknown}. Known: {known}")
    return [roles[r] for r in role_ids]


def _profile_role_ids(profiles: dict[str, list[str]], profile_id: str) -> list[str]:
    if profile_id in profiles:
        return list(profiles[profile_id])
    known = ", ".join(sorted(profiles.keys()))
    raise ValueError(f"Unknown profile id: {profile_id}. Known: {known}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compose a single prompt from base prompt(s) + optional role prompts."
    )
    ap.add_argument(
        "--base",
        type=Path,
        action="append",
        default=[],
        help="Base prompt markdown file(s) to prepend (can be repeated). If omitted, uses PROMPT_COMMON.md / PROMPT_PROJECT*.md when present.",
    )
    ap.add_argument("--common", type=Path, default=Path("PROMPT_COMMON.md"), help=argparse.SUPPRESS)
    ap.add_argument("--project", type=Path, default=Path("PROMPT_1.md"), help=argparse.SUPPRESS)
    ap.add_argument(
        "--team",
        type=Path,
        action="append",
        default=[],
        help="Team JSON file path (repeatable). Later files override earlier ones by id.",
    )
    ap.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Profile id defined in prompts/team.json (e.g. debugging, trading_pipeline).",
    )
    ap.add_argument(
        "--roles",
        type=str,
        default=None,
        help="Comma-separated role ids defined in prompts/team.json (e.g. analysis,code_review).",
    )
    ap.add_argument(
        "--role",
        type=Path,
        action="append",
        default=[],
        help="Role prompt markdown file (can be repeated).",
    )
    ap.add_argument("--out", type=Path, default=None, help="Write composed prompt to this path (optional).")
    ap.add_argument(
        "--context-mode",
        type=str,
        choices=["full", "lean"],
        default="full",
        help="Context injection mode (e.g. 'lean' to use LEAN base prompts).",
    )
    args = ap.parse_args()

    base_paths: list[Path] = list(args.base) if args.base else _default_base_paths(args.context_mode)
    if not base_paths:
        # Backward compatible fallback if user runs from a non-project cwd.
        if args.common.exists():
            base_paths.append(args.common.resolve())
        if args.project.exists():
            base_paths.append(args.project.resolve())

    role_paths: list[Path] = []
    if args.profile is not None or args.roles is not None:
        team_paths = list(args.team) if args.team else _default_team_paths()
        if not args.team:
            team_paths.extend(_default_project_overlay_team())
        for tp in team_paths:
            if not tp.exists():
                raise FileNotFoundError(tp)
        roles, profiles = _merge_teams(team_paths)
        if args.profile is not None:
            role_ids = _profile_role_ids(profiles, args.profile)
            role_paths.extend(_role_paths(roles, role_ids))
        if args.roles is not None:
            role_ids = [r.strip() for r in args.roles.split(",") if r.strip()]
            role_paths.extend(_role_paths(roles, role_ids))

    parts: list[str] = []
    for p in [*base_paths, *role_paths, *args.role]:
        if not p.exists():
            raise FileNotFoundError(p)
        parts.append(_read_text(p).rstrip())

    composed = "\n\n---\n\n".join(parts).rstrip() + "\n"

    if args.out is None:
        print(composed, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(composed, encoding="utf-8")
        print(f"Wrote: {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
