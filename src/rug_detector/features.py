"""Build the feature table by running 03_features.sql. Provides a
pandas-DataFrame view of the features for downstream model code.
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import get_settings
from .db import connect, run_sql_file

log = logging.getLogger(__name__)


# Categorical columns that need encoding before modelling.
CATEGORICAL_COLS = ["quote_token", "version"]

# Bool columns that pandas reads as object; cast to int for model code.
BOOL_COLS = [
    "contract_verified",
    "contract_has_mintable_owner",
    "contract_has_pausable",
    "contract_has_blacklist",
    "contract_owner_renounced",
    "contract_proxy_pattern",
]


def build_features() -> int:
    """Run 03_features.sql. Assumes labels already built (deployer_prior_rugs uses it)."""
    settings = get_settings()
    run_sql_file(settings.sql_dir / "03_features.sql")
    with connect(read_only=True) as db:
        n = db.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    log.info("Built features for %d tokens", n)
    return n


def load_features(with_labels: bool = True) -> pd.DataFrame:
    """Read the features table into pandas, optionally joining labels."""
    sql = """
        SELECT f.*, l.is_rug, l.removal_time
        FROM features f
        LEFT JOIN labels l USING (token_address)
    """ if with_labels else "SELECT * FROM features"
    with connect(read_only=True) as db:
        df = db.execute(sql).df()
    # Cast bool-ish columns to int (LightGBM handles NaNs natively)
    for c in BOOL_COLS:
        if c in df.columns:
            df[c] = df[c].astype("boolean").astype("Int8")
    return df
