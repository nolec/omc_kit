from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "scripts" / "fixtures" / "omc_latency_benchmark_v2.json"


def test_latency_benchmark_v2_preserves_resolver_contracts() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 2
    assert payload["source_commit"] == "48a9782"
    prompt = payload["prompt"]
    for contract in (
        "OMC_CODEX_BIN command names remain supported",
        "main returns 127 and preserves the prompt",
        "headless preflight returns a soft-failure tuple",
        "absolute paths are validated independently from PATH command names",
    ):
        assert contract in prompt
    assert payload["related_test_command"] == (
        "pytest -q scripts/test_omc_exec_resolution.py "
        "scripts/test_omc_exec_codex_headless.py scripts/test_omc_exec_interface.py"
    )
