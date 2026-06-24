from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "omc_changeset_suggest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("omc_changeset_suggest", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_status_lines_and_group_marketing_changes():
    mod = _load_module()

    status_text = "\n".join(
        [
            " M .env.example",
            " M Makefile",
            " D scripts/gui_app.py",
            "?? .gitignore",
            "?? WeeklyKPI-arm64.spec",
            "?? docs/ONBOARDING.md",
            "?? gui/gui_app.py",
            "?? notice_watcher/watch_notice_slack.py",
            "?? sixshop_blog/blog_pipeline.py",
            "?? scripts/autopilot.py",
        ]
    )

    entries = mod.parse_status_text(status_text)
    grouped = mod.group_entries(entries)

    assert [entry.path for entry in grouped["root_config"]] == [
        ".env.example",
        "Makefile",
        ".gitignore",
    ]
    assert [entry.path for entry in grouped["gui"]] == [
        "scripts/gui_app.py",
        "gui/gui_app.py",
    ]
    assert [entry.path for entry in grouped["docs"]] == ["docs/ONBOARDING.md"]
    assert [entry.path for entry in grouped["notice_watcher"]] == [
        "notice_watcher/watch_notice_slack.py"
    ]
    assert [entry.path for entry in grouped["sixshop_blog"]] == [
        "sixshop_blog/blog_pipeline.py"
    ]
    assert [entry.path for entry in grouped["specs"]] == ["WeeklyKPI-arm64.spec"]
    assert [entry.path for entry in grouped["misc"]] == ["scripts/autopilot.py"]


def test_build_report_counts_entries_per_group():
    mod = _load_module()

    entries = mod.parse_status_text(
        "\n".join(
            [
                " M .env.example",
                "?? gui/gui_app.py",
                "?? gui/test_gui_app.py",
                "?? docs/ONBOARDING.md",
            ]
        )
    )

    report = mod.build_report(entries)

    assert report["summary"]["total_entries"] == 4
    assert report["summary"]["group_counts"] == {
        "root_config": 1,
        "gui": 2,
        "docs": 1,
    }


def test_parse_status_text_handles_quoted_paths_and_renames():
    mod = _load_module()

    entries = mod.parse_status_text(
        "\n".join(
            [
                '?? "sixshop_blog/assets/example image.png"',
                "R  old/path.py -> notice_watcher/watch_notice_slack.py",
            ]
        )
    )

    assert [entry.path for entry in entries] == [
        "sixshop_blog/assets/example image.png",
        "notice_watcher/watch_notice_slack.py",
    ]
    grouped = mod.group_entries(entries)
    assert [entry.path for entry in grouped["sixshop_blog"]] == [
        "sixshop_blog/assets/example image.png"
    ]
    assert [entry.path for entry in grouped["notice_watcher"]] == [
        "notice_watcher/watch_notice_slack.py"
    ]


def test_marketing_profile_is_default_and_can_be_selected_explicitly(tmp_path: Path):
    status_file = tmp_path / "status.txt"
    status_file.write_text("?? gui/gui_app.py\n", encoding="utf-8")

    implicit = subprocess.run(
        [sys.executable, str(SCRIPT), "score", "--input", str(status_file)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    explicit = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "score",
            "--input",
            str(status_file),
            "--profile",
            "marketing",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )

    assert implicit.returncode == 0, implicit.stderr
    assert explicit.returncode == 0, explicit.stderr
    assert json.loads(implicit.stdout) == json.loads(explicit.stdout)


def test_cli_emits_json_report(tmp_path: Path):
    status_file = tmp_path / "status.txt"
    status_file.write_text(
        "\n".join(
            [
                " M .env.example",
                "?? notice_watcher/README.md",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "score", "--input", str(status_file)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["total_entries"] == 2
    assert payload["summary"]["group_counts"] == {
        "root_config": 1,
        "notice_watcher": 1,
    }
