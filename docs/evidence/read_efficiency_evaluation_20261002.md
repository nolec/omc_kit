# Isolated read-efficiency measurement — 2026-10-02

Claim: ISOLATED_READ_EFFICIENCY_ONLY. Six approved fresh-session calls; budget exhausted. No product, installation, or cohort changes. This is a controlled synthetic reading-policy comparison, not natural-workflow or competitive evidence.

## Frozen task and actual execution

Both arms investigated the same checkout implementation, read its contract, and ran the same failing verification. Full policy requested complete source/contract reads; narrow policy requested the checkout region and contract, permitting expansion if needed. It did not force duplicate baseline reads. Unrelated inventory comments are synthetic. The fixture hashes stayed unchanged. Actual rollout turn contexts confirmed gpt-6.1-sol / low for all six distinct sessions. User defaults were loaded; this is not a fully frozen environment.

`frozen.json` preserves the prompts, pre-call expected values, arm order and fixture hashes. `results/attempt-1` through `attempt-6` preserve started commands, raw stdout/stderr and parsed results. Rollouts remain in the user's local Codex session store, keyed by result session_ids; full rollout custody is not portable with this temporary directory.

## Descriptive measurements

| Metric (three runs per arm) | Full | Narrow |
| --- | ---: | ---: |
| Reported input tokens, sum | 196455 | 221527 |
| Reported output tokens, sum | 724 | 964 |
| Reported cached input tokens, sum | 156928 | 196736 |
| Input minus cached input, sum | 39527 | 24791 |
| Wall time median, seconds | 26.37 | 28.54 |
| Completed command executions, sum | 6 | 13 |
| Completed command output characters, sum | 32946 | 1641 |

Fresh independent session final turn usage counters are summed; these are not summed cumulative resume snapshots. Narrow reduced command-output characters by 95.0%, but reported total input+output rose 12.8% and median elapsed time rose 8.2%. Cache composition differed considerably; uncached input fell, so total-token changes do not establish billed cost or subscription consumption. Wall time includes process startup and provider latency; no variance or general improvement claim is supported by n=3.

## Scoring defect and quality boundary

The frozen automatic scorer expected the bug string `discount_on_subtotal`, but the prompt allowed any concise identifier. All six returned the substantively correct `shipping_discounted`; the scorer marked all six false. Its expected identifier is also poorly named for the actual defect. This is an evaluator defect, not six observed task failures. The original false scores are retained; no post-hoc change to the primary scoring endpoint was made.

Post-hoc manual inspection found all six explanations identify incorrectly discounted shipping, expected=2375 versus observed=2250, and preserve source/contract evidence. Actual command events show the required verification returned exit 1 in every run. This qualitative audit is separate from the invalid automatic endpoint and does not convert the benchmark into a confirmatory quality-preservation pass. Unit tests (3 passed) cover parsing/scorer mechanics, not semantic validity of the rubric.

## Interpretation and next decision

No efficiency improvement is established. The narrow runs split investigation into 4/4/5 command calls versus 2/2/2 in full; reducing output without bounding tool round trips did not reduce total reported tokens or time here. This is an observed association, not an isolated causal attribution to call count. No duplicate-read benefit was tested independently.

Before another experiment, repair and pre-register the semantic rubric and compare equally batched full versus narrow reads. That requires separate approval and a new call budget; do not rerun automatically or deploy this policy. The staged repository TDD gate reports no new implementation files and does not validate this external harness. No repository lesson was written; this report records the evaluator and round-trip lessons locally only.

## Offline scorer correction — separate maintenance scope

`quality_v2.py` supersedes the legacy mechanical scoring contract for offline audit; `measure.py`, frozen.json and all six attempt records remain historical and unchanged. Do not reuse the legacy runner for another experiment. The new audit permits arbitrary nonempty bug identifiers, rejects wrong numeric types/values and malformed evidence, and compares claimed verification with one exact executed command, exit 1 and the expected failure output. Missing, duplicate, echoed or contradictory verification is rejected.

Meaning of the explanation/source/contract is deliberately NOT inferred from keyword matching. Passing mechanical checks remains `UNRESOLVED_MANUAL_REVIEW`; it never yields approval or confirmatory eligibility. `quality_v2_offline_audit.json` is a separate post-hoc diagnostic, not a replacement of the original scores or a new quality-preservation pass. Further model calls require separately approved corrected prompts, rubric and call budget. The v2 tool performs no provider calls, file writes or cohort access.

### Expected failure status correction

The initial v2 incorrectly required command status `completed` despite expecting exit 1. Its six failed mechanical audits remain preserved in `quality_v2_offline_audit.json`. The corrected audit requires an `item.completed` event containing explicit `status=failed`, integer exit 1, the exact verification command and expected failure output; missing or contradictory status is rejected. `quality_v2_status_fix_audit.json` records the separate corrected diagnostic without replacing historical scores or promoting quality authority. Regression tests consume all six real raw event streams, in addition to missing/in-progress/completed/cancelled status cases. This fixture-specific tool does not treat arbitrary failed commands as successful validation.

## Repository adoption boundary

Adopted 2026-10-02 as ISOLATED_READ_EFFICIENCY_ONLY. The companion JSON stores a SHA-256-bound gzip+base64 payload containing frozen conditions, fixture text, six raw CLI streams, original results, current scorer/tests and both post-hoc audits. Decode payload_gzip_base64 with base64, then gzip, to obtain UTF-8 JSON; verify payload_sha256 against the decompressed bytes. Filename references above describe the original isolated directory. Full session rollouts remain local and are not included.

Evidence A, its metrics, installation identity and ledger remain unchanged. No efficiency improvement or monetary savings is established. Original false scores remain historical; corrected mechanical checks do not replace them or grant confirmatory eligibility. Initial v2 source was not separately frozen before its status fix: prior audit and current source are preserved, not complete source-version history. No new model calls, consumer updates or commits are authorized by adoption.
