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
  +0.31   deployer_wallet_age_days < 1
  +0.22   liquidity_lock_duration_days == 0
  +0.18   top5_holder_concentration > 0.90
  +0.11   contract_has_mintable_owner == True
  +0.05   pool_creation_hour_utc ∈ {0,1,2,3}
```

---

## Headline findings

> *[To be filled in after model run on full dataset. If empty at submission time, this section will be removed rather than shipped with placeholders.]*

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
