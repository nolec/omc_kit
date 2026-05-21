#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"[!] Command failed: {' '.join(cmd)}\n{proc.stderr}", file=sys.stderr)
        sys.exit(proc.returncode)
    return proc.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an async or sync peer-review using OMC headless execution.")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    ap.add_argument("--async-mode", action="store_true", help="Run in background via detached process.")
    args = ap.parse_args()

    project_root = args.target.resolve()
    kit_dir = Path(__file__).resolve().parents[1]
    omc_script = kit_dir / "scripts" / "omc.py"
    exec_script = kit_dir / "scripts" / "omc_exec.py"

    # If --async-mode is requested and we are not already detached, spawn a detached child and exit
    if args.async_mode and os.environ.get("OMC_PEER_REVIEW_DETACHED") != "1":
        env = os.environ.copy()
        env["OMC_PEER_REVIEW_DETACHED"] = "1"
        cmd = [sys.executable, str(__file__), "--target", str(project_root)]
        # start_new_session=True detaches the process from the terminal (Linux/Mac)
        subprocess.Popen(cmd, cwd=str(project_root), env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[+] Background peer-review process started.")
        return 0

    print("Gathering git diff...")
    diff_text = _run(["git", "diff", "HEAD"], cwd=project_root)
    if not diff_text.strip():
        print("No changes found to review.")
        return 0

    with tempfile.NamedTemporaryFile(prefix="omc-review-prompt.", suffix=".md", delete=False) as fp:
        prompt_path = Path(fp.name)

    print("Generating review prompt...")
    # Use omc.py prompt to compose a standard role prompt with 'code_review' role
    _run(
        [
            sys.executable,
            str(omc_script),
            "prompt",
            "Please review the following git diff and identify any logic errors, bugs, or improvements.",
            "--roles",
            "code_review",
            "--out",
            str(prompt_path),
            "--assume-confirm",
            "--quiet-write",
        ],
        cwd=project_root,
    )

    # Append the diff to the prompt
    with open(prompt_path, "a", encoding="utf-8") as f:
        f.write("\n\n## Target Git Diff\n\n```diff\n")
        f.write(diff_text)
        f.write("\n```\n")

    print("Executing headless review...")
    # Execute the review using omc_exec.py in headless mode
    proc = subprocess.run(
        [
            sys.executable,
            str(exec_script),
            "--target",
            str(project_root),
            "--prompt-file",
            str(prompt_path),
            "--executor",
            "auto",
            "--execution-mode",
            "headless",
        ],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        check=False,
    )

    review_out_path = project_root / ".omc" / "peer_review.md"
    review_out_path.parent.mkdir(parents=True, exist_ok=True)
    
    if proc.returncode == 0:
        review_out_path.write_text(proc.stdout, encoding="utf-8")
        note_text = "Peer review completed. See `.omc/peer_review.md`."
    else:
        err_msg = f"Peer review failed with exit code {proc.returncode}.\n{proc.stderr}"
        review_out_path.write_text(err_msg, encoding="utf-8")
        note_text = "Peer review failed. See `.omc/peer_review.md`."

    # Record note to notepad.md
    _run(
        [
            sys.executable,
            str(omc_script),
            "state",
            "note",
            "--target",
            str(project_root),
            "--kind",
            "peer-review",
            "--text",
            note_text,
        ],
        cwd=project_root,
    )

    prompt_path.unlink(missing_ok=True)
    if os.environ.get("OMC_PEER_REVIEW_DETACHED") != "1":
        print(f"Review finished. Written to {review_out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
