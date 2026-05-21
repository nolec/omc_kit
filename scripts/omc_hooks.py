#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import omc_state


HOOK_EVENTS = {"session_start", "session_end", "pre_compact", "post_compact"}


def _read_hooks(project_root: Path) -> dict:
    return omc_state.read_hooks(project_root)


def _append_memory_note(project_root: Path, text: str) -> None:
    omc_state.append_note(project_root, note_kind="hook", text=text)


def _snapshot_memory(project_root: Path) -> Path:
    memory = omc_state.read_memory(project_root)
    snapshot_path = omc_state.get_compact_dir(project_root) / f"{omc_state.make_slug_now()}.json"
    snapshot_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _append_memory_note(project_root, f"pre_compact snapshot saved: {snapshot_path.name}")
    return snapshot_path


def _refresh_notepad(project_root: Path) -> Path:
    return omc_state.refresh_notepad(project_root)


def _auto_compact(project_root: Path) -> dict[str, object]:
    policy = omc_state.read_policy(project_root)
    threshold = int(policy.get("auto_compact_threshold_count", 0))
    if threshold <= 0:
        return {"status": "skipped", "reason": "threshold not set"}

    session_count = len(omc_state.get_session_entries(project_root))

    if session_count >= threshold:
        keep = int(policy.get("auto_compact_keep_entries", 25))
        result = omc_state.compact_state(project_root, keep_entries=keep)
        _append_memory_note(project_root, f"Auto-compacted: {session_count} -> {keep} entries (threshold {threshold})")
        return {"status": "ok", "type": "builtin", "name": "auto_compact", "count": session_count, "keep": keep, "result": result}

    return {"status": "skipped", "reason": f"count {session_count} < threshold {threshold}"}


def _run_shell(project_root: Path, command: str) -> int:
    proc = subprocess.run(command, cwd=project_root, shell=True, check=False)
    return int(proc.returncode)


def _run_hook(project_root: Path, event: str, hook: dict) -> dict[str, object]:
    hook_type = str(hook.get("type", "builtin"))
    if hook_type == "builtin":
        name = str(hook.get("name", ""))
        if name == "refresh_notepad":
            path = _refresh_notepad(project_root)
            return {"status": "ok", "type": "builtin", "name": name, "path": str(path)}
        if name == "snapshot_memory":
            path = _snapshot_memory(project_root)
            return {"status": "ok", "type": "builtin", "name": name, "path": str(path)}
        if name == "auto_compact":
            return _auto_compact(project_root)
        raise ValueError(f"Unknown builtin hook: {name}")

    if hook_type == "shell":
        command = str(hook.get("command", "")).strip()
        if not command:
            raise ValueError("Shell hook requires `command`.")
        code = _run_shell(project_root, command)
        return {"status": "ok" if code == 0 else "error", "type": "shell", "command": command, "code": code}

    raise ValueError(f"Unknown hook type: {hook_type}")


def _reset_pipeline_guard_session(project_root: Path) -> dict[str, object]:
    """session_start 시 pipeline_guard의 CONTRACT 플래그를 자동 초기화.

    모든 LLM(Cursor / Claude / Gemini / Codex) 공통 진입점인 omc_hooks.py에서
    처리하므로 LLM별 별도 설정 없이 자동 적용된다.
    """
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "omc_pipeline_guard",
            project_root / "scripts" / "omc_pipeline_guard.py",
        )
        if _spec is None:
            return {"status": "skipped", "reason": "omc_pipeline_guard.py not found"}
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        rc = _mod.cmd_session_start(project_root)
        return {"status": "ok", "type": "builtin", "name": "pipeline_guard.session-start", "rc": rc}
    except Exception as exc:
        return {"status": "error", "type": "builtin", "name": "pipeline_guard.session-start", "error": str(exc)}


def run_event(project_root: Path, event: str) -> dict[str, object]:
    if event not in HOOK_EVENTS:
        known = ", ".join(sorted(HOOK_EVENTS))
        raise ValueError(f"Unknown event: {event}. Known: {known}")

    config = _read_hooks(project_root)
    hooks = list(config.get("hooks", {}).get(event, []))
    results: list[dict[str, object]] = []

    # session_start 이벤트 발생 시 모든 LLM 공통으로 CONTRACT 플래그 초기화
    if event == "session_start":
        results.append(_reset_pipeline_guard_session(project_root))

    for hook in hooks:
        try:
            results.append(_run_hook(project_root, event, hook))
        except ValueError as exc:
            results.append({"status": "error", "error": str(exc), "hook": hook})

    return {
        "event": event,
        "count": len(results),
        "results": results,
    }


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run OMC lifecycle hooks.")
    ap.add_argument("event", choices=sorted(HOOK_EVENTS), help="Hook event name.")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    return ap


def main() -> int:
    ap = _parser()
    args = ap.parse_args()
    result = run_event(args.target.resolve(), args.event)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
