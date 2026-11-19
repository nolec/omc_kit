from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_context
import omc_lesson


def test_withdrawn_lesson_preserves_source_and_stays_out_of_recall(tmp_path, capsys):
    folder = tmp_path / ".omc/lessons"
    folder.mkdir(parents=True)
    old = folder / "z-old.md"
    old.write_text("# Old invoice rule\n## 적용된 규칙\nOLD invoice export\n")
    new = folder / "a-new.md"
    new.write_text("# New invoice rule\n## 적용된 규칙\nNEW invoice export\n")
    original = old.read_bytes()
    args = [sys.executable, "-B", omc_lesson.__file__, "--target", str(tmp_path)]
    command = args + ["retract", old.name, "--reason", "Policy changed", "--replacement", new.name]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert old.read_bytes() == original
    assert subprocess.run(command, capture_output=True).returncode != 0
    shown = subprocess.check_output(args + ["show", old.name], text=True)
    assert all(s in shown for s in ("OLD invoice export", "Policy changed", new.name))
    assert omc_lesson.search_relevant(tmp_path, "invoice export") == [
        ("New invoice rule", "NEW invoice export")
    ]
    for output in [
        subprocess.check_output(args + ["search", "invoice export"], text=True),
        subprocess.check_output(args + ["recent"], text=True),
        omc_context._collect_lessons(tmp_path, {"branch": "invoice export"}),
        omc_context._collect_lessons(tmp_path, {"branch": "nohits"}),
    ]:
        assert "New invoice rule" in output and "Old invoice rule" not in output
    omc_context._print_lessons_to_console(tmp_path, {"branch": "nohits"})
    assert "OLD invoice export" not in capsys.readouterr().out
    omc_context.write_lessons_inject(tmp_path)
    injected = tmp_path / ".cursor/rules/omc-lessons-inject.mdc"
    assert "NEW invoice export" in injected.read_text() and "OLD invoice export" not in injected.read_text()


def test_retract_rejects_invalid_replacement_and_never_overwrites_source(tmp_path):
    folder = tmp_path / ".omc/lessons"
    folder.mkdir(parents=True)
    old = folder / "old.md"
    old.write_text("# Original\n")
    for replacement in ("missing.md", "old.md", "../out.md"):
        assert omc_lesson.cmd_retract(tmp_path, old.name, "obsolete", replacement) == 1
    assert list(folder.iterdir()) == [old]
    assert old.read_text() == "# Original\n"
    assert omc_lesson.cmd_retract(tmp_path, old.name, "obsolete", None) == 0
    assert omc_context._collect_lessons(tmp_path, {"branch": "original"}) == ""


def test_show_reports_current_replacement_state_read_only(tmp_path):
    folder = tmp_path / ".omc/lessons"
    folder.mkdir(parents=True)
    original = folder / "old.md"
    replacement = folder / "current.md"
    original.write_text("# Old invoice rule\n")
    replacement.write_text("# New invoice rule\n")
    args = [sys.executable, "-B", omc_lesson.__file__, "--target", str(tmp_path)]
    subprocess.run(args + ["retract", original.name, "--reason", "Policy changed",
                           "--replacement", replacement.name], check=True)

    def show(expected):
        def snapshot():
            return {p: (p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                    for p in folder.iterdir()}

        before = snapshot()
        result = subprocess.check_output(args + ["show", original.name], text=True)
        assert f"대체 교훈 상태: {expected}" in result
        assert "대체 교훈: current.md" in result and "# Old invoice rule" in result
        assert snapshot() == before

    show("활성")
    subprocess.run(args + ["retract", replacement.name, "--reason", "Policy changed again"], check=True)
    show("다시 철회됨")
    replacement.with_suffix(".md.retracted.json").write_text("{broken")
    show("확인 불가")
    replacement.unlink()
    show("파일 없음")
    replacement.symlink_to(original)
    show("확인 불가")
