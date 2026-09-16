# Completion observation incident 001

## Status and boundary

- Study: `completion-quality-live-20260913-v2`
- Recorded on: `2026-09-16` (Asia/Seoul)
- Incident classification: `OBSERVATION_INVALID`
- Official cohort closure: not produced

This record is outside the frozen cohort. It does not amend the registration,
policies, starts, completion snapshots, outcomes, failures, or closure code.
It is containment evidence only and is not a replacement cohort closure or
evidence that OMC improves completion quality.

## Trigger

The three policies bind OMC `0.2.7`, source revision
`4df95e46a02460f9aeb625a547299a773b6972fc`, source SHA-256
`c2c2d6c2fa244eb36d8d06ece59886e04f6d5487620274227110bf5e4d5bf4e6`,
and repository-specific install receipts. All three repositories now have OMC
`0.3.0` receipts. The current `_live_policy()` path returns
`live_install_identity_invalid` for every registered repository.

The current receipts do not encode a reliable replacement timestamp. This
audit therefore does not infer the transition instant for each repository or
declare an earlier record corrupt solely because the current receipt changed.
The cohort can no longer collect or close through its frozen policy using the
current install identity, so the current observation surface fails closed.

## Frozen registration

- Custody path: `/Users/noseunglae/Downloads/dev/omc-observation-custody/completion-quality-live-02/registration.json`
- File SHA-256: `8d7b41950789fb0709d3320de3de5d28df6903b6547006c384c454b98c959cc8`
- Validated self-hash: `67b169155db9e2afce0760fd6dd18bfd7ce76f67b2418e5df66c34e0fcdbb073`
- Window: `2026-09-12T18:15:00Z` through `2026-09-26T18:15:00Z`
- Closure deadline: `2026-09-27T18:15:00Z`
- Roster: `ai-cs`, `okx-maker-grid-bot-wind`, `sixshop3-storefront-fe`

The registration self-hash and all three policy self-hashes passed the existing
validators. This audit did not change their bytes.

## Install identity mismatch

| Repository | Frozen policy self-hash | Frozen receipt SHA-256 | Current receipt SHA-256 | Result |
| --- | --- | --- | --- | --- |
| `ai-cs` | `72f5342192920ac197c0ca5d87f4067a6d2b6b95209f7e4f67feaf2bbad33ff3` | `f69ca8017b84948e952f3f9e4d28c02505df9094d503f91d61d38dc8237c4a9a` | `1a11f6e1dbe5242b0b08ba3ee3addacf81fbb0b407f08607ce30c09deedd9b63` | invalid |
| `okx-maker-grid-bot-wind` | `bc7a47d3b441cb1f6f7114933e82d672f9993ff268d1da65b3bba8df91d33992` | `253266eb31e1e384703ed21bbd7143dde60abc2a169bf62170d49e3401a69e50` | `08678c51d8e90ce4a9f5029727cfaf6a70453589bd96ac616db7df7dce8acbeb` | invalid |
| `sixshop3-storefront-fe` | `9c9f3ac504d2f56a2ed305bdabb40d18ece3a71304e693d384c941c9e93e4d60` | `4853d3f0f6ef06fc64fb87826bcb4d2a4aaa34e7ac5d6710b8020fc6c5e7b16c` | `672e6fb52673a58b35e5b309151bb72d0ef790a0f36297562ffc84b66dbc6535` | invalid |

## Ledger inventory

All 17 live records and all 5 capture-failure records passed the existing
self-hash validators. Integrity of an individual record does not establish
cohort validity.

| Repository | Starts | First completions | Outcomes | Failures |
| --- | ---: | ---: | ---: | ---: |
| `ai-cs` | 6 | 5 | 1 | 0 |
| `okx-maker-grid-bot-wind` | 0 | 0 | 0 | 2 |
| `sixshop3-storefront-fe` | 2 | 2 | 1 | 3 |

The outcomes belong to `8055142800ce4a8e82fbd2db25b9ef0f` and
`2abad46720464145b55f7d1439b9d653`. Six starts have no outcome; start
`e8d20466e48f4e6aa975d76163db387f` also lacks a first-completion snapshot.
These are inventory facts, not a cohort verdict.

## Evidence file SHA-256 manifest

Paths below are relative to each repository's `.omc/observations/` directory.

```text
# ai-cs
d9d8f6c036ee6d5e0a8c8ad3e86080197bce88ae3d7801cfc34ba29c963504b4  live/a1b053c7405e413b8cbce68d8772c9cc/start.json
00e076ec0fdccf9c75acab2836c34a0e2fd2ff78422496899c00deeb3467a748  live/a1b053c7405e413b8cbce68d8772c9cc/first-completion.json
fe286e572d6d425926bad3fd2759af51a4126f52d9f4e2b3d37baa3d3f7ca966  live/905400e7983745ce9ca71a687e4f2253/start.json
c3d77523cb037f792486d6f52da0bc7259ee1cb92e8dee3d76506fdf3d54325b  live/905400e7983745ce9ca71a687e4f2253/first-completion.json
df93324310e7e7e6749f806a77a3c8699b946b6372e9ed851e0dac5aa3a93f9b  live/8055142800ce4a8e82fbd2db25b9ef0f/start.json
edbfef6f4b53b67b2f5b29bd3a0b853a74650c80bf0441e9c659e0c3e0062807  live/8055142800ce4a8e82fbd2db25b9ef0f/first-completion.json
288c582653b18d7e1c90da561c2045ca14e85117b905a58b0260811e8a0e4c48  live/8055142800ce4a8e82fbd2db25b9ef0f/terminal.json
d468d55a899d3acd50680da870f963fc205b2165f28eb0aabef79dfdc8ed4dc7  live/3b486bdce23644bebd1217f28ce0a34e/start.json
e1693bc991a479848674efa874242f7d327bf1d254ba44c6776c6e4b5bfad232  live/3b486bdce23644bebd1217f28ce0a34e/first-completion.json
570db414535b36f28da4f8bdb48b487e19c8bd2484f67af937afb98b274b283f  live/e8d20466e48f4e6aa975d76163db387f/start.json
fc4067edeaa266ebc0b1089e77b9e8675fea5bb40843b0a9c9799b8859ef01c3  live/a2186b46c6c5404cbfb169a6ec0c2ad3/start.json
77c186a9650b8782475957eb62e773b6523f29a4e17a943c48668aee868c04e1  live/a2186b46c6c5404cbfb169a6ec0c2ad3/first-completion.json

# sixshop3-storefront-fe
428f936f0d616eb7bb062d02cc7a1c6449af657835832a7e95383ecc4c13e73a  live/2abad46720464145b55f7d1439b9d653/start.json
0e8c6b81f83ce5067470de52cdc5b24ccf100cfcc9bf8831f558a9cce30c62d1  live/2abad46720464145b55f7d1439b9d653/first-completion.json
94321ab59e8d2e0548dbd3ac0f0c3a0168fc6aead529a49fbfa51b6bbda669ac  live/2abad46720464145b55f7d1439b9d653/terminal.json
906deef3a3e9f52d4b2e874c18d645cafc2ecfff6c9de324ad3cb08263a636eb  live/c7803c7beec140009062a2e3e3fd52fa/start.json
bb7ce5ae808c7a2b2c785d7100cd4d6c6b463f5ae14e6ff29f791a63beb85144  live/c7803c7beec140009062a2e3e3fd52fa/first-completion.json

# okx-maker-grid-bot-wind failures
686ab1e414e680facb7149d83021f1d91996be1b0e35cfe5ab11735b9e70246e  live-failures/15219edd3fc42c77c359a2a82cfbfe1930ca02634521051650d9ec0d0eb811e3.json
2bc3dcdf1e7e3aca37e0220486d24c39293866e7f3ef4fe9329ae069fdad33a0  live-failures/b252ee2e5bae164d2aabc9bc2bc40e3bdccb343e52bf1cae47bbde5c1df01b1a.json

# sixshop3-storefront-fe failures
207127ffaf5c3737d0477661b1699e6a0064ac260868dc9aaa93b1e36bc2de5e  live-failures/12bcd5abba532661b633f70a4dba38239af1213dddcd33d825ed2a97c87fe38c.json
89361ea393fd91da9cd686c873e9d3bbec7991ce62377aa1d0fc2e96654f56c7  live-failures/aabd2be7f8173cae41406485ba9db16daef1388e2fa4ce05ef067fd531549bf7.json
4893df67e0f8e7b688810e61882563e50d351cd60fb283d9fb965c9196d72f0f  live-failures/f2d148b3b85580264ebea938dcae034e429373df57c6daa48f002c03da686277.json
```

## Decision boundary

1. Do not edit or replace frozen cohort artifacts.
2. Do not run official `live-close` while install identities fail validation.
3. Do not promote intact record hashes into product-effect evidence.
4. Resume only under a new study ID, registration, future T0, bound install
   identities, and a preregistered installation lifecycle rule.

## Unverified facts

- Exact receipt replacement time per repository.
- Whether each start preceded its repository's receipt replacement.
- Historical receipt recovery from rollout backups.
- Any official closure or product-effect claim.
