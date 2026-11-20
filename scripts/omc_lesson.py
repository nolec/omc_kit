#!/usr/bin/env python3
"""
omc_lesson.py — Compound Engineering 교훈 관리

작업 완료 후 반복하지 말아야 할 것을 작은 파일로 기록합니다.
전역 지침 파일(AGENTS.md)에 추가하지 않고 .omc/lessons/ 에 분리 저장합니다.

사용:
  python3 scripts/omc_lesson.py add --title "설명" [--target .]
  python3 scripts/omc_lesson.py list [--tag TAG] [--target .]
  python3 scripts/omc_lesson.py search QUERY [--top N] [--target .]
  python3 scripts/omc_lesson.py show FILENAME [--target .]
  python3 scripts/omc_lesson.py recent [--n 5] [--target .]
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import math
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_LESSONS_DIR = ".omc/lessons"
_TEMPLATE = """\
# {title}
날짜: {date}
태그: {tags}

## 증상
{symptom}

## 원인
{cause}

## 적용된 규칙
{rule}

## 검증 커맨드
{verify}
"""


# ---------------------------------------------------------------------------
# 파일명 생성
# ---------------------------------------------------------------------------

def _slug(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return s[:50].strip("-")


def _lesson_path(root: Path, title: str) -> Path:
    date = datetime.now().strftime("%Y-%m-%d")
    slug = _slug(title)
    lessons_dir = root / _LESSONS_DIR
    lessons_dir.mkdir(parents=True, exist_ok=True)
    return lessons_dir / f"{date}-{slug}.md"


# ---------------------------------------------------------------------------
# BM25 (순수 stdlib — 외부 패키지 없음)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """한글·영문·숫자를 단어 단위로 분리합니다."""
    text = text.lower()
    tokens = re.findall(r"[가-힣]+|[a-z0-9_]+", text)
    return [t for t in tokens if len(t) > 1]


def _bm25_scores(
    query: str,
    docs: list[tuple[Path, str]],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[tuple[float, Path, str]]:
    """BM25 점수를 계산해 (score, path, content) 리스트를 반환합니다.

    Args:
        k1: 단어 빈도 포화도 조절 (1.2~2.0 권장)
        b:  문서 길이 정규화 강도 (0~1, 0.75 표준)
    """
    if not docs:
        return []

    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    doc_tokens = [_tokenize(content) for _, content in docs]
    avg_dl = sum(len(t) for t in doc_tokens) / len(doc_tokens) or 1

    N = len(docs)
    df: dict[str, int] = Counter()
    for tokens in doc_tokens:
        for t in set(tokens):
            df[t] += 1

    def idf(term: str) -> float:
        n = df.get(term, 0)
        return math.log((N - n + 0.5) / (n + 0.5) + 1)

    results = []
    for (path, content), tokens in zip(docs, doc_tokens):
        tf = Counter(tokens)
        dl = len(tokens) or 1
        score = 0.0
        for term in q_tokens:
            f = tf.get(term, 0)
            idf_val = idf(term)
            score += idf_val * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avg_dl))
        results.append((score, path, content))

    return sorted(results, key=lambda x: x[0], reverse=True)


def _snippet(content: str, query_tokens: list[str], max_len: int = 120) -> str:
    """쿼리 단어가 등장하는 주변 텍스트를 에스니핏으로 반환합니다."""
    lines = content.splitlines()
    for line in lines:
        line_low = line.lower()
        if any(t in line_low for t in query_tokens):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and len(stripped) > 3:
                return stripped[:max_len] + ("…" if len(stripped) > max_len else "")
    return ""


# ---------------------------------------------------------------------------
# 커맨드
# ---------------------------------------------------------------------------

def _retraction(path: Path) -> Path:
    return path.with_suffix(".md.retracted.json")


def active_lesson_files(root: Path) -> list[Path]:
    """Return eligible lessons in the existing newest-first order."""
    return [path for path in sorted((root / _LESSONS_DIR).glob("*.md"), reverse=True)
            if not os.path.lexists(_retraction(path))]


def _read_lesson_body(path: Path) -> str | None:
    def open_lesson(name: str, flags: int) -> int:
        return os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK)

    try:
        with open(path, encoding="utf-8", opener=open_lesson) as stream:
            if stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return stream.read()
    except (OSError, UnicodeError):
        pass
    return None


def _searchable_lessons(root: Path) -> list[tuple[Path, str]]:
    docs = []
    for path in active_lesson_files(root):
        content = _read_lesson_body(path)
        if content is not None:
            docs.append((path, content))
    return docs


def cmd_retract(root: Path, filename: str, reason: str, replacement: str | None) -> int:
    folder = root / _LESSONS_DIR

    def lesson_path(name: str) -> Path:
        if Path(name).name != name or Path(name).suffix != ".md":
            raise ValueError("정확한 교훈 파일명(.md)이 필요합니다.")
        path = folder / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"교훈 파일을 찾을 수 없습니다: {name}")
        return path

    try:
        if not reason.strip():
            raise ValueError("철회 이유가 필요합니다.")
        if folder.resolve() != folder:
            raise ValueError("심볼릭 링크 교훈 경로는 사용할 수 없습니다.")
        original = lesson_path(filename)
        if replacement is not None:
            target = lesson_path(replacement)
            if target == original or os.path.lexists(_retraction(target)):
                raise ValueError("대체 교훈은 현재 활성 상태인 다른 교훈이어야 합니다.")
        record = {"reason": reason.strip(), "replacement": replacement,
                  "retracted_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory(prefix=".retract-", dir=folder) as temporary:
            staged = Path(temporary) / "record.json"
            staged.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            os.link(staged, _retraction(original))
    except (OSError, ValueError) as error:
        print(f"[LESSON] 철회 실패: {error}", file=sys.stderr)
        return 1
    print(f"[LESSON] 철회됨: {filename}")
    return 0


def _read_retraction(marker: Path) -> dict:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(marker, flags), encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("invalid marker")
        record = json.load(stream)
    if not isinstance(record, dict) or not isinstance(record.get("reason"), str):
        raise ValueError("invalid record")
    return record


def _is_lesson_filename(filename: object) -> bool:
    return (isinstance(filename, str) and "\x00" not in filename
            and Path(filename).name == filename and Path(filename).suffix == ".md")


def _replacement_status(original: Path, filename: object) -> str:
    if not _is_lesson_filename(filename) or filename == original.name:
        return "확인 불가"
    target = original.with_name(filename)
    try:
        if original.parent.resolve() != original.parent:
            return "확인 불가"
        if not stat.S_ISREG(target.lstat().st_mode):
            return "확인 불가"
    except FileNotFoundError:
        return "파일 없음"
    except (OSError, RuntimeError):
        return "확인 불가"
    try:
        _read_retraction(_retraction(target))
    except FileNotFoundError:
        return "활성"
    except (OSError, ValueError, RecursionError):
        return "확인 불가"
    return "다시 철회됨"


def _show_retraction(path: Path) -> None:
    marker = _retraction(path)
    if not os.path.lexists(marker):
        return
    print("[LESSON] 철회됨")
    try:
        record = _read_retraction(marker)
        print(f"철회 이유: {record['reason']}")
        replacement = record.get("replacement")
        if replacement is not None:
            status = _replacement_status(path, replacement)
            if isinstance(replacement, str) and replacement.isprintable():
                print(f"대체 교훈: {replacement}")
            print(f"대체 교훈 상태: {status}")
    except (OSError, ValueError, RecursionError):
        print("철회 기록 읽기 실패")


def cmd_doctor(root: Path, replacement_filter: str | None = None) -> int:
    """Check lesson bodies and direct replacement links."""
    if replacement_filter is not None and not _is_lesson_filename(replacement_filter):
        print("[LESSON] --replacement에는 경로 없이 정확한 .md 파일명을 지정하세요.", file=sys.stderr)
        return 2

    folder = root / _LESSONS_DIR
    suffix = ".retracted.json"
    try:
        if folder.resolve() != folder:
            raise ValueError("심볼릭 링크 교훈 경로는 사용할 수 없습니다.")
        try:
            entries = sorted(folder.iterdir())
        except FileNotFoundError:
            entries = []
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[LESSON] 진단 불가: {error}", file=sys.stderr)
        return 2

    body_failure_count = 0
    if replacement_filter is None:
        for path in entries:
            if path.suffix == ".md" and _read_lesson_body(path) is None:
                label = path.name if path.name.isprintable() else repr(path.name)
                print(f"- {label}: 교훈 본문 읽기 실패")
                body_failure_count += 1

    markers = [path for path in entries if path.name.endswith(".md" + suffix)]
    counts = dict.fromkeys(("활성", "파일 없음", "다시 철회됨", "확인 불가", "대체 지정 없음"), 0)
    orphan_count = 0
    unreadable_count = 0
    print(f"[LESSON] 철회 기록 {len(markers)}개")
    for marker in markers:
        original = marker.with_name(marker.name.removesuffix(suffix))
        label = original.name if original.name.isprintable() else repr(original.name)
        try:
            replacement = _read_retraction(marker).get("replacement")
        except (OSError, ValueError, RecursionError):
            replacement = None
            status = "확인 불가"
            if replacement_filter is not None:
                unreadable_count += 1
                print(f"- {label}: 철회 기록 읽기 실패")
                continue
        else:
            if replacement_filter is not None and replacement != replacement_filter:
                continue
            status = "대체 지정 없음" if replacement is None else _replacement_status(original, replacement)
        try:
            source_exists = stat.S_ISREG(original.lstat().st_mode)
        except FileNotFoundError:
            source_exists = False
        except OSError:
            source_exists = None
        if source_exists is False:
            orphan_count += 1
            print(f"- {label}: 원본 교훈 없음 또는 일반 파일 아님")
        elif source_exists is None:
            orphan_count += 1
            print(f"- {label}: 원본 교훈 확인 불가")
        if isinstance(replacement, str) and replacement.isprintable():
            label += f" → {replacement}"
        counts[status] += 1
        print(f"- {label}: {status}")

    if replacement_filter is not None:
        print(f"일치 기록 {sum(counts.values())}개")
    if unreadable_count:
        print(f"조회 불완전: 철회 기록 {unreadable_count}개의 일치 여부를 확인할 수 없음")
    print(" / ".join(f"{status} {count}" for status, count in counts.items()))
    if orphan_count:
        print(f"원본 교훈 확인 필요 {orphan_count}")
    return int(bool(body_failure_count or unreadable_count or orphan_count or counts["파일 없음"]
                    or counts["다시 철회됨"] or counts["확인 불가"]))


def cmd_add(root: Path, args) -> int:
    """대화형 또는 인자로 교훈을 추가한다."""
    title = args.title or input("교훈 제목: ").strip()
    if not title:
        print("[LESSON] 제목이 필요합니다.")
        return 1

    if args.interactive or not args.symptom:
        print(f"\n교훈: {title}")
        print("(빈 줄 입력 시 기본값 사용)")
        symptom = input("증상 (무슨 일이 발생했나): ").strip() or "(생략)"
        cause   = input("원인 (왜 발생했나): ").strip() or "(생략)"
        rule    = input("적용된 규칙 (다음엔 어떻게): ").strip() or "(생략)"
        verify  = input("검증 커맨드 (있으면): ").strip() or "(생략)"
        tags    = input("태그 (쉼표 구분): ").strip() or "general"
    else:
        symptom = args.symptom or "(생략)"
        cause   = args.cause or "(생략)"
        rule    = args.rule or "(생략)"
        verify  = args.verify or "(생략)"
        tags    = args.tags or "general"

    path = _lesson_path(root, title)
    if os.path.lexists(_retraction(path)):
        print(f"[LESSON] 철회된 교훈은 덮어쓸 수 없습니다: {path.name}", file=sys.stderr)
        return 1
    content = _TEMPLATE.format(
        title=title,
        date=datetime.now().strftime("%Y-%m-%d"),
        tags=tags,
        symptom=symptom,
        cause=cause,
        rule=rule,
        verify=verify,
    )
    try:
        with tempfile.TemporaryDirectory(prefix=".add-", dir=path.parent) as temporary:
            staged = Path(temporary) / "lesson.md"
            staged.write_text(content, encoding="utf-8")
            # Publish the completed file only if the destination is still free.
            os.link(staged, path)
    except FileExistsError:
        print(f"[LESSON] 같은 이름의 교훈이 이미 있습니다: {path.name}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"[LESSON] 저장 실패: {error}", file=sys.stderr)
        return 1
    print(f"\n[LESSON] ✅ 저장됨: {path.relative_to(root)}")
    print("         다음 세션 시작 시 자동으로 컨텍스트에 포함됩니다.")
    return 0


def cmd_list(root: Path, tag: str | None = None) -> int:
    lessons_dir = root / _LESSONS_DIR
    if not lessons_dir.exists():
        print("[LESSON] 교훈 없음 (.omc/lessons/ 없음)")
        return 0

    files = sorted(lessons_dir.glob("*.md"), reverse=True)
    if not files:
        print("[LESSON] 교훈 없음")
        return 0

    for f in files:
        content = f.read_text(encoding="utf-8")
        if tag and "태그:" in content:
            tag_line = next((l for l in content.splitlines() if l.startswith("태그:")), "")
            if tag not in tag_line:
                continue
        title_line = next((l for l in content.splitlines() if l.startswith("# ")), f.stem)
        title = title_line.lstrip("# ").strip()
        date_line = next((l for l in content.splitlines() if l.startswith("날짜:")), "")
        tags_line = next((l for l in content.splitlines() if l.startswith("태그:")), "")
        print(f"  {f.name}")
        print(f"    제목: {title}")
        if date_line:
            print(f"    {date_line}")
        if tags_line:
            print(f"    {tags_line}")
        print()
    return 0


def cmd_search(root: Path, query: str, top_n: int = 5) -> int:
    """BM25 기반 유사도 검색 — 단순 키워드 매칭보다 높은 재현율."""
    lessons_dir = root / _LESSONS_DIR
    if not lessons_dir.exists():
        print("[LESSON] 교훈 없음")
        return 0

    docs = _searchable_lessons(root)
    if not docs:
        print("[LESSON] 검색 가능한 교훈 없음")
        return 0

    scored = _bm25_scores(query, docs)
    q_tokens = _tokenize(query)

    hits = [(s, p, c) for s, p, c in scored if s > 0][:top_n]

    if not hits:
        # BM25 히트 없으면 단순 포함 검색 fallback
        q_low = query.lower()
        hits = [(0.0, p, c) for p, c in docs if q_low in c.lower()][:top_n]
        if not hits:
            print(f"[LESSON] '{query}' 검색 결과 없음")
            return 0

    print(f"\n[LESSON] '{query}' — BM25 검색 결과 {len(hits)}개\n")
    for rank, (score, path, content) in enumerate(hits, 1):
        lines = content.splitlines()
        title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), path.stem)
        tags_line = next((l for l in lines if l.startswith("태그:")), "")
        snippet = _snippet(content, q_tokens)
        score_bar = "█" * min(int(score * 2), 10)
        print(f"  {rank}. {title}")
        if tags_line:
            print(f"     {tags_line}")
        if snippet:
            print(f"     ↳ {snippet}")
        if score > 0:
            print(f"     점수: {score:.2f} {score_bar}")
        print(f"     파일: {path.name}\n")

    print("상세 보기: python3 scripts/omc_lesson.py show <파일명>")
    return 0


def cmd_show(root: Path, filename: str) -> int:
    lessons_dir = root / _LESSONS_DIR
    path = lessons_dir / filename
    if not path.exists():
        candidates = [p for p in lessons_dir.glob("*.md") if filename in p.name]
        if len(candidates) == 1:
            path = candidates[0]
        elif len(candidates) > 1:
            print("[LESSON] 여러 개 매칭:")
            for c in candidates:
                print(f"  {c.name}")
            return 1
        else:
            print(f"[LESSON] {filename} 없음")
            return 1

    _show_retraction(path)
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_recent(root: Path, n: int = 5) -> int:
    """최근 n개 교훈을 요약 출력한다 (omc_context.py 에서 사용)."""
    lessons_dir = root / _LESSONS_DIR
    if not lessons_dir.exists():
        return 0

    files = active_lesson_files(root)[:n]
    if not files:
        return 0

    print(f"## 최근 교훈 (최대 {n}개)")
    for f in files:
        content = f.read_text(encoding="utf-8")
        lines = content.splitlines()
        title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), f.stem)
        rule_idx = next((i for i, l in enumerate(lines) if l.startswith("## 적용된 규칙")), None)
        rule = lines[rule_idx + 1].strip() if rule_idx is not None and rule_idx + 1 < len(lines) else "(없음)"
        print(f"- **{title}**")
        print(f"  규칙: {rule}")
    print()
    return 0


def search_relevant(root: Path, context_text: str, top_n: int = 3) -> list[tuple[str, str]]:
    """외부에서 호출 가능한 BM25 검색 공개 API.

    omc_context.py 등에서 현재 작업 컨텍스트 기반으로
    관련 교훈을 자동 추천할 때 사용합니다.

    Returns:
        [(title, rule), ...] 형태의 리스트
    """
    return [(title, rule) for title, rule, _ in search_relevant_with_source(root, context_text, top_n)]


def search_relevant_with_source(
    root: Path, context_text: str, top_n: int = 3,
) -> list[tuple[str, str, str]]:
    """Return the same ranked lessons, including their exact filenames."""
    lessons_dir = root / _LESSONS_DIR
    if not lessons_dir.exists():
        return []
    docs = _searchable_lessons(root)
    if not docs:
        return []
    scored = _bm25_scores(context_text, docs)
    results = []
    for score, path, content in scored[:top_n]:
        if score <= 0:
            break
        lines = content.splitlines()
        title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), path.stem)
        rule_idx = next((i for i, l in enumerate(lines) if l.startswith("## 적용된 규칙")), None)
        rule = (
            lines[rule_idx + 1].strip()
            if rule_idx is not None and rule_idx + 1 < len(lines)
            else "(없음)"
        )
        results.append((title, rule, path.name))
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="OMC Compound Engineering 교훈 관리")
    ap.add_argument("--target", type=Path, default=Path.cwd())
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="교훈 추가")
    p_add.add_argument("--title", default="", help="교훈 제목")
    p_add.add_argument("--symptom", default="", help="증상")
    p_add.add_argument("--cause", default="", help="원인")
    p_add.add_argument("--rule", default="", help="적용된 규칙")
    p_add.add_argument("--verify", default="", help="검증 커맨드")
    p_add.add_argument("--tags", default="general", help="태그 (쉼표 구분)")
    p_add.add_argument("-i", "--interactive", action="store_true", help="대화형 입력")

    p_list = sub.add_parser("list", help="교훈 목록")
    p_list.add_argument("--tag", default=None, help="태그 필터")

    p_search = sub.add_parser("search", help="BM25 교훈 검색")
    p_search.add_argument("query", help="검색어")
    p_search.add_argument("--top", type=int, default=5, help="최대 결과 수 (기본: 5)")

    p_show = sub.add_parser("show", help="교훈 상세 출력")
    p_show.add_argument("filename", help="파일명 (부분 매칭 가능)")

    p_retract = sub.add_parser("retract", help="원문을 보존하고 검색과 추천에서 교훈을 제외")
    p_retract.add_argument("filename")
    p_retract.add_argument("--reason", required=True)
    p_retract.add_argument("--replacement", help="현재 활성 상태인 대체 교훈의 정확한 파일명")

    p_doctor = sub.add_parser("doctor", help="교훈 본문과 철회 기록의 대체 관계를 읽기 전용으로 점검")
    p_doctor.add_argument("--replacement", help="직접 참조를 조회할 대체 교훈의 정확한 .md 파일명")

    p_recent = sub.add_parser("recent", help="최근 교훈 요약 (context 주입용)")
    p_recent.add_argument("--n", type=int, default=5)

    args = ap.parse_args()
    root = args.target.resolve()

    if args.cmd == "add":
        return cmd_add(root, args)
    if args.cmd == "list":
        return cmd_list(root, args.tag)
    if args.cmd == "search":
        return cmd_search(root, args.query, args.top)
    if args.cmd == "show":
        return cmd_show(root, args.filename)
    if args.cmd == "retract":
        return cmd_retract(root, args.filename, args.reason, args.replacement)
    if args.cmd == "doctor":
        return cmd_doctor(root, args.replacement)
    if args.cmd == "recent":
        return cmd_recent(root, args.n)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
