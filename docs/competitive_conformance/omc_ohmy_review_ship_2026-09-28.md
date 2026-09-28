# OMC vs oh-my Launch: review→ship identity (2026-09-28)

**Classification: `COMPETITIVE_CONFORMANCE_ONLY`.** This is an isolated synthetic comparison record, not Evidence A, Evidence B, or product-effect evidence. OMC product value remains `NOT_PROVEN`. This record creates no implementation, rollout, cohort, or experiment authority and implies **no automatic product decision**.

## Question and boundary

Question: after an approved review, does the tested path compare the candidate being closed or shipped with the candidate that was reviewed? A normal content-preserving commit is the control; a comment-only edit and a code edit are changes. `review_stale` demonstrates identity conformance, not whether the extra re-review cost is worthwhile.

This is **not a symmetric end-to-end comparison**. The OMC side exercised the `omc_review_snapshot.py validate-ship` CLI directly. The oh-my side exercised independent Phase 4 reviewers followed by a *new* `/oh-my-claudecode:launch` invocation. Their full review, acceptance, and ship workflows were not run under an identical lifecycle. No overall winner follows from the observations below.

## OMC isolated fixture

- OMC source commit used: `61694edc635871738e84e7148d422f2aa0e54417`.
- Fixture base commit: `36e9c5f42d5a139bc1f75daa886415066e69b205` (local disposable repository).
- Original, corrected temporary report SHA-256: `6b64ba37c943513e4b86638db5cc3f28463b984592315cce0742bd3785291423`.
- Input candidate: untracked `double.py` containing `def double(value: int) -> int: return value * 2`, and `test_double.py` with `double(3) == 6` and `double(-4) == -8` assertions. The TDD RED run failed with `ModuleNotFoundError: No module named 'double'`; after adding the implementation, 2 tests passed. The base fixture's staged OMC TDD check exited 0.
- Three independent copies had the same reviewed `base_commit`, frozen snapshot SHA-256 `be46d8c1bde1176b96dc6241d3d8d2e0ee60b94701173698bbc09155030bbae0`, and `candidate_scope_sha256` `34991df6073f11429482668a7c066cd5df73eefecdd51920c3ecee0502b44e4c`. The frozen diff was displayed for one copy; identical snapshot hashes were observed for the other two.
- The real `capture-review` → `seal-review-output` → `record-review APPROVE` CLI path created the receipts below. The APPROVE body was **synthetic, manually authored review output**: there was **no independent peer reviewer**, no complete `$omc-review` skill run, and verification-receipt binding is absent (`verification_receipt_sha256=null`). This tests the validator's identity behavior, not review quality or the full approval chain.

| Fixture | Review receipt SHA-256 | Post-review action | Current candidate scope SHA-256 | Direct `validate-ship` result | Tests |
|---|---|---|---|---|---|
| `normal-commit` | `e202bddb39890d9a2cb713b8336e91941767ac716a6b2de8e934de6207f8d7ee` | Commit reviewed files unchanged | `34991df6073f11429482668a7c066cd5df73eefecdd51920c3ecee0502b44e4c` | exit 0, `READY`, `changed_paths=[]` | 2 passed |
| `comment-change` | `61251a4a9bd80f49c4a795548f27ec0b4cb5af115280c38f6c05341fcd83bf64` | Add one non-executable comment to `test_double.py` | `0687f5ac99a5bb99e13971ac72e22cf540f1e4808f0f166137204612c4691c12` | exit 2, `BLOCKED`, `review_stale`, `changed_paths=[test_double.py]` | 2 passed |
| `code-change` | `31e939c754051cc12fe42024a2ec197f0180103a775a4651a3ad354b29ad5ac0` | Change `double` from `* 2` to `* 3` | `9455beeb9643db9493f3c570c30cc8a06b982aa2bcc4f26b62b39a0663495871` | exit 2, `BLOCKED`, `review_stale`, `changed_paths=[double.py]` | 2 failed |

All three copies returned `READY` before the post-review action. The previous review receipts remained intact after mutation; only the **current candidate's ship eligibility** was blocked by `review_stale`. The normal commit retained the same canonical candidate scope hash.

Command surface (each `--target` was one disposable fixture copy):

```text
python3 scripts/omc_review_snapshot.py capture-review --target <fixture> --base-commit <base>
python3 scripts/omc_review_snapshot.py show-review-diff --review-snapshot <snapshot> --review-snapshot-sha256 <snapshot-sha>  # first copy
python3 scripts/omc_review_snapshot.py seal-review-output --target <fixture> --review-snapshot <snapshot> --review-snapshot-sha256 <snapshot-sha> --review-output <synthetic-review-file>
python3 scripts/omc_review_snapshot.py record-review --target <fixture> --review-snapshot <snapshot> --review-snapshot-sha256 <snapshot-sha> --review-evidence <evidence> --review-evidence-sha256 <evidence-sha> --verdict APPROVE
python3 scripts/omc_review_snapshot.py validate-ship --target <fixture>
```

For the committed control, current candidate reconstruction used the reviewed base plus `--candidate-commit HEAD` when capturing the post-commit comparison snapshot. No actual push or deployment occurred.

## oh-my Launch observation (separate path)

In the disposable Launch fixture, two real `oh-my-claudecode:code-reviewer` subagents ran under Bedrock Opus 5 and passed the standards and spec axes. After a comment-only change to `test_double.py`, a new `/oh-my-claudecode:launch` invocation ran `python3 -m unittest discover` and the yard audit and emitted a completion report. Its tool trace showed no read of the prior reviewer evidence and no exact-candidate comparison. A previous plain-language attempt to invoke the Launch skill through the model's Skill tool was denied by `disable-model-invocation` and is excluded from this observation.

This supports only: **the tested new-Launch closeout path did not visibly bind the prior review to the current candidate**. It does not prove that all oh-my workflows lack approval invalidation; other paths, including PRD/criteria invalidation, were not tested here. The plugin commit at the earlier invocation was not frozen in this record.

## Evidence custody and interpretation

This Markdown preserves the executed command shape, observed exit/results, candidate and receipt hashes, source boundary, and exclusions. **The raw receipts and CLI logs are not archived here**; their original paths were under disposable `/private/tmp` workspaces or local Claude transcripts. Consequently this repository record is not a self-contained cryptographic replay of the original runs. Re-execution with a fresh isolated repository is required for independent conformance confirmation.

No natural-work cohort event, installation identity, metric definition, or user-outcome record was changed. The OMC result establishes local candidate-identity conformance for these three synthetic cases. It does not measure false-valid approval rates in natural work, the human cost of comment-only invalidation, missed meaningful defects, or product effect. Any adoption of an external pattern requires separate bottleneck evidence, plan, and human approval.
