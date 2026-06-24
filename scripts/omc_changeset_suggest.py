#!/usr/bin/env python3

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT_CONFIG_NAMES = {
    ".env.example",
    ".gitignore",
    "Makefile",
    "PROMPT_1.md",
    "requirements.txt",
}

GROUP_ORDER = [
    "root_config",
    "gui",
    "docs",
    "notice_watcher",
    "sixshop_blog",
    "specs",
    "misc",
]

DEFAULT_PROFILE = "marketing"
SUPPORTED_PROFILES = {DEFAULT_PROFILE}


@dataclass(frozen=True)
class StatusEntry:
    status: str
    path: str


def parse_status_text(text: str) -> list[StatusEntry]:
    entries: list[StatusEntry] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        if len(raw_line) < 4:
            raise ValueError(f"invalid status line: {raw_line!r}")
        status = raw_line[:2]
        path = raw_line[3:].strip()
        if not path:
            raise ValueError(f"missing path in status line: {raw_line!r}")
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"')
        entries.append(StatusEntry(status=status, path=path))
    return entries


def classify_path(path: str, profile: str = DEFAULT_PROFILE) -> str:
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported profile: {profile}")
    if path in ROOT_CONFIG_NAMES:
        return "root_config"
    if path.startswith("gui/") or path in {
        "scripts/gui_app.py",
        "scripts/sync_weekly_kpi.py",
        "scripts/apple_style_month_calendar.py",
    }:
        return "gui"
    if path.startswith("docs/"):
        return "docs"
    if path.startswith("notice_watcher/"):
        return "notice_watcher"
    if path.startswith("sixshop_blog/"):
        return "sixshop_blog"
    if path.endswith(".spec") or path.endswith(".spec.py"):
        return "specs"
    return "misc"


def group_entries(
    entries: list[StatusEntry], profile: str = DEFAULT_PROFILE
) -> dict[str, list[StatusEntry]]:
    grouped: dict[str, list[StatusEntry]] = {}
    for entry in entries:
        group = classify_path(entry.path, profile=profile)
        grouped.setdefault(group, []).append(entry)
    return grouped


def build_report(
    entries: list[StatusEntry], profile: str = DEFAULT_PROFILE
) -> dict[str, object]:
    grouped = group_entries(entries, profile=profile)
    summary_counts = {group: len(items) for group, items in grouped.items()}
    ordered_groups = {
        group: [asdict(entry) for entry in grouped[group]]
        for group in GROUP_ORDER
        if group in grouped
    }
    return {
        "summary": {
            "total_entries": len(entries),
            "profile": profile,
            "group_counts": {
                group: summary_counts[group]
                for group in GROUP_ORDER
                if group in summary_counts
            },
        },
        "groups": ordered_groups,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Group git status changes into commit-ready buckets.")
    sub = parser.add_subparsers(dest="command", required=True)

    score = sub.add_parser("score", help="Read git status --short text and emit JSON report.")
    score.add_argument("--input", type=Path, required=True, help="Input text path")
    score.add_argument(
        "--profile",
        choices=sorted(SUPPORTED_PROFILES),
        default=DEFAULT_PROFILE,
        help="Grouping profile",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "score":
        entries = parse_status_text(args.input.read_text(encoding="utf-8"))
        json.dump(
            build_report(entries, profile=args.profile),
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
