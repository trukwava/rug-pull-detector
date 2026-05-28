# Methodology

*Pre-Rug Risk Signals: A reproducible classifier for liquidity-removal rug pulls on Ethereum.*

---

## 0. Abstract

This document describes the methodology behind a classifier that estimates the probability that a newly deployed ERC-20 token on Ethereum will execute a liquidity-removal rug pull within 30 days of its first decentralized exchange (DEX) pool creation. The classifier uses only features observable at the moment of pool creation (`T₀`), ensuring that no information from the post-launch period leaks into the prediction. The intended use is *investigative triage*: surfacing high-risk tokens for human review, not issuing final judgments.

The document is organized around four claims about what this study does and does not establish:

- **It establishes**: that a small set of on-chain features observable at `T₀` carries non-trivial discriminative signal for one well-defined subtype of fraud (liquidity rugs).
- **It does not establish**: that those features are *causally* implicated in the fraud.
- **It does not establish**: that the classifier generalizes to (a) other chains, (b) other fraud subtypes, or (c) future periods without retraining.
- **It does not establish**: a base rate for crypto fraud in general. The base rate reported is a lower bound on one subtype.

Each of these claims is unpacked below.

---

## 1. Research questions

**Q1 (prediction).** Given the set of features observable on-chain at the moment a new ERC-20 token's first Uniswap V2 or V3 pool is created, can a classifier reliably distinguish tokens that will execute a liquidity-removal rug pull within 30 days from tokens that will not?

**Q2 (interpretation).** Which features contribute most to the classifier's discrimination, and do those features admit a defensible interpretation in terms of known rug-pull mechanics?

Q1 and Q2 are kept separate because predictive accuracy and causal interpretability come apart. A feature may be highly predictive without being causally implicated in the outcome (it may simply correlate with one); conversely, a feature with a clear mechanistic story may be weakly predictive in any given period because adversaries adapt around the easily detected variants. Treating Q1 and Q2 as one question is a common source of overclaiming in applied ML, and is one of the things this document tries to avoid.

---

## 2. Operational definition of "rug pull"

A token is labeled a rug pull if all of the following hold within 30 days of the token's first Uniswap V2 or V3 pool being created (`T₀`):

- **(D1)** A single on-chain transaction signed by an address belonging to the *privileged set* — defined as the set containing the pool deployer, the token contract deployer, and any address that was the top-1 holder of the pool's LP tokens at any point in `[T₀, T₀ + 30d]` — removes liquidity tokens redeemable for ≥80% of the pool's reserve value at the moment immediately preceding the transaction.
- **(D2)** The token's price, measured as the geometric mean of swap-execution prices, drops by ≥90% within the 24-hour window following the removal transaction in (D1).
- **(D3)** No address in the privileged set adds liquidity to the pool, or to any new pool for the same token contract, within the 30-day window following the removal transaction in (D1).

### 2.1 Rationale

The definition is deliberately conservative.

**(D1)** requires *explicit* liquidity removal by a privileged party. This excludes tokens that fail through gradual deployer dumping, abandoned development, or organic market collapse — phenomena that are sometimes fraudulent but whose on-chain signatures differ enough that bundling them into a single classifier would produce a noisier label and a less defensible model.

**(D2)** ensures that the liquidity removal *coincided with concrete harm to retail holders*. A deployer removing liquidity from a pool that nobody had bought into is not, in any meaningful sense, a fraud against retail. The price-drop condition also excludes legitimate liquidity migrations in which the projected price impact is small because liquidity is re-added on a different venue.

**(D3)** distinguishes rug pulls proper from migrations. Some legitimate projects move liquidity between pools — for example, from a launch pool with restrictive parameters to a permanent pool with more depth. (D3) rules these out.

The conjunction (D1) ∧ (D2) ∧ (D3) defines what the crypto-security literature usually calls a "hard rug" or "liquidity rug." See, in particular, Mazorra, Adan, and Daza-Olivella (2023) and Xia et al. (2021) for closely related operationalizations.

### 2.2 What the definition excludes, and why it matters

Soft rugs, slow rugs, exit scams without LP removal, and honeypots are all out of scope. The base rate of "rugs" computed under this definition is therefore a *lower bound* on the population of token launches that defrauded retail buyers.

This is the right tradeoff for the present project, but it has implications for downstream use. A user of the classifier should not infer from a low predicted probability that a token is "safe" — only that it does not match the signature of a liquidity rug in particular. Other fraud subtypes (notably honeypots, where the contract prevents holders from selling) are detected by different methods and are out of scope here.

---

## 3. Data and labeling strategy

### 3.1 Data sources

| Source | Use | Access | Provenance |
|---|---|---|---|
| Etherscan API | Token contract bytecode, deployer address, deployer wallet history, transfer logs | Free tier with API key | Snapshots cached with fetch date in `data/raw/etherscan/<date>/` |
| The Graph — Uniswap V2 subgraph | Pool creation events, mint/burn events, LP token transfers, swap events | Public endpoint | Cached |
| The Graph — Uniswap V3 subgraph | Pool creation, position events, swap events | Public endpoint | Cached |
| Honeypot.is API | Contract red-flag detection (mintable, pausable, blacklist functions, owner-only restrictions) | Free tier | Cached. See §10 for a note on circularity. |

All API responses are cached locally with the fetch timestamp, so the analysis can be reproduced from the cache without re-hitting the APIs. Where two sources disagree (e.g., on pool creation timestamp), the on-chain event log from The Graph is treated as authoritative.

### 3.2 Labeling: self-labeling as the primary strategy

Labels are constructed by applying the operational definition in §2 directly to the on-chain data fetched from the sources above. For each token in the candidate universe:

- The privileged set is computed from token-deployment, pool-creation, and LP-token transfer events.
- For each transaction signed by an address in the privileged set, the pool's reserve composition before and after the transaction is reconstructed from the indexed swap and mint/burn history.
- (D1), (D2), and (D3) are then evaluated mechanically.

This *self-labeling* approach has two advantages over relying on external rug-pull databases:

1. The label is auditable. Every positive can be traced back to the specific on-chain events that triggered it.
2. The label is internally consistent. Positives and negatives are defined by the same evidence stream, with no ambiguity about which tokens were "investigated" or "noticed."

The disadvantage is that the label depends on the quality of the operational definition. Errors in the definition propagate uniformly through the labeled set. §10 discusses the residual risks.

### 3.3 Validation of the labeling pipeline

To check the self-labeling pipeline, two validation passes are run:

1. **Manual review of a random sample of 100 positives.** A confusion-matrix-style summary is reported. Expected outcomes: most cases confirm; some cases are edge cases at the threshold parameters (the 80% / 90% / 30-day cutoffs); a small number reveal bugs in the labeling code, which are then fixed.

2. **Cross-reference against external sources, where available.** Where external rug-pull lists (e.g., the De.Fi REKT database, academic replication datasets such as Mazorra et al. 2023 and Cernera et al. 2023) are accessible and licensable, the overlap with self-labeled positives is reported. Tokens that appear in external lists but not in the self-labeled positives are reviewed and the operational definition is interrogated against the disagreement.

External sources are used as a *check on*, not the *source of*, the labels. This avoids any dependency on the continued availability or quality of any single external dataset.

---

## 4. Sample construction

### 4.1 Universe

The candidate universe is every ERC-20 token with at least one Uniswap V2 or V3 pool on Ethereum mainnet whose `PairCreated` (V2) or `PoolCreated` (V3) event occurred between **2022-07-01** and **2025-12-31**. The window starts after the May 2022 Terra/LUNA collapse, which produced unusual market conditions and a one-time spike in token launches that does not reflect typical conditions. The end date leaves a clean tail of 30+ days of post-creation data available for label construction at submission time.

The **analyzed sample for this submission** covers pool creations between **2024-06-01 and 2024-06-03 (UTC)**, a three-day slice of the broader window described above. The same ETL and labeling code runs on the full window without modification; the reduced scope was chosen so the end-to-end pipeline could be exercised against real on-chain data within the project's time budget. After applying the §4.2 inclusion filters, the sample contains **1,213 tokens** (1,103 on Uniswap V2, 110 on Uniswap V3). The reduced scope is itself a limitation; see §10. Studies of comparable Uniswap windows on Ethereum have analyzed populations on the order of tens of thousands of tokens (e.g., Xia et al. 2021; Mazorra et al. 2023); extending this work to that population is the most natural next step.

### 4.2 Inclusion criteria

A token is included in the analysis only if:

- It has at least one Uniswap V2 or V3 pool paired against WETH, USDC, USDT, or DAI. (Pools paired against obscure tokens are excluded to avoid pricing pathologies and quote-side rugs.)
- The pool reached at least $5,000 in total liquidity at some point. (Pools that never attracted any meaningful capital are not relevant to a fraud-detection use case.)
- The pool has at least 30 days of post-creation data available in the indexed history at the time of label construction.

### 4.3 Class balance

Under the operational definition in §2, the labeled base rate on the analyzed sample is **26.1%** (317 rugs across 1,213 tokens; Wilson 95% confidence interval [23.7%, 28.7%]). The base rate is highly asymmetric across Uniswap versions: **28.7% on V2** (317/1,103) and **0% on V3** (0/110). The V3 zero is not a real-world observation; it is a detection artifact of how the privileged-set is constructed when V3 LP positions are NFTs owned by a single position-manager contract — see §10 for details.

These figures are consistent in order of magnitude with published estimates for memecoin-heavy Ethereum populations (Xia et al. 2021; Cernera et al. 2023; Mazorra et al. 2023), though the strict (D1)∧(D2)∧(D3) operational definition is narrower than the looser ones used in some of that work, which would push our rate toward the lower end of literature reports if uniformly applied.

For model training, the dataset is *not* artificially rebalanced. SMOTE-style oversampling is rejected on the grounds that it (a) distorts the calibration of the resulting model and (b) introduces synthetic feature combinations that may not be realistic. Class weight is instead handled via cost-sensitive learning (positive weight proportional to inverse class frequency) where the model permits.

---

## 5. Feature engineering

All features are computed using only data observable at `T₀` — the moment the token's first qualifying pool is created. Features that depend on post-`T₀` information (e.g., subsequent volume, holder growth, social mentions) are *excluded by construction* to prevent leakage.

Features fall into five families. Each is motivated by a hypothesis about rug mechanics; whether the hypothesis is borne out is a question for §8.

### 5.1 Contract features (Etherscan + Honeypot.is)

- `contract_is_verified` — boolean
- `contract_has_mintable_owner` — owner can mint new supply
- `contract_has_pausable` — owner can pause transfers
- `contract_has_blacklist` — owner can blacklist addresses
- `contract_owner_renounced` — ownership has been transferred to the zero address
- `contract_proxy_pattern` — contract is upgradeable via proxy
- `contract_bytecode_similarity_to_prior_rugs` — Jaccard similarity to a fingerprint set built from rugs labeled *prior to* `T₀`, computed using a temporally-restricted reference set (see §10)

### 5.2 Deployer features (Etherscan)

- `deployer_wallet_age_days` — days between deployer's first on-chain transaction and `T₀`
- `deployer_tx_count` — total transactions by deployer prior to `T₀`
- `deployer_funded_by` — categorical: CEX hot wallet, mixer (Tornado Cash and similar), other contract, or direct
- `deployer_prior_token_deployments` — number of ERC-20 tokens previously deployed by this address
- `deployer_prior_rugs` — number of prior token deployments by this deployer that satisfy the rug definition, computed using only rugs labeled *prior to* `T₀`

### 5.3 Pool features (Uniswap subgraphs)

- `initial_liquidity_usd` — USD value of liquidity at the first mint event
- `liquidity_lock_duration_days` — days until LP tokens unlock, where lock is detectable on-chain via known lockers (Unicrypt, Team Finance, etc.); 0 if unlocked
- `lp_holder_concentration_t0` — fraction of LP tokens held by top-1 holder immediately after pool creation
- `pool_pair_quote_token` — WETH / USDC / USDT / DAI
- `pool_creation_hour_utc` — hour of day; tested as both numeric and categorical
- `pool_creation_dow` — day of week

### 5.4 Token supply features

- `total_supply` — log-scaled
- `top5_holder_concentration` — fraction of total supply held by top 5 non-pool addresses
- `holder_count_t0` — count of non-zero holders at `T₀`
- `share_supply_in_pool` — fraction of total supply locked in the launch pool

### 5.5 Network / market context features

- `eth_gas_price_t0` — gas price at `T₀` (coarse market-stress signal)
- `concurrent_token_deployments_24h` — count of other token deployments in the prior 24 hours (proxy for launch bunching, useful for detecting batched fraud operations)

All feature builds are implemented in SQL (DuckDB-compatible) in `sql/03_features.sql`. The choice to do feature engineering in SQL rather than pandas is deliberate: SQL is what a reviewing analyst will read most fluently, and window-function-based feature builds are easier to audit than equivalent pandas pipelines.

---

## 6. Model

### 6.1 Architecture

Two models are trained:

1. **Logistic regression with L2 regularization** — a calibrated, interpretable baseline.
2. **Gradient-boosted trees (LightGBM)** — the production model.

The two models exist for different purposes. The logistic baseline establishes a floor and produces interpretable coefficients. The boosted model is what the CLI uses; its outputs are explained using SHAP values.

A neural model is not used. The dataset size and the feature structure do not justify the additional complexity, and the resulting model would be harder to defend in an investigative context where feature attributions matter as much as scores.

### 6.2 Calibration

Both models are calibrated using isotonic regression on a held-out calibration fold, separate from both the training fold and the test fold. Calibration matters because the downstream use case requires interpretable probabilities, not just a ranking.

---

## 7. Evaluation

### 7.1 Temporal split

The dataset is split *temporally*, not randomly. The split is percentile-based on `T₀`:

- **Training set**: the earliest 60% of tokens by `T₀`
- **Calibration set**: the next 20%
- **Test set**: the latest 20%

Percentile-based ordering preserves the strict temporal property — every test-set `T₀ ≥` every calibration-set `T₀ ≥` every training-set `T₀` — while making the split scale-invariant: the same split logic applies whether the analyzed sample spans three days or three years.

A temporal split is essential. Random splits would allow the model to learn features specific to particular epochs of crypto market structure (gas regime, dominant scam patterns, retail attention cycles), producing inflated estimates of out-of-sample performance. The specific time boundaries that result from applying this split to the analyzed sample are reported in §8.2.

### 7.2 Metrics

The primary metrics are:

- **Precision-at-k** for k ∈ {10, 50, 100} — the fraction of the top-k highest-scored tokens that are actual rugs. This metric is privileged because it matches the downstream use case (an investigator reviewing the top-flagged tokens).
- **AUC-PR** — area under the precision-recall curve, appropriate for imbalanced binary classification.
- **Calibration plot** — predicted probability vs. empirical frequency, binned into deciles.
- **AUC-ROC** — reported for comparability with prior literature, but de-emphasized given class imbalance.

Brier score is reported but not used for model selection.

### 7.3 Feature stability

For each top-decile prediction, the SHAP feature attribution is logged. A separate notebook (`03_model_validation.ipynb`) reports the stability of the top-5 features across bootstrap resamples of the training set, as a check on whether feature importances are artifacts of any particular sample.

---

## 8. Findings

The numbers below come from a single end-to-end pipeline run against a three-day sample of June 2024 (described in §4). They should be read with the implementation-specific limitations in §10.2 in view — most importantly, the V3 LP-NFT detection gap (which makes the V3 base rate uninformative in this version) and the narrow sample window (which prevents any claim about generalization across market regimes).

### 8.1 Reporting plan

When the model is trained, this section will report:

- **Discrimination.** AUC-PR on the held-out temporal test set (primary metric per §7.2), with AUC-ROC reported alongside for comparability with prior work.
- **Calibration.** Reliability diagram across deciles of predicted probability, and Brier score.
- **Operating points.** Precision-at-k for k ∈ {10, 50, 100}, framed in the language of an investigative triage workflow: "of the top k tokens flagged, what fraction were actually rugs."
- **Feature attribution.** Top features by mean absolute SHAP value, with stability across bootstrap resamples reported in `notebooks/03_model_validation.ipynb`.
- **Null results.** Features hypothesized in §5 to matter that did not contribute meaningfully to the model. These are recorded explicitly so the analysis is not a post-hoc story over only the confirming results.

### 8.2 Results

Trained on the June 2024 sample described in §4. Temporal split (per §7.1): n_train = 727 (T₀ ≤ 2024-06-02 18:40 UTC), n_calib = 242, n_test = 244 with 66 positives.

| Metric                  | Logistic regression | LightGBM (production) |
|-------------------------|---------------------|-----------------------|
| AUC-PR (primary, §7.2)  | 0.316               | **0.403**             |
| AUC-ROC                 | 0.596               | **0.703**             |
| Brier score             | 0.189               | 0.178                 |
| Precision @ top 10      | 0.30                | **0.40**              |
| Precision @ top 50      | 0.34                | 0.36                  |
| Precision @ top 100     | 0.40                | **0.41**              |

The held-out test set has a 27.0% base rate; any AUC-PR above that is real lift.

**Interpretation.** LightGBM shows a meaningful lift over the test-set base rate on both AUC-PR (0.40 vs 0.27 baseline) and AUC-ROC (0.70 vs 0.50 baseline). The logistic baseline provides minimal lift on AUC-ROC (0.60), suggesting the predictive signal lives in feature *interactions* that a 12-feature linear model in standardized coordinates cannot capture — consistent with the hypothesis in §5 that the relevant rug patterns are conjunctive (e.g., short-deployer-history *and* low initial liquidity *and* high LP-holder concentration, simultaneously).

**Calibration.** LightGBM's predicted probability tracks observed rug rate within roughly 5 percentage points across non-trivial deciles (decile 5 has n = 1 in the test set and is uninformative). The logistic model's outputs cluster near the base rate (only deciles 0 and 1 are populated, with 243 and 1 observations respectively), which is consistent with its weak discrimination — it is not a calibration failure so much as the model declining to commit to differentiated predictions.

**Precision at k.** The LightGBM precision-at-100 of 0.41 is the most operationally relevant figure. An investigative triage workflow that reviews the top 100 flagged tokens from a comparable candidate stream would, on this evidence, find roughly 41 actual rugs in those 100 — versus roughly 27 by random selection from the same population — a 1.5× lift.

**Cautions on these numbers.** They come from a single three-day temporal slice (2024-06-01 to 2024-06-03) and have not been validated against other market regimes. The model has not been tested for stability under distribution shift (§9.2), and the top feature attributions have not been audited for spurious associations on this sample. Generalization beyond the analyzed window is not established. The five additional features described in §5 but not yet implemented (see §10) may meaningfully change feature ranking once added; that is one of the most important next-step directions.

Raw metrics including per-decile calibration are saved at `reports/training_results.json`. Reproducing them requires only `python -m rug_detector pipeline run-all` with the same date window.

---

## 9. Limitations

### 9.1 Selection bias from the operational definition

The labeled set captures exactly the rugs that satisfy the operational definition in §2. Rugs that defraud retail through mechanisms that do not satisfy (D1)–(D3) — slow rugs, honeypots, exit scams routed through bridges, governance-attack rugs — are absent from the positive class and present in the negative class. The classifier therefore should not be treated as a general fraud detector.

### 9.2 Distribution shift

Adversaries adapt. A feature that strongly predicts rugs in 2022–2024 (e.g., "deployer wallet less than 24 hours old") may have weaker predictive power in 2026 if deployers learn to age their wallets before launch. Publishing the methodology accelerates that adaptation; see §11.

### 9.3 Asymmetric error costs

A false positive flags a legitimate project as a fraud. The cost is non-trivial: reputational damage, legal exposure for whoever acts on the flag, and erosion of trust in any institution that uses the classifier. The model is therefore designed as a *triage* tool: it produces a ranked list with feature attribution, and a human reviewer is required before any action is taken. The CLI output format reinforces this — it shows reasons, not verdicts.

### 9.4 Causal vs. predictive interpretation

The model identifies feature combinations that *correlate* with rug outcomes. It does not identify features that *cause* rugs. This distinction matters in any setting where the basis for a flag needs to be justified beyond "the model said so." The feature-attribution outputs are descriptive, not causal.

---

## 10. Threats to validity

Several threats are taken seriously in this work. Each is partially mitigated, none is eliminated. The first five are properties of the methodology as designed; the last four are properties of *this particular implementation* and arose during the real-data run that produced §8.2.

### 10.1 Methodological threats

| Threat | Mitigation | Residual risk |
|---|---|---|
| **Label leakage** (features encoding post-`T₀` information) | All features computed strictly from data available at `T₀`; SQL feature builds audited for forbidden joins on post-`T₀` events | Subtle leakage through cached external sources cannot be fully ruled out |
| **Bytecode-similarity temporal leakage** | The reference set for `contract_bytecode_similarity_to_prior_rugs` is restricted to rugs labeled *strictly before* `T₀`, so the feature can only encode information available at the moment of launch | The reference set grows over time, so the feature has different distributions in different epochs — handled by training/test temporal split, but worth noting |
| **Honeypot.is circularity** | Honeypot.is uses its own heuristics for contract-level flags. Some of its heuristics may overlap with patterns the classifier is trying to learn, which inflates apparent feature importance for contract features | Could be partially mitigated by ablating Honeypot.is-derived features and reporting a model trained only on raw-bytecode-derived features |
| **Label noise** | 100-case manual review of automated positives; cross-reference against external sources where available | Rare edge cases (contract redeployments with same bytecode at different addresses, multi-step rugs across multiple transactions) may be mislabeled |
| **Selection bias in negatives** | Random sample of `rug = 0` tokens reviewed for late rugs at submission time | Cannot detect rugs that occurred after the review cutoff |

### 10.2 Implementation-specific limitations of the §8 run

The four limitations below are not properties of the methodology — the same code runs without modification on a longer window and with the missing data sources wired up. They are properties of the specific submission run and should be read as scope cuts rather than design choices.

| Limitation | Why | Effect on §8 numbers |
|---|---|---|
| **Three-day analyzed window** (2024-06-01 to 2024-06-03) | End-to-end pipeline demonstration within project time budget. The full 2022-07 → 2025-12 window is supported by the same code; running it requires roughly two days of wall-clock at free-tier Etherscan + Graph quotas. | Generalization to other market regimes (crypto winter, peaks, post-update Uniswap V4 if extended) is not established. The reported AUC-PR / AUC-ROC are estimates on one market regime only. |
| **V3 LP-NFT modeling gap** | Uniswap V3 LP positions are non-fungible tokens owned by a single position-manager contract (0xc36442b4…). Burn events therefore carry the position manager's address as `sender`, not the EOA that controls the position. The privileged_set constructor cannot identify V3 LP owners without modeling NFT ownership transfers. | All V3 rugs are systematic false negatives in this version. The detector is effectively V2-only. The §4.3 V3 base rate of 0% is a detection artifact, not a real-world observation. |
| **Subgraph block_number unavailable** | The Graph V2/V3 subgraphs do not expose `block_number` or `log_index` on event entities. The ETL stores zero as a placeholder. | The `pool_reserves` view orders cumulative reserves by `(block_time, tx_hash)` instead. Intra-block ordering between distinct transactions is approximate; rugs that depend on intra-block order relative to a same-block swap could be mislabeled. In practice rugs dominate their block (deployer drains in a single tx with no concurrent activity), so the approximation rarely changes labels — but it is not zero risk. The canonical fix is to source `block_number` from Etherscan event logs in a future revision. |
| **Five features unimplemented** | The following features described in §5 require data sources beyond the V2/V3 subgraphs and basic Etherscan endpoints we use: `deployer_wallet_age_days` (needs deployer's full tx history), `top5_holder_concentration`, `holder_count_t0`, `share_supply_in_pool` (all need a token-holder enumeration not exposed by free-tier endpoints), and `bytecode_similarity_to_prior_rugs` (needs a separate Python similarity pipeline). | The model in §8.2 trains on 12 features rather than the 18 described in §5. The §5.1 / §5.2 hypothesis that deployer history and holder concentration matter is only partially testable from the implemented set. Adding these features is the single most promising path to model improvement and is unlikely to require methodological changes. |

---

## 11. What this study does and does not license

A reader of the classifier outputs is licensed to conclude:

- That the flagged token shares observable on-chain features with historical liquidity rugs.
- That those features warrant human review of the token.

A reader is *not* licensed to conclude:

- That the flagged token is a fraud.
- That the deployer intended to commit fraud.
- That the unflagged tokens are safe.
- That the classifier's outputs constitute evidence of wrongdoing in any legal or quasi-legal sense.

The careful preservation of these distinctions is, I think, the most important methodological discipline in applied fraud detection. Models that conflate the two — that present prediction as adjudication — tend to produce both false confidence and real injustice.

One further note on adversarial use: publishing the full feature list and methodology gives sophisticated deployers a recipe for evasion. The countervailing reason to publish is that the same transparency lets defenders, regulators, and researchers audit the model, contest its outputs, and improve on it. The right resolution is not to obscure the methodology but to assume the model will degrade against sophisticated adversaries over time and to plan for retraining accordingly. This is one reason the project is structured for reproducibility from end to end rather than as a black-box service.

---

## 12. References

- Mazorra, B., Adan, V., & Daza-Olivella, V. (2023). *Do not rug on me: Zero-dimensional Scam Detection.* arXiv:2201.07220.
- Xia, P., Wang, H., Gao, B., et al. (2021). *Trade or Trick? Detecting and Characterizing Scam Tokens on Uniswap Decentralized Exchange.* Proc. ACM Meas. Anal. Comput. Syst.
- Cernera, F., La Morgia, M., Mei, A., & Sassi, F. (2023). *Token Spammers, Rug Pulls, and Sniper Bots: An Analysis of the Ecosystem of Tokens in Ethereum and BNB Smart Chain.* USENIX Security.

Additional citations are inline.

---

*Last updated: [DATE]. Version 0.2.*
