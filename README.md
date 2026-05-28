# Pre-Rug Risk Signals

A reproducible classifier for detecting liquidity-removal rug pulls on Ethereum, using only features observable at the moment a token's first DEX pool is created.

---

## Why this exists

Rug pulls — token launches in which the deployer or a privileged liquidity provider drains the pool and abandons the project, leaving retail buyers holding worthless assets — are among the highest-volume crypto frauds by victim count. Industry reports place annual rug-pull losses in the billions of USD, with the harm distribution skewing heavily toward small, non-accredited buyers.

The detection problem is non-trivial because most of the obvious signals (price collapse, liquidity withdrawal, holder concentration crashes) only appear *after* the rug. Investigations therefore tend to be retrospective. The question this project asks is narrower and, for an investigator's purposes, more useful: at the moment a token first goes live on a DEX, before any retail buying has taken place, what features distinguish the tokens that will rug from those that won't?

The intended use is investigative triage: surfacing tokens for human review with attached reasons, not issuing automated verdicts.

---

## What this does

`rug-detector` is a Python + SQL pipeline that:

1. Ingests on-chain data from Etherscan and The Graph's Uniswap V2/V3 subgraphs.
2. Constructs a feature table for each newly deployed ERC-20 token paired with a Uniswap pool, using only data available at the moment of pool creation (`T₀`).
3. Constructs labels by applying a precise operational definition of "rug pull" (below) to the same historical on-chain data — so positives and negatives are defined by the same evidence stream, and the labeling is auditable.
4. Trains a calibrated classifier on a temporally split historical dataset.
5. Exposes a CLI to score any token address:
   ```bash
   python -m rug_detector score 0xABC... --network ethereum
   ```

The CLI output is a risk score plus a feature-attribution table showing *why* the model flagged the token.

---

## Operational definition

A token is labeled a **rug pull** if all three conditions hold within 30 days of the token's first Uniswap V2 or V3 pool being created:

1. A single transaction signed by an address in the *privileged set* (pool deployer, token deployer, or top-1 LP-token holder) removes liquidity tokens redeemable for ≥80% of the pool's reserve value.
2. The token price drops by ≥90% within the 24 hours following that transaction.
3. No address in the privileged set adds liquidity to the same token in the subsequent 30 days.

This is deliberately narrow. It excludes "slow rugs," "soft rugs," abandoned-development cases, and exit scams that do not involve explicit liquidity removal. The narrower definition produces a cleaner label and a more defensible classifier, at the cost of generalizability. Full rationale in [`reports/methodology.md`](reports/methodology.md), §2.

---

## Example output (schematic)

The numbers below illustrate the output format. They are not from a trained model.

```
$ python -m rug_detector score 0x4f3e... --network ethereum

Token:        ExampleCoin (EXMPL)
Pool:         Uniswap V2, created 2024-03-15 14:22 UTC
Risk score:   0.87  (decile 10 / "high")

Top contributing features:
  +0.31   deployer_prior_rugs > 0
  +0.22   initial_liquidity_quote < 0.5_ETH
  +0.18   lp_holder_concentration_t0 > 0.90
  +0.11   concurrent_token_deployments_24h > 100
  +0.05   pool_creation_hour_utc ∈ {0,1,2,3}
```

---

## Headline findings

Trained and evaluated on a **two-regime sample**: 3 days from April 2023 (post-FTX recovery) and 3 days from June 2024 (mid-cycle memecoin period) — 2,093 tokens total, 570 of which satisfy the operational rug definition for a base rate of **27.2%** (Wilson 95% CI [25.4%, 29.2%]). The percentile-based temporal split places **all of 2023-Q2 in training** and the **last day of 2024-Q2 in test**, making this a cross-regime evaluation.

| Metric                  | Logistic (production) | LightGBM |
|-------------------------|-----------------------|----------|
| AUC-PR                  | **0.406**             | 0.338    |
| AUC-ROC                 | **0.712**             | 0.647    |
| Brier                   | 0.178                 | 0.178    |
| Precision @ top 10      | **0.60**              | 0.40     |
| Precision @ top 50      | **0.52**              | 0.30     |
| Precision @ top 100     | **0.43**              | 0.35     |

**The simpler model wins under regime shift.** An earlier single-regime run on 2024-Q2 alone showed LightGBM dominating logistic (AUC-PR 0.40 vs 0.32). Holding out a test set from a *later* market period than the training data inverts that ranking: the logistic model now beats LightGBM by 0.07 AUC-PR and 8 percentage points of precision-at-100. The plausible reading is that LightGBM's apparent edge came from fitting regime-specific feature interactions that did not transfer. The logistic model's simpler hypothesis class generalizes better.

The logistic precision-at-10 of **0.60** means an investigator reviewing only the 10 tokens most strongly flagged would find 6 actual rugs there — vs ~2.6 by random selection — a **2.3× lift**.

A consistency finding worth noting: the V2 base rate is **29.6% in 2023-Q2 vs 28.7% in 2024-Q2** — essentially identical across a major regime boundary. The operational definition appears to capture something structural about memecoin liquidity rugs rather than a regime-specific artifact.

**Important caveats** (full discussion in [`reports/methodology.md`](reports/methodology.md) §8.2 and §10.2):
- Only two market regimes; generalization beyond the 2023-Q2 → 2024-Q2 boundary is not established
- V3 rugs are systematic false negatives in both windows (V3 LP-NFT modeling gap); the 0% V3 base rate is a detection artifact, not a real-world observation
- 5 of 17 features described in the methodology are unimplemented for data-source reasons
- Subgraph-derived event ordering is approximate within blocks; a known PK-collision pattern produced ~5–10% silent event loss

Raw metrics: [`reports/training_results.json`](reports/training_results.json).

---

## Limitations

This is a research artifact, not a production tool. Five caveats matter most:

1. **Selection bias in positives.** Self-labeled positives are limited to what the operational definition captures. Sophisticated rugs that drained slowly, laundered cleanly, or used novel mechanisms not foreseen by the definition will be missed.
2. **Distribution shift.** Rug-pull techniques evolve. A model trained on 2022–2024 launches may underperform on 2026 launches as deployers adapt to detection patterns — including those described in this very document.
3. **Asymmetric error costs.** A false positive flags a legitimate project as a fraud, with real reputational and possibly legal consequences. The model is explicitly designed as a triage tool: it surfaces tokens for review and provides feature attribution; a human reviewer is required before any action is taken.
4. **Operational-definition conservatism.** Tokens that defraud retail buyers through means other than explicit liquidity removal are labeled as non-rug under this definition. The reported base rate is therefore a lower bound on retail fraud, not an estimate of it.
5. **Network scope.** Ethereum mainnet only. Solana, BSC, Base, and other chains where rug pulls are common are out of scope for this version.

A fuller treatment of threats to validity is in [`reports/methodology.md`](reports/methodology.md), §10.

---

## Reproducing the analysis

```bash
# Clone and install
git clone https://github.com/[user]/rug-pull-detector.git
cd rug-pull-detector
uv sync   # or: pip install -e .

# Configure API keys
cp .env.example .env
# Edit .env to add ETHERSCAN_API_KEY
# (Etherscan free tier is sufficient; The Graph endpoints are public)

# Run the full pipeline
python -m rug_detector pipeline run-all

# Or run individual stages:
python -m rug_detector etl           # Pull raw on-chain data
python -m rug_detector features      # Build feature tables (SQL)
python -m rug_detector label         # Apply operational definition
python -m rug_detector train         # Train and evaluate
python -m rug_detector report        # Generate plots and tables

# Score a single token
python -m rug_detector score <token_address>
```

Raw API responses are cached locally with their fetch timestamp under `data/raw/<source>/<YYYY-MM-DD>/`, so the pipeline is reproducible from the cache without re-hitting APIs. A complete fresh pull of the historical dataset takes ~[N] hours and ~[M] Etherscan API credits.

---

## Repository structure

```
rug-pull-detector/
├── README.md                  ← this file
├── reports/
│   ├── methodology.md         ← full methodology and limitations
│   ├── calibration.png
│   └── feature_importance.png
├── data/
│   ├── raw/                   ← dated API snapshots (with provenance)
│   └── processed/             ← feature tables (parquet)
├── sql/
│   ├── schema.sql
│   ├── 01_pool_events.sql
│   ├── 02_holder_concentration.sql
│   ├── 03_features.sql        ← window-function-heavy feature builds
│   └── 04_labels.sql          ← operational definition → labels
├── src/rug_detector/
│   ├── etl/
│   │   ├── etherscan.py
│   │   └── thegraph.py
│   ├── features.py
│   ├── label.py
│   ├── model.py
│   ├── score.py               ← CLI entry point
│   └── validation.py
├── notebooks/
│   ├── 01_eda.ipynb
│   ├── 02_feature_analysis.ipynb
│   └── 03_model_validation.ipynb
├── tests/
└── pyproject.toml
```

---

## Author

Trevor Rukwava — [trukwava@gsu.edu](mailto:trukwava@gsu.edu)

---

## License

MIT
