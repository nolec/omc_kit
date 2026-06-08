#!/usr/bin/env python3
"""
omc_context.py — 세션 시작 시 프로젝트 컨텍스트 자동 수집 + 자동 confirm

세션 시작(session_start 훅)에서 호출되어 .omc/context.md 에 저장합니다.
직전 세션이 confirmed 상태인 fresh 세션은 자동으로 confirm 처리됩니다.
  → AI가 "python3 scripts/omc.py state confirm" 을 매번 요구하지 않습니다.
  → ship/git commit 전에만 TDD 게이트가 동작하는 기존 정책은 유지됩니다.

사용:
  python3 scripts/omc_context.py [--target .]
  python3 scripts/omc_context.py --print
  python3 scripts/omc_context.py --no-auto-confirm   # 자동 confirm 비활성화
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime
from pathlib import Path

def _run(cmd, cwd, fallback=""):
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else fallback
    except Exception:
        return fallback

def _collect_git_info(root):
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root, "(unknown)")
    commits = _run(["git", "log", "--oneline", "-7", "--no-merges"], root, "(없음)")
    status = _run(["git", "status", "-sb"], root, "(없음)")
    diff = _run(["git", "diff", "--name-only", "--diff-filter=ACM", "origin/main...HEAD"], root, "")
    if not diff:
        diff = _run(["git", "diff", "--name-only", "--diff-filter=ACM", "HEAD~3...HEAD"], root, "(없음)")
    return {"branch": branch, "commits": commits, "status": status, "diff": diff}

def _collect_project_info(root):
    info = {}
    pkg = root / "package.json"
    if pkg.exists():
        try:
            d = json.loads(pkg.read_text(encoding="utf-8"))
            info["name"] = d.get("name", "")
            info["test"] = d.get("scripts", {}).get("test", "")
            info["scripts"] = list(d.get("scripts", {}).keys())
            info["nx"] = (root / "nx.json").exists()
        except Exception:
            pass
    return info

def _collect_coverage(root):
    cov = root / "coverage" / "coverage-summary.json"
    if cov.exists():
        try:
            d = json.loads(cov.read_text(encoding="utf-8"))
            pct = d.get("total", {}).get("lines", {}).get("pct", "?")
            return f"라인 커버리지: {pct}%"
        except Exception:
            return "(coverage-summary.json 파싱 실패)"
    return "(커버리지 없음 — npx jest --coverage 실행 후 생성)"

def _collect_notepad(root):
    p = root / ".omc" / "notepad.md"
    if p.exists():
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        return "\n".join(lines[:20]) + ("\n..." if len(lines) > 20 else "")
    return "(notepad.md 없음)"

def _collect_wip(root) -> str:
    """저장된 WIP 컨텍스트를 읽어 마크다운 섹션으로 반환합니다."""
    import json as _json
    import os as _os
    env_path = _os.environ.get("OMC_WIP_PATH")
    wip_path = Path(env_path) if env_path else root / ".omc" / "wip" / "latest.json"
    if not wip_path.exists():
        return ""
    try:
        data = _json.loads(wip_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    lines = ["## WIP 이전 작업 컨텍스트"]
    saved_at = data.get("saved_at", "")
    branch = data.get("branch", "")
    if saved_at:
        lines.append(f"저장 시각: {saved_at[:19]}  브랜치: {branch}")
    decisions = data.get("decisions", "").strip()
    remaining = data.get("remaining", "").strip()
    failed = data.get("failed_approaches", "").strip()
    if decisions:
        lines.append(f"\n결정 사항:\n  {decisions}")
    if remaining:
        lines.append(f"\n남은 작업:\n  {remaining}")
    if failed:
        lines.append(f"\n실패한 접근:\n  {failed}")
    return "\n".join(lines)

def _collect_lessons(root, git_info: dict | None = None, n: int = 5) -> str:
    """BM25로 현재 컨텍스트와 관련된 교훈을 추천합니다.

    git_info(브랜치명 + 최근 커밋)를 쿼리로 사용해 단순 최신순 대신
    현재 작업과 의미적으로 유사한 교훈을 우선 표시합니다.
    BM25 히트가 없으면 최신순 n개 fallback.
    """
    lessons_dir = root / ".omc" / "lessons"
    if not lessons_dir.exists():
        return ""
    files = sorted(lessons_dir.glob("*.md"), reverse=True)
    if not files:
        return ""

    # BM25 검색 시도 (omc_lesson.search_relevant 재사용)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import omc_lesson as _lesson_mod

        context_query = ""
        if git_info:
            context_query = " ".join(filter(None, [
                git_info.get("branch", ""),
                git_info.get("commits", ""),
            ]))

        if context_query.strip():
            relevant = _lesson_mod.search_relevant(root, context_query, top_n=n)
            if relevant:
                parts = ["## 관련 교훈 (BM25 — 현재 작업 기준)"]
                for title, rule in relevant:
                    parts.append(f"\n### {title}")
                    parts.append(f"규칙: {rule}")
                return "\n".join(parts)
    except Exception:
        pass

    # Fallback: 최신순 n개
    parts = [f"## 최근 교훈 (최대 {n}개)"]
    for f in files[:n]:
        lines = f.read_text(encoding="utf-8").splitlines()
        title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), f.stem)
        rule_idx = next((i for i, l in enumerate(lines) if l.startswith("## 적용된 규칙")), None)
        rule = lines[rule_idx + 1].strip() if rule_idx is not None and rule_idx + 1 < len(lines) else "(없음)"
        tags_line = next((l for l in lines if l.startswith("태그:")), "")
        parts.append(f"\n### {title}")
        if tags_line:
            parts.append(tags_line)
        parts.append(f"규칙: {rule}")
    return "\n".join(parts)


def write_lessons_inject(root: Path, top_n: int = 3) -> None:
    """BM25 top_n 교훈을 .cursor/rules/omc-lessons-inject.mdc 에 기록합니다.

    Cursor는 .cursor/rules/ 파일을 자동으로 컨텍스트에 포함하므로
    이 파일을 통해 교훈이 실질적으로 주입됩니다.
    교훈이 0개이면 파일을 생성하지 않습니다.
    """
    lessons_dir = root / ".omc" / "lessons"
    out_path = (root / ".cursor" / "rules" / "omc-lessons-inject.mdc")
    if not lessons_dir.exists():
        out_path.unlink(missing_ok=True)
        return
    files = sorted(lessons_dir.glob("*.md"), reverse=True)
    if not files:
        out_path.unlink(missing_ok=True)
        return

    # BM25 또는 최신순 top_n 수집
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import omc_lesson as _lesson_mod
        git_info = _collect_git_info(root)
        context_query = " ".join(filter(None, [
            git_info.get("branch", ""),
            git_info.get("commits", ""),
        ]))
        if context_query.strip():
            relevant = _lesson_mod.search_relevant(root, context_query, top_n=top_n)
        else:
            relevant = []
    except Exception:
        relevant = []

    parts = ["# OMC 자동 주입 교훈 (BM25 top-{})".format(top_n), ""]
    parts.append("> 이 파일은 세션 시작 시 자동 생성됩니다. 직접 수정하지 마세요.")
    parts.append("")

    if relevant:
        for title, rule in relevant:
            parts.append(f"## {title}")
            parts.append(f"- {rule}")
            parts.append("")
    else:
        # BM25 히트 없으면 최신순 top_n
        for f in files[:top_n]:
            lines = f.read_text(encoding="utf-8").splitlines()
            title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), f.stem)
            rule_idx = next((i for i, l in enumerate(lines) if l.startswith("## 적용된 규칙")), None)
            rule = lines[rule_idx + 1].strip() if rule_idx is not None and rule_idx + 1 < len(lines) else "(없음)"
            parts.append(f"## {title}")
            parts.append(f"- {rule}")
            parts.append("")

    rules_dir = root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    out_path = rules_dir / "omc-lessons-inject.mdc"
    out_path.write_text("\n".join(parts), encoding="utf-8")

def _print_lessons_to_console(root: Path, git_info: dict | None = None, n: int = 3) -> None:
    """세션 시작 시 관련 교훈을 터미널에 직접 출력합니다.

    BM25 히트가 있으면 현재 작업과 관련된 교훈을,
    없으면 최신 n개를 fallback으로 출력합니다.
    교훈이 없으면 아무것도 출력하지 않습니다.
    """
    lessons_dir = root / ".omc" / "lessons"
    if not lessons_dir.exists():
        return
    files = sorted(lessons_dir.glob("*.md"), reverse=True)
    if not files:
        return

    entries: list[tuple[str, str]] = []  # (title, rule)

    # BM25 검색 시도
    try:
        import omc_lesson as _lesson_mod

        context_query = ""
        if git_info:
            context_query = " ".join(filter(None, [
                git_info.get("branch", ""),
                git_info.get("commits", ""),
            ]))

        if context_query.strip():
            relevant = _lesson_mod.search_relevant(root, context_query, top_n=n)
            if relevant:
                entries = relevant
    except Exception:
        pass

    # Fallback: 최신순
    if not entries:
        for f in files[:n]:
            lines = f.read_text(encoding="utf-8").splitlines()
            title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), f.stem)
            rule_idx = next((i for i, l in enumerate(lines) if l.startswith("## 적용된 규칙")), None)
            rule = lines[rule_idx + 1].strip() if rule_idx is not None and rule_idx + 1 < len(lines) else "(없음)"
            entries.append((title, rule))

    count = len(entries)
    print(f"[CONTEXT] 💡 관련 교훈 {count}개")
    for title, rule in entries:
        print(f"          ▸ {title}")
        if rule and rule != "(없음)":
            rule_preview = rule[:80] + ("…" if len(rule) > 80 else "")
            print(f"            규칙: {rule_preview}")

_INDEX_LIMIT = 300
_MAX_FILE_BYTES = 500 * 1024
_EXCLUDE_DIRS = {
    "node_modules", ".git", "dist", ".next", ".nuxt", "build",
    "coverage", "__pycache__", ".turbo", ".cache", "out", ".omc",
}
_SOURCE_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".go", ".java", ".rb", ".rs", ".swift", ".kt",
}
_FALLBACK_SRC_DIRS = ["src", "app", "lib", "packages", "scripts"]


def _collect_codebase_index(root: Path) -> str:
    """코드베이스 파일 트리를 수집해 .omc/context/file_index.txt 에 저장하고 요약 문자열을 반환한다.

    1. git ls-files 로 파일 목록 수집 (gitignore 자동 적용)
    2. git 없으면 _FALLBACK_SRC_DIRS glob fallback
    3. _EXCLUDE_DIRS, 500KB 초과 파일 제외
    4. 300개 상한 초과 시 상위 300개 + 생략 표시
    5. 소스 파일 없으면 빈 문자열 반환
    """
    root = Path(root)

    # 1. git ls-files 시도
    git_ok = False
    all_files: list[str] = []
    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        all_files = [p.strip() for p in out.splitlines() if p.strip()]
        git_ok = True
    except Exception:
        pass

    # 2. fallback: 소스 디렉토리 glob (git 자체가 실패한 경우만)
    if not git_ok:
        for d in _FALLBACK_SRC_DIRS:
            src_dir = root / d
            if src_dir.is_dir():
                for p in src_dir.rglob("*"):
                    parts_check = p.parts
                    if any(part in _EXCLUDE_DIRS for part in parts_check):
                        continue
                    if p.is_file():
                        try:
                            rel = str(p.relative_to(root))
                            all_files.append(rel)
                        except ValueError:
                            pass

    # 3. 필터: 제외 디렉토리 + 확장자 + 파일 크기
    filtered: list[str] = []
    for rel in sorted(all_files):
        parts = Path(rel).parts
        if any(part in _EXCLUDE_DIRS for part in parts):
            continue
        if Path(rel).suffix not in _SOURCE_EXTS:
            continue
        abs_path = root / rel
        try:
            if abs_path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        filtered.append(rel)

    if not filtered:
        return ""

    # 4. 상한 처리
    truncated = False
    if len(filtered) > _INDEX_LIMIT:
        filtered = filtered[:_INDEX_LIMIT]
        truncated = True

    # 5. 파일 저장
    ctx_dir = root / ".omc" / "context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    index_file = ctx_dir / "file_index.txt"

    lines = [
        f"파일 수: {len(filtered)}{'+ (300개 상한)' if truncated else ''}",
        f"수집 시각: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
        "",
    ] + filtered
    if truncated:
        lines.append("... (이하 생략 — 300개 상한 초과)")

    index_file.write_text("\n".join(lines), encoding="utf-8")
    summary = f"{len(filtered)}개 파일{'(+생략)' if truncated else ''}"
    return summary


def build_context(root) -> tuple[str, dict]:
    """컨텍스트 마크다운 문자열과 git_info dict를 반환한다.

    git_info를 함께 반환해 호출자가 중복 git 호출 없이 재활용하도록 한다.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    g = _collect_git_info(root)
    proj = _collect_project_info(root)
    cov = _collect_coverage(root)
    note = _collect_notepad(root)
    lessons = _collect_lessons(root, git_info=g)
    wip = _collect_wip(root)
    index_summary = _collect_codebase_index(Path(root))

    parts = [
        f"# OMC 세션 컨텍스트 — {now}", "",
        "## 브랜치 & 상태",
        f"현재 브랜치: `{g['branch']}`", "",
        "```", g["status"], "```", "",
        "## 최근 커밋 (최대 7개)",
        "```", g["commits"], "```", "",
        "## origin/main 대비 변경 파일",
        "```", g["diff"] or "(없음)", "```", "",
        "## 테스트 커버리지", cov, "",
    ]
    if proj:
        parts += ["## 프로젝트 정보"]
        if proj.get("name"):
            parts.append(f"이름: {proj['name']}")
        if proj.get("test"):
            parts.append(f"테스트 커맨드: `{proj['test']}`")
        if proj.get("nx"):
            parts.append("빌드 시스템: Nx monorepo")
        if proj.get("scripts"):
            parts.append(f"npm scripts: {', '.join(proj['scripts'][:10])}")
        parts.append("")
    if lessons:
        parts += [lessons, ""]
    if wip:
        parts += [wip, ""]
    if index_summary:
        parts += [f"## [코드베이스] {index_summary}", "(전체 목록: .omc/context/file_index.txt)", ""]
    parts += [
        "## OMC Notepad (이전 세션 메모)", note, "",
        "---",
        "> 이 파일은 세션 시작 시 자동 생성됩니다. 수동 편집하지 마세요.",
    ]
    return "\n".join(parts), g

def main():
    ap = argparse.ArgumentParser(description="OMC 세션 컨텍스트 자동 수집")
    ap.add_argument("--target", type=Path, default=Path.cwd())
    ap.add_argument("--print", action="store_true", dest="print_only")
    ap.add_argument("--no-auto-confirm", action="store_true", dest="no_auto_confirm",
                    help="자동 confirm 비활성화 (수동 confirm 필요)")
    args = ap.parse_args()
    root = args.target.resolve()
    ctx, g = build_context(root)
    if args.print_only:
        print(ctx)
        return 0
    out = root / ".omc" / "context.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ctx, encoding="utf-8")
    print(f"[CONTEXT] ✅ 컨텍스트 저장: {out.relative_to(root)}")
    print(f"[CONTEXT] 브랜치: {g['branch']}")
    changed = [l for l in g["diff"].splitlines() if l.strip()]
    if changed:
        print(f"[CONTEXT] 변경 파일: {len(changed)}개")
        for f in changed[:5]:
            print(f"          {f}")
        if len(changed) > 5:
            print(f"          ... 외 {len(changed) - 5}개")
    else:
        print("[CONTEXT] 변경 파일: 없음")

    # ── 관련 교훈 콘솔 출력 ──────────────────────────────────────────────────────
    _print_lessons_to_console(root, git_info=g)
    write_lessons_inject(root, top_n=3)

    # ── 새 세션 시작: contract_confirmed 초기화 ───────────────────────────────
    try:
        import omc_pipeline_guard as _guard
        _guard.cmd_session_start(root)
    except Exception:
        pass

    # ── 자동 confirm ─────────────────────────────────────────────────────────
    # 직전 세션이 이미 confirmed 이거나 세션이 없는 경우에만 자동 처리합니다.
    # 미확정(pending) 세션이 남아 있으면 자동 confirm 을 건너뜁니다.
    if not args.no_auto_confirm:
        _try_auto_confirm(root)

    return 0

def _try_auto_confirm(root: Path) -> None:
    """fresh 세션이고 직전 세션이 confirmed 상태면 자동으로 confirm 합니다.

    자동 confirm 조건:
    - latest_session_id 가 존재하고
    - latest_session_id != latest_confirmed_session_id (아직 미확정)
    - latest_confirmation.status 가 "none" 이거나 "pending" 인 경우
      (AI가 작업 중 중단된 "blocked" 상태는 자동 confirm 하지 않습니다)
    """
    latest_path = root / ".omc" / "state" / "latest.json"
    if not latest_path.exists():
        return
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        return

    session_id = latest.get("latest_session_id")
    confirmed_id = latest.get("latest_confirmed_session_id")

    if not session_id:
        return

    if session_id == confirmed_id:
        # 이미 confirmed
        return

    # "blocked" 상태는 사용자가 의도적으로 확인이 필요한 상태 — 자동 confirm 금지
    confirmation_status = latest.get("latest_confirmation", {}).get("status", "none")
    if confirmation_status == "blocked":
        print(f"[CONTEXT] ⚠️  세션 blocked 상태 — 자동 confirm 건너뜀")
        print(f"           수동 확인: python3 scripts/omc.py state status --target .")
        return

    scripts_dir = root / "scripts"
    state_script = scripts_dir / "omc_state.py"
    if not state_script.exists():
        state_script = Path(__file__).parent / "omc_state.py"
    if not state_script.exists():
        return

    result = subprocess.run(
        [sys.executable, str(state_script), "confirm", "--target", str(root)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("[CONTEXT] ✅ 세션 자동 confirm 완료 (fresh session)")
    else:
        print(f"[CONTEXT] ⚠️  자동 confirm 실패 (수동 실행 필요): python3 scripts/omc.py state confirm --target .")

if __name__ == "__main__":
    raise SystemExit(main())
