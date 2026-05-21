#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
import omc_utils

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_state



def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run a command while recording OMC run lifecycle.")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    ap.add_argument("--label", required=True, help="Human-readable command label.")
    ap.add_argument("--summary", default=None, help="Short run summary.")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute after `--`.")
    return ap


def main() -> int:
    args = _parser().parse_args()
    project_root = omc_utils.project_root(args.target)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("Provide the wrapped command after `--`.")

    run = omc_state.start_run(project_root, command_name=args.label, summary=args.summary or args.label)
    run_id = str(run["run_id"])
    env = os.environ.copy()
    env["OMC_PROJECT_ROOT"] = str(project_root)
    env["OMC_RUN_ID"] = run_id
    result_path = project_root / ".omc" / "state" / "runs" / f"{run_id}.result.json"
    env["OMC_RESULT_JSON"] = str(result_path)

    log_path = project_root / ".omc" / "state" / "runs" / f"{run_id}.log"
    env["OMC_LOG_PATH"] = str(log_path)

    try:
        # 실시간 출력을 유지하면서 로그 파일에도 기록하기 위해 stderr를 터미널로 보내고, 
        # 필요 시 캡처할 수 있도록 처리합니다. 
        # 여기서는 단순함을 위해 stderr를 파일로 리다이렉션하면서 터미널에도 출력되도록 
        # subprocess.Popen과 select/threading 대신, 간단한 래퍼를 사용하거나 
        # 최소한 stderr_tail이라도 캡처하도록 시도합니다.
        
        with open(log_path, "w", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                command, 
                cwd=str(project_root), 
                env=env, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            log_tail = []
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log_fh.write(line)
                log_tail.append(line)
                if len(log_tail) > 50:
                    log_tail.pop(0)
            
            proc.wait()
            rc = int(proc.returncode)

        status = "completed" if rc == 0 else "failed"
        result_payload = {"returncode": rc, "command": command}
        if rc != 0 and log_tail:
            result_payload["stderr_tail"] = "".join(log_tail[-10:])
            
        if result_path.exists():
            try:
                result_payload.update(json.loads(result_path.read_text(encoding="utf-8")))
            except Exception:
                result_payload["result_json_error"] = "failed_to_parse"
        omc_state.finish_run(
            project_root,
            run_id=run_id,
            status=status,
            message=f"{args.label} exited with code {rc}",
            result=result_payload,
        )
        return rc
    except KeyboardInterrupt:
        omc_state.finish_run(
            project_root,
            run_id=run_id,
            status="aborted",
            message=f"{args.label} interrupted by user",
            result={"command": command},
        )
        return 130
    except Exception as exc:
        omc_state.finish_run(
            project_root,
            run_id=run_id,
            status="failed",
            message=f"{args.label} failed before execution",
            result={"error": str(exc), "command": command},
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
