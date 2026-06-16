#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _resolve_install_script(start_dir: Path) -> Path:
    for base in [start_dir, *start_dir.parents]:
        # Dev-repo smoke should prefer the root installer; nested omc_kit/ is fallback for packaged layouts.
        for rel in ("scripts/install.py", "omc_kit/scripts/install.py"):
            candidate = base / rel
            if candidate.exists():
                return candidate.resolve()
    raise SystemExit("could not locate install.py from current script path")


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _require_ok(proc: subprocess.CompletedProcess[str], *, label: str) -> None:
    if proc.returncode == 0:
        return
    parts = [f"{label} failed: exit_code={proc.returncode}"]
    if proc.stdout.strip():
        parts.append(proc.stdout.strip())
    if proc.stderr.strip():
        parts.append(proc.stderr.strip())
    raise SystemExit("\n".join(parts))


def _assert_exists(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"missing expected path: {path}")


def _seed_custom_agents(path: Path) -> None:
    path.write_text(
        "# AGENTS.md\n\n"
        "프로젝트 소개 문구.\n\n"
        "## FE 팀원용 빠른 시작\n\n"
        "1. 먼저 읽기: AGENTS.md\n"
        "2. 바로 멈출 변경: auth, payment\n\n"
        "<!-- OMC:BEGIN -->\n"
        "<!-- OMC:AGENTS:V1 -->\n"
        "## OMC — Orchestrated Multi-agent Craft\n"
        "old managed block\n"
        "<!-- OMC:END -->\n\n"
        "## Git and Pull Request Rules\n\n"
        "- base는 develop\n\n"
        "## 13. 완료 전 체크리스트\n\n"
        "- 요청한 동작만 변경했습니다.\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the OMC kit into a temp project and run common OMC smoke checks.")
    ap.add_argument("--executor", choices=["codex", "gemini"], default=None, help="Also run installed headless/chat smoke with this executor.")
    ap.add_argument("--headless-timeout-sec", type=int, default=180)
    ap.add_argument("--chat-timeout-sec", type=int, default=300)
    ap.add_argument("--chat-exec-timeout-sec", type=int, default=120)
    args = ap.parse_args()

    install_script = _resolve_install_script(Path(__file__).resolve().parent)
    repo_root = install_script.parent.parent
    with tempfile.TemporaryDirectory(prefix="omc-setup-smoke.") as tmp:
        project_root = Path(tmp).resolve()

        install = _run(
            [sys.executable, str(install_script), "--target", str(project_root)],
            cwd=repo_root,
        )
        _require_ok(install, label="install")

        for rel in [
            "run",
            "scripts/omc.py",
            "scripts/omc_exec.py",
            "scripts/omc_domain.py",
            "scripts/omc_doctor.py",
            "scripts/omc_chat.py",
            "docs/verification_checklist.md",
        ]:
            _assert_exists(project_root / rel)

        agents = project_root / "AGENTS.md"
        _seed_custom_agents(agents)

        setup = _run(
            [sys.executable, "scripts/omc.py", "setup", "--target", str(project_root), "--force"],
            cwd=project_root,
        )
        _require_ok(setup, label="setup")

        agents_text = agents.read_text(encoding="utf-8")
        if "## FE 팀원용 빠른 시작" not in agents_text:
            raise SystemExit("setup force lost custom AGENTS quick-start section")
        if agents_text.count("<!-- OMC:BEGIN -->") != 1 or agents_text.count("<!-- OMC:END -->") != 1:
            raise SystemExit("setup force did not preserve a single managed OMC block")
        if "old managed block" in agents_text:
            raise SystemExit("setup force did not refresh the managed OMC block")

        omc_help = _run([str(project_root / "run"), "omc-help"], cwd=project_root)
        _require_ok(omc_help, label="run omc-help")

        status = _run([sys.executable, "scripts/omc.py", "state", "status", "--target", str(project_root)], cwd=project_root)
        _require_ok(status, label="state status")
        if "enforce_confirm: True" not in status.stdout:
            raise SystemExit("state status missing enforce_confirm=True")

        domain = _run([str(project_root / "run"), "omc-domain", "sample"], cwd=project_root)
        _require_ok(domain, label="run omc-domain")
        _assert_exists(project_root / "project_prompts" / "team.local.json")
        _assert_exists(project_root / "project_prompts" / "ROLE_PROJECT_SAMPLE_ASSISTANT.md")

        doctor = _run([str(project_root / "run"), "omc-doctor"], cwd=project_root)
        _require_ok(doctor, label="run omc-doctor")

        _assert_exists(project_root / ".omc" / "policy.json")
        _assert_exists(project_root / ".omc" / "hooks.json")
        _assert_exists(project_root / ".omc" / "summary.md")
        _assert_exists(project_root / ".omc" / "notepad.md")

        if args.executor:
            _assert_exists(project_root / "scripts" / "test_omc_headless_smoke.py")
            _assert_exists(project_root / "scripts" / "test_omc_chat_headless_smoke.py")
            env = os.environ.copy()
            env["OMC_EXEC_TIMEOUT_SEC"] = str(args.chat_exec_timeout_sec)
            headless = _run(
                [
                    sys.executable,
                    "scripts/test_omc_headless_smoke.py",
                    "--target",
                    str(project_root),
                    "--executor",
                    args.executor,
                    "--timeout-sec",
                    str(args.headless_timeout_sec),
                ],
                cwd=project_root,
                timeout=max(300, args.headless_timeout_sec + 30),
                env=env,
            )
            _require_ok(headless, label=f"headless smoke ({args.executor})")

            chat = _run(
                [
                    sys.executable,
                    "scripts/test_omc_chat_headless_smoke.py",
                    "--target",
                    str(project_root),
                    "--executor",
                    args.executor,
                    "--timeout-sec",
                    str(args.chat_timeout_sec),
                    "--exec-timeout-sec",
                    str(args.chat_exec_timeout_sec),
                ],
                cwd=project_root,
                timeout=max(360, args.chat_timeout_sec + 30),
                env=env,
            )
            _require_ok(chat, label=f"chat smoke ({args.executor})")

        print("SMOKE_OK")
        print(f"[project_root] {project_root}")
        print("[checked] install, setup, run omc-help, state status, domain overlay, doctor, .omc bootstrap")
        if args.executor:
            print(f"[checked] headless smoke, chat smoke ({args.executor})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
