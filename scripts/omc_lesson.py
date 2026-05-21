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
import math
import re
import sys
from collections import Counter
from datetime import datetime
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
    path.write_text(
        _TEMPLATE.format(
            title=title,
            date=datetime.now().strftime("%Y-%m-%d"),
            tags=tags,
            symptom=symptom,
            cause=cause,
            rule=rule,
            verify=verify,
        ),
        encoding="utf-8",
    )
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

    files = sorted(lessons_dir.glob("*.md"), reverse=True)
    if not files:
        print("[LESSON] 교훈 없음")
        return 0

    docs = [(f, f.read_text(encoding="utf-8")) for f in files]
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
        candidates = list(lessons_dir.glob(f"*{filename}*"))
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

    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_recent(root: Path, n: int = 5) -> int:
    """최근 n개 교훈을 요약 출력한다 (omc_context.py 에서 사용)."""
    lessons_dir = root / _LESSONS_DIR
    if not lessons_dir.exists():
        return 0

    files = sorted(lessons_dir.glob("*.md"), reverse=True)[:n]
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
    lessons_dir = root / _LESSONS_DIR
    if not lessons_dir.exists():
        return []
    docs = [
        (f, f.read_text(encoding="utf-8"))
        for f in sorted(lessons_dir.glob("*.md"), reverse=True)
    ]
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
        results.append((title, rule))
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
    if args.cmd == "recent":
        return cmd_recent(root, args.n)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
