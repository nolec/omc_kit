# OMC Review Synthetic Comparison

## Scope

- Source: `synthetic`
- Comparison scope: `same_diff`
- Cases: 4
- Providers: Codex CLI and OMC review
- Purpose: validate the comparison pipeline, not prove production superiority.
- Metrics source: `omc_review_synthetic_runtime_cases.json`
- Codex output: complete CLI output with temporary-repository paths redacted to `src/...`.
- OMC output: complete manually recorded output, with reviewer basis metadata attached.

## Results

| Provider | Completed | Critical-or-higher hits | Misses | False positives | Evidence matches | Provenance |
|---|---:|---:|---:|---:|---:|---|
| Codex | 4 | 4 | 0 | 0 | 4 | `cli_completed` |
| OMC review | 4 | 4 | 0 | 0 | 4 | `manual_rule_application` |

## Interpretation

Both providers found the expected defect in all 4 controlled cases. The result is a tie on this synthetic set. Codex output is preserved after path redaction; OMC output was recorded by applying the OMC review checklist manually, so it is not an independent OMC model-executor result.

This report must not be used to claim that OMC review replaces Codex review. That requires at least 10 real anonymized observed diffs with independently preserved outputs and adjudicated gold findings.
