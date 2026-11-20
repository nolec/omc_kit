from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_context
import omc_lesson


def _add_command(root: Path, rule: str) -> list[str]:
    return [sys.executable, "-B", omc_lesson.__file__, "--target", str(root),
            "add", "--title", "Invoice delivery", "--symptom", "Delivery failed",
            "--rule", rule, "--verify", "Verify delivery"]


def test_add_reports_existing_filename_and_preserves_contents(tmp_path):
    first = subprocess.run(_add_command(tmp_path, "first rule"), capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    folder = tmp_path / ".omc/lessons"
    original, = folder.iterdir()
    before = original.read_bytes(), original.stat().st_mtime_ns

    second = subprocess.run(_add_command(tmp_path, "second rule"), capture_output=True, text=True)

    assert second.returncode == 1
    assert original.name in second.stderr
    assert list(folder.iterdir()) == [original]
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before


def test_simultaneous_add_publishes_only_one_complete_lesson(tmp_path):
    rules = ("first concurrent rule", "second concurrent rule")
    with subprocess.Popen(_add_command(tmp_path, rules[0]),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as first, \
         subprocess.Popen(_add_command(tmp_path, rules[1]),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as second:
        first_output = first.communicate(timeout=10)
        second_output = second.communicate(timeout=10)

    assert sorted((first.returncode, second.returncode)) == [0, 1]
    winner = first.returncode == 0
    original, = (tmp_path / ".omc/lessons").iterdir()
    loser_stderr = second_output[1] if winner else first_output[1]
    winning_rule, losing_rule = rules if winner else rules[::-1]
    assert original.name in loser_stderr
    content = original.read_text(encoding="utf-8")
    assert f"## 적용된 규칙\n{winning_rule}\n" in content
    assert losing_rule not in content
    assert content.endswith("## 검증 커맨드\nVerify delivery\n")


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


def test_doctor_checks_multiple_replacements_without_writes(tmp_path):
    folder = tmp_path / ".omc/lessons"
    folder.mkdir(parents=True)
    for name in ("old", "middle", "live", "deleted", "missing-link", "broken", "standalone"):
        (folder / f"{name}.md").write_text(f"# {name}\n")
    assert omc_lesson.cmd_retract(tmp_path, "old.md", "outdated", "middle.md") == 0
    assert omc_lesson.cmd_retract(tmp_path, "middle.md", "outdated", "live.md") == 0
    assert omc_lesson.cmd_retract(tmp_path, "missing-link.md", "outdated", "deleted.md") == 0
    assert omc_lesson.cmd_retract(tmp_path, "standalone.md", "outdated", None) == 0
    (folder / "deleted.md").unlink()
    (folder / "broken.md.retracted.json").write_text("{broken")

    def snapshot():
        return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in folder.iterdir()}

    before = snapshot()
    result = subprocess.run(
        [sys.executable, "-B", omc_lesson.__file__, "--target", str(tmp_path), "doctor"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1, result.stderr
    for line in ("old.md → middle.md: 다시 철회됨", "middle.md → live.md: 활성",
                 "missing-link.md → deleted.md: 파일 없음", "broken.md: 확인 불가",
                 "대체 지정 없음 1"):
        assert line in result.stdout
    assert snapshot() == before


def test_doctor_treats_missing_lessons_directory_as_empty(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", omc_lesson.__file__, "--target", str(tmp_path), "doctor"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "철회 기록 0개" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_doctor_reports_orphan_and_refuses_symlink_directory(tmp_path, capsys):
    folder = tmp_path / ".omc/lessons"
    folder.mkdir(parents=True)
    marker = folder / "missing.md.retracted.json"
    marker.write_text('{"reason":"old","replacement":null}')
    assert omc_lesson.cmd_doctor(tmp_path) == 1
    assert "원본 교훈 확인 필요 1" in capsys.readouterr().out
    elsewhere = tmp_path / "other"
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)
    assert omc_lesson.cmd_doctor(tmp_path) == 2
    assert "진단 불가" in capsys.readouterr().err
    assert (elsewhere / marker.name).read_text() == '{"reason":"old","replacement":null}'


def test_doctor_filters_exact_replacement_and_reports_incomplete_results(tmp_path):
    folder = tmp_path / ".omc/lessons"
    folder.mkdir(parents=True)
    for name in ("old-a", "old-b", "unrelated", "current", "other-current"):
        (folder / f"{name}.md").write_text(f"# {name}\n")
    for source, target in (("old-a", "current"), ("old-b", "current"),
                           ("unrelated", "other-current")):
        assert omc_lesson.cmd_retract(tmp_path, source + ".md", "old", target + ".md") == 0
    command = [sys.executable, "-B", omc_lesson.__file__, "--target", str(tmp_path),
               "doctor", "--replacement"]

    def snapshot():
        return {p: (p.lstat().st_mtime_ns, p.readlink() if p.is_symlink() else p.read_bytes())
                for p in folder.iterdir()}

    def query(filename):
        before = snapshot()
        result = subprocess.run(command + [filename], capture_output=True, text=True)
        assert snapshot() == before
        return result

    active = query("current.md")
    assert active.returncode == 0
    assert "old-a.md → current.md: 활성" in active.stdout

    (folder / "current.md").unlink()
    result = query("current.md")
    assert result.returncode == 1, result.stderr
    assert "일치 기록 2개" in result.stdout
    assert "old-a.md → current.md: 파일 없음" in result.stdout
    assert "old-b.md → current.md: 파일 없음" in result.stdout
    assert "unrelated.md" not in result.stdout
    assert query("../current.md").returncode == 2
    empty = query("unused.md")
    assert empty.returncode == 0 and "일치 기록 0개" in empty.stdout

    (folder / "broken.md.retracted.json").write_text("{broken")
    (folder / "linked.md.retracted.json").symlink_to("unrelated.md.retracted.json")
    partial = query("current.md")
    assert partial.returncode == 1 and "일치 기록 2개" in partial.stdout
    assert "broken.md: 철회 기록 읽기 실패" in partial.stdout
    assert "linked.md: 철회 기록 읽기 실패" in partial.stdout
    assert "조회 불완전: 철회 기록 2개" in partial.stdout
    no_known_matches = query("unused.md")
    assert no_known_matches.returncode == 1
    assert "일치 기록 0개" in no_known_matches.stdout and "조회 불완전" in no_known_matches.stdout
