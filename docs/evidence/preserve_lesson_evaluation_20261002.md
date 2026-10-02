# Preserve lesson evaluation — completed isolated comparison

Claim boundary: ISOLATED_LESSON_EVAL_ONLY. No general quality, natural-use product effect, or token/cost saving claim.

## Frozen conditions

The companion JSON evidence bundle embeds frozen.json, which contains exact prompt, three contexts, expected per-item answers, source SHA-256 values, model gpt-6.1-sol/low, three alternating arm orders, and maximum nine model attempts. Each attempt must use a fresh session. None emits no additional context; summary uses the original hook's recorded output byte-for-byte; rule uses only the original lesson's applied-rule section. The actual prompt contains scenario facts and output enum alternatives, but not the oracle or recommendation. It may still allow the model to infer the answer without the lesson: baseline success would not establish lesson necessity.

## Oracle evidence

scripts/test_omc_guard.py:624-660 confirms a valid explicit preserve is signed, has empty lineage fields, leaves pending bytes and original lock unchanged, and creates no completion-lineage. Lines 663-683 reject non-preserve and partially populated lineage inputs. scripts/omc_state.py:3290-3355 limits preserve to a matching existing pending context. Scoring separately checks non_lineage / keep / reject, plus strict answer shape. A correct label alone does not prove the reasoning explanation is sound; explanations must be inspected separately.

## Preparation observation

The original hook selects the expected lesson for the fixed diagnostic prompt, but its output does not contain the full applied-rule sentence verbatim. This does not prove that no useful meaning is present. The original raw output is preserved in the companion bundle as preparation_stdout. At preparation time, delivery and behavioral comparison had not been executed. The completed results are recorded below.

## Checks and boundaries

Four local evaluator tests cover per-item error scoring, malformed responses, condition separation, and timeout partial-output preservation. Product source is unchanged. Original source files are only read or copied into this directory; all fixture data is isolated. The repository staged gate does not cover these external files. The original experiment was benchmark_maintenance; this adoption is document_only. Evidence A, its metrics, installation identity, and ledger are not modified.

The user trusted this exact handler through the normal UI. The stored trusted hash was inspected before execution; no bypass option was used. Nine approved calls have now completed with nine distinct session IDs. Current mode is none. No further calls are authorized by this experiment's budget.

## Actual runtime comparison

All nine sessions used gpt-6.1-sol with low effort and no tool calls. Their rollout developer messages match the frozen condition text exactly: no hook context for none, original recorded search output for summary, and original applied-rule text for rule. Original source hashes remain unchanged. Detailed provenance, raw CLI outputs, relevant rollout extracts, answers, and totals are archived in preserve_lesson_evaluation_20261002.json. Full session rollouts remain local; their hashes record provenance but do not constitute portable full-rollout custody.

| Condition | Correct signing | Correct pending | Correct partial-lineage | Fully correct |
| --- | --- | --- | --- | --- |
| No injection | 3/3 | 3/3 | 3/3 | 3/3 |
| Current search summary | 3/3 | 3/3 | 3/3 | 3/3 |
| Explicit applied rule | 3/3 | 3/3 | 3/3 | 3/3 |

Manual inspection of the reason fields found all nine explanations consistent with reference-versus-participation separation, matching pending preservation, and rejection rather than repair/acceptance of partial lineage. The explicit-rule arm also mentioned Guard CLI and pending-invariance validation; this was not a preregistered scoring endpoint and is descriptive only.

Observed input tokens by condition: none 59,832; summary 60,852; rule 59,919. Outputs: 409 / 351 / 373. Cache inputs: 39,552 each. These are independent fresh-session reported counters, not cumulative resumed-turn sums. They are descriptive usage evidence only, not monetary cost or general savings estimates. The process-level raw status keeps usage_status=unmeasured; usage is explicitly parsed and reported in the verified aggregate, not assumed from process completion.

User defaults were loaded; model and effort were explicitly pinned and verified. This is not a completely frozen environment. Chronicle feature warnings were preserved in raw output and do not represent hook failure; no hook error or condition mismatch was observed in the model-visible evidence.

Conclusion: this case verifies transport but shows no accuracy uplift from injection. Baseline ceiling success makes it unsuitable for proving lesson necessity or choosing product changes. Do not conclude that lessons are generally useless, that rule extraction is superior, or that deduplication is safe. Any different diagnostic case or further model calls need a separate scope decision.

Lesson recorded here only: selecting a relevant lesson and transmitting its applied rule are distinct evidence boundaries.

## Adoption boundary

Adopted on 2026-10-02 as a limited isolated research record after APPROVE WITH NOTES. This is not Evidence A or product-effect evidence and grants no implementation, deployment, or further experiment authority. The preparation sentence was corrected for historical tense; original raw results were not altered. No new model calls were made. Temporary evaluator tests are embedded in the companion bundle for evidence, not installed as product tooling.
