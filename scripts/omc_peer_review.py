#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import omc_review_snapshot


def _run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"[!] Command failed: {' '.join(cmd)}\n{proc.stderr}", file=sys.stderr)
        sys.exit(proc.returncode)
    return proc.stdout


def _untracked_diff(project_root: Path, path: str) -> str:
    candidate = project_root / path
    if candidate.is_file() or candidate.is_symlink():
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--", "/dev/null", path],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode not in (0, 1):
            raise omc_review_snapshot.PeerSnapshotError("untracked_diff_unavailable")
        return proc.stdout
    if candidate.is_dir():
        return f"Submodule candidate: {path}\n"
    raise omc_review_snapshot.PeerSnapshotError("untracked_candidate_missing")


def freeze_peer_input(project_root: Path) -> dict[str, object]:
    """Capture every review input before an async child can observe later edits."""
    project_root = project_root.resolve()
    base_commit = _run(["git", "rev-parse", "--verify", "HEAD^{commit}"], project_root).strip()
    candidate = omc_review_snapshot.build_worktree_candidate(
        project_root, base_commit=base_commit
    )
    if not candidate["candidate_scope"]:
        raise omc_review_snapshot.PeerSnapshotError("peer_review_scope_empty")
    diff_text = omc_review_snapshot.build_review_diff(
        project_root, base_commit=base_commit
    ).decode("utf-8")
    frozen = omc_review_snapshot.create_peer_snapshot(
        project_root,
        candidate=candidate,
        review_diff=diff_text.encode("utf-8"),
    )
    return {
        "snapshot_path": frozen["path"],
        "snapshot_sha256": frozen["sha256"],
        "candidate_scope_sha256": candidate["candidate_scope_sha256"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an async or sync peer-review using OMC headless execution.")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    ap.add_argument("--async-mode", action="store_true", help="Run in background via detached process.")
    ap.add_argument("--snapshot-path", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--snapshot-sha256", help=argparse.SUPPRESS)
    args = ap.parse_args()

    project_root = args.target.resolve()
    kit_dir = Path(__file__).resolve().parents[1]
    omc_script = kit_dir / "scripts" / "omc.py"
    exec_script = kit_dir / "scripts" / "omc_exec.py"

    if bool(args.snapshot_path) != bool(args.snapshot_sha256):
        print("[!] Peer-review snapshot arguments are incomplete.", file=sys.stderr)
        return 2
    try:
        frozen = (
            {
                "snapshot_path": str(args.snapshot_path),
                "snapshot_sha256": args.snapshot_sha256,
            }
            if args.snapshot_path is not None
            else freeze_peer_input(project_root)
        )
    except (
        omc_review_snapshot.CandidateScopeError,
        omc_review_snapshot.PeerSnapshotError,
    ) as error:
        if str(error) == "peer_review_scope_empty":
            print("No changes found to review.")
            return 0
        print(f"[!] Peer-review snapshot failed: {error}", file=sys.stderr)
        return 2

    # A detached child receives only the already-hashed input, never the live tree.
    if args.async_mode and os.environ.get("OMC_PEER_REVIEW_DETACHED") != "1":
        env = os.environ.copy()
        env["OMC_PEER_REVIEW_DETACHED"] = "1"
        cmd = [
            sys.executable,
            str(__file__),
            "--target",
            str(project_root),
            "--snapshot-path",
            str(frozen["snapshot_path"]),
            "--snapshot-sha256",
            str(frozen["snapshot_sha256"]),
        ]
        # start_new_session=True detaches the process from the terminal (Linux/Mac)
        subprocess.Popen(cmd, cwd=str(project_root), env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[+] Background peer-review process started.")
        return 0

    try:
        loaded = omc_review_snapshot.load_peer_snapshot(
            Path(str(frozen["snapshot_path"])),
            expected_sha256=str(frozen["snapshot_sha256"]),
        )
    except omc_review_snapshot.PeerSnapshotError as error:
        print(f"[!] Peer-review snapshot failed: {error}", file=sys.stderr)
        return 2
    diff_text = loaded["review_diff"].decode("utf-8")
    frozen["candidate_scope_sha256"] = loaded["candidate"]["candidate_scope_sha256"]

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
    
    metadata = {
        "snapshot_name": Path(str(frozen["snapshot_path"])).name,
        "snapshot_sha256": frozen["snapshot_sha256"],
        "candidate_scope_sha256": frozen.get("candidate_scope_sha256"),
    }
    if proc.returncode == 0:
        evidence = omc_review_snapshot.seal_review_output(
            project_root,
            snapshot_path=Path(str(frozen["snapshot_path"])),
            snapshot_sha256=str(frozen["snapshot_sha256"]),
            review_output=proc.stdout.encode("utf-8"),
        )
        metadata.update(evidence)
        review_out_path.write_text(
            f"<!-- OMC_PEER_REVIEW: {json.dumps(metadata, sort_keys=True)} -->\n{proc.stdout}",
            encoding="utf-8",
        )
        note_text = "Peer review completed with a frozen snapshot. See `.omc/peer_review.md`."
    else:
        err_msg = f"Peer review failed with exit code {proc.returncode}.\n{proc.stderr}"
        review_out_path.write_text(
            f"<!-- OMC_PEER_REVIEW: {json.dumps(metadata, sort_keys=True)} -->\n{err_msg}",
            encoding="utf-8",
        )
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
