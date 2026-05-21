#!/usr/bin/env python3
"""
OMC Cost Tracker
- 전 LLM: git diff --stat 기반 작업 규모 기록 (files changed / lines +/-)
- Claude Code : --output-format json 응답에서 실제 토큰 파싱
- Gemini CLI  : --json 응답(usageMetadata)에서 실제 토큰 파싱
- OpenAI/Codex: API response JSON(usage)에서 실제 토큰 파싱
- 기록 위치: .omc/cost_log.jsonl
- 조회: python3 scripts/omc_cost.py report
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_utils


# ---------------------------------------------------------------------------
# 가격표 (2025-05 기준, USD / 1M tokens)
# ---------------------------------------------------------------------------
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-opus-4":   {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-haiku":    {"input": 0.25, "output": 1.25, "cache_read": 0.03, "cache_write": 0.30},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.5-pro":   {"input": 1.25, "output": 10.0},
    "gpt-4o":          {"input": 2.50, "output": 10.0},
    "gpt-4o-mini":     {"input": 0.15, "output": 0.60},
    "o3":              {"input": 10.0, "output": 40.0},
    "o4-mini":         {"input": 1.10, "output": 4.40},
}
_DEFAULT_PRICING = {"input": 3.0, "output": 15.0}  # claude-sonnet-4 기본


def _cost_log(root):
    return root / ".omc" / "cost_log.jsonl"


def _git_diff_stat(root, base="HEAD"):
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", base],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        if not output:
            result = subprocess.run(
                ["git", "diff", "--cached", "--stat"],
                cwd=root, capture_output=True, text=True, timeout=10,
            )
            output = result.stdout.strip()
        m = re.search(
            r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?",
            output,
        )
        if m:
            return {
                "files_changed": int(m.group(1) or 0),
                "insertions": int(m.group(2) or 0),
                "deletions": int(m.group(3) or 0),
            }
    except Exception:
        pass
    return {"files_changed": 0, "insertions": 0, "deletions": 0}


def _estimate_size(stat):
    total = stat["insertions"] + stat["deletions"]
    files = stat["files_changed"]
    if total == 0 and files == 0: return "none"
    if total <= 30 and files <= 2: return "small"
    if total <= 150 and files <= 8: return "medium"
    if total <= 500 and files <= 20: return "large"
    return "xl"


# ---------------------------------------------------------------------------
# LLM별 토큰 파서
# ---------------------------------------------------------------------------

def _parse_claude_usage(json_output: str) -> dict | None:
    """Claude Code --output-format json 응답 파싱."""
    try:
        data = json.loads(json_output)
        usage = data.get("usage") or data.get("result", {}).get("usage")
        if usage:
            return {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            }
    except Exception:
        pass
    return None


def _parse_gemini_usage(json_output: str) -> dict | None:
    """Gemini CLI --json 응답 파싱.

    응답 형식:
      {"usageMetadata": {"promptTokenCount": N, "candidatesTokenCount": M, "totalTokenCount": T}}
    또는 스트리밍 배열 마지막 항목에서 usageMetadata 추출.
    """
    try:
        data = json.loads(json_output)
        # 배열(스트리밍) 형태 처리
        if isinstance(data, list):
            for item in reversed(data):
                meta = item.get("usageMetadata")
                if meta:
                    data = item
                    break
        meta = data.get("usageMetadata") or {}
        input_t = meta.get("promptTokenCount", 0)
        output_t = meta.get("candidatesTokenCount", 0)
        if input_t or output_t:
            return {
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
    except Exception:
        pass
    return None


def _parse_openai_usage(json_output: str) -> dict | None:
    """OpenAI / Codex API response JSON 파싱.

    응답 형식:
      {"usage": {"prompt_tokens": N, "completion_tokens": M, "total_tokens": T}}
    또는 responses API:
      {"usage": {"input_tokens": N, "output_tokens": M, "input_tokens_details": {...}}}
    """
    try:
        data = json.loads(json_output)
        usage = data.get("usage") or {}
        # responses API (new format)
        input_t = usage.get("input_tokens") or usage.get("prompt_tokens", 0)
        output_t = usage.get("output_tokens") or usage.get("completion_tokens", 0)
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
        if input_t or output_t:
            return {
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cache_read_tokens": cached,
                "cache_write_tokens": 0,
            }
    except Exception:
        pass
    return None


_PARSERS = {
    "claude": _parse_claude_usage,
    "claude-code": _parse_claude_usage,
    "gemini": _parse_gemini_usage,
    "codex": _parse_openai_usage,
    "openai": _parse_openai_usage,
    "gpt": _parse_openai_usage,
}


def _parse_llm_usage(executor: str, json_output: str) -> dict | None:
    """executor 이름 기반으로 알맞은 파서를 선택합니다."""
    key = executor.lower().split("-")[0]
    parser = _PARSERS.get(key) or _PARSERS.get(executor.lower())
    if parser:
        return parser(json_output)
    return None


def _estimate_cost_usd(usage: dict, model: str = "") -> float:
    """usage dict + model명으로 USD 추정."""
    pricing = _DEFAULT_PRICING
    for key, p in _PRICING.items():
        if key in model.lower():
            pricing = p
            break
    return round(
        usage.get("input_tokens", 0) * pricing.get("input", 3.0) / 1_000_000
        + usage.get("output_tokens", 0) * pricing.get("output", 15.0) / 1_000_000
        + usage.get("cache_read_tokens", 0) * pricing.get("cache_read", 0.0) / 1_000_000
        + usage.get("cache_write_tokens", 0) * pricing.get("cache_write", 0.0) / 1_000_000,
        6,
    )


# ---------------------------------------------------------------------------
# 기록 / 리포트
# ---------------------------------------------------------------------------

def record(root, *, executor, session_id=None, task_title=None,
           llm_json=None, model="", base_ref="HEAD"):
    stat = _git_diff_stat(root, base=base_ref)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "executor": executor,
        "model": model or "",
        "session_id": session_id,
        "task": task_title,
        "git": stat,
        "size": _estimate_size(stat),
    }
    if llm_json:
        usage = _parse_llm_usage(executor, llm_json)
        if usage:
            entry["tokens"] = usage
            entry["estimated_usd"] = _estimate_cost_usd(usage, model)

    log_path = _cost_log(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def report(root, last_n=20):
    log_path = _cost_log(root)
    if not log_path.exists():
        print("기록 없음 — python3 scripts/omc_cost.py record 로 기록을 시작하세요.")
        return

    entries = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try: entries.append(json.loads(line))
                except json.JSONDecodeError: pass

    if not entries:
        print("기록 없음")
        return

    recent = entries[-last_n:]
    total_files = sum(e["git"]["files_changed"] for e in entries)
    total_ins   = sum(e["git"]["insertions"] for e in entries)
    total_del   = sum(e["git"]["deletions"] for e in entries)
    total_usd   = sum(e.get("estimated_usd", 0.0) for e in entries)

    # LLM별 집계
    by_executor: dict[str, dict] = {}
    for e in entries:
        ex = e.get("executor", "unknown")
        if ex not in by_executor:
            by_executor[ex] = {"sessions": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
        by_executor[ex]["sessions"] += 1
        t = e.get("tokens", {})
        by_executor[ex]["input_tokens"] += t.get("input_tokens", 0)
        by_executor[ex]["output_tokens"] += t.get("output_tokens", 0)
        by_executor[ex]["usd"] += e.get("estimated_usd", 0.0)

    print(f"\n{'═'*65}")
    print(f" OMC Cost Report  ({len(entries)}개 세션)")
    print(f"{'═'*65}")
    print(f" 총 변경 파일  : {total_files:,}개")
    print(f" 총 추가 줄    : +{total_ins:,}")
    print(f" 총 삭제 줄    : -{total_del:,}")
    if total_usd > 0:
        print(f" 총 추정 비용  : ${total_usd:.4f} USD")

    if any(v["input_tokens"] > 0 for v in by_executor.values()):
        print(f"\n{'─'*65}")
        print(f" {'LLM':<12}  {'세션':>4}  {'입력':>8}  {'출력':>8}  {'비용(USD)':>10}")
        print(f"{'─'*65}")
        for ex, v in sorted(by_executor.items()):
            if v["input_tokens"] > 0:
                print(f" {ex:<12}  {v['sessions']:>4}  {v['input_tokens']:>8,}  {v['output_tokens']:>8,}  ${v['usd']:>9.4f}")

    print(f"\n{'─'*65}")
    print(f" 최근 {len(recent)}개 세션:")
    print(f"{'─'*65}")

    size_icons = {"none": "·", "small": "S", "medium": "M", "large": "L", "xl": "XL"}
    for e in reversed(recent):
        ts       = e["ts"][:16].replace("T", " ")
        size     = size_icons.get(e.get("size", "?"), "?")
        g        = e["git"]
        executor = e.get("executor", "?")[:10]
        task     = (e.get("task") or "")[:30]
        tokens_str = ""
        if "tokens" in e:
            t = e["tokens"]
            usd = e.get("estimated_usd", 0)
            tokens_str = f"  [{t['input_tokens']:,}in/{t['output_tokens']:,}out ${usd:.4f}]"
        print(f" {ts}  [{size:2s}] {executor:<10}  +{g['insertions']:4d}/-{g['deletions']:4d}  {task}{tokens_str}")

    print(f"{'═'*65}\n")


def main():
    parser = argparse.ArgumentParser(description="OMC 비용/작업 규모 추적")
    parser.add_argument("--target", default=None)
    sub = parser.add_subparsers(dest="cmd")

    rec = sub.add_parser("record", help="작업 완료 후 기록")
    rec.add_argument("--executor", default=os.environ.get("OMC_EXECUTOR", "cursor"))
    rec.add_argument("--model", default=os.environ.get("OMC_MODEL", ""),
                     help="모델명 (예: claude-sonnet-4, gemini-2.5-pro, gpt-4o)")
    rec.add_argument("--session-id", default=None)
    rec.add_argument("--task", default=None)
    rec.add_argument("--llm-json", default=None,
                     help="LLM JSON 출력 파일 경로 (Claude/Gemini/OpenAI 모두 지원)")
    # 하위 호환: 기존 --claude-json 인자 유지
    rec.add_argument("--claude-json", default=None, help="(deprecated) --llm-json 사용 권장")
    rec.add_argument("--base-ref", default="HEAD")

    sub.add_parser("report", help="비용 리포트 출력")

    args = parser.parse_args()
    root = omc_utils.project_root(args.target)

    if args.cmd == "record":
        llm_json_str = None
        json_file = getattr(args, "llm_json", None) or getattr(args, "claude_json", None)
        if json_file:
            try:
                llm_json_str = Path(json_file).read_text(encoding="utf-8")
            except FileNotFoundError:
                print(f"[WARN] 파일 없음: {json_file}", file=sys.stderr)

        entry = record(
            root,
            executor=args.executor,
            model=getattr(args, "model", ""),
            session_id=args.session_id,
            task_title=args.task,
            llm_json=llm_json_str,
            base_ref=args.base_ref,
        )
        g = entry["git"]
        usd_str = f"  추정 ${entry['estimated_usd']:.4f}" if "estimated_usd" in entry else ""
        model_str = f" ({entry['model']})" if entry.get("model") else ""
        print(f"[COST] 기록됨 [{entry['size'].upper()}] +{g['insertions']}/-{g['deletions']} ({g['files_changed']} files){usd_str}{model_str}")

    elif args.cmd == "report":
        report(root)
    else:
        parser.print_help()


if __name__ == "__main__":
    raise SystemExit(main())
