"""Score a single token address by running the on-the-fly ETL + feature
extraction + model inference.

This is the user-facing entry point: `python -m rug_detector score 0xABC...`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import get_settings
from .features import load_features
from .model import FEATURE_COLS, TrainedModel, load

log = logging.getLogger(__name__)


@dataclass
class Score:
    token_address: str
    risk_score: float
    decile: int
    top_features: list[tuple[str, float]]


def score_token(address: str, model_path: Path | None = None) -> Score:
    """Score a single token address. Currently assumes the feature row
    already exists in the local DB (i.e. the address is in the training
    universe). A future version should run on-the-fly ETL for arbitrary
    addresses; for now, raise a clear error if the token isn't known."""
    settings = get_settings()
    address = address.lower()

    df = load_features(with_labels=False)
    row = df[df["token_address"] == address]
    if row.empty:
        raise ValueError(
            f"Token {address} not present in local feature table. "
            "Run `python -m rug_detector etl --token {address}` first, "
            "or add on-the-fly ETL in score.score_token()."
        )

    model_path = model_path or (settings.reports_dir / "model_lightgbm.pkl")
    if not model_path.exists():
        # Fall back to the logistic model if LightGBM isn't trained
        model_path = settings.reports_dir / "model_logistic.pkl"
    model: TrainedModel = load(model_path)

    p = float(model.predict_proba(row)[0])
    decile = int(np.clip(int(p * 10) + 1, 1, 10))
    top = _explain(model, row)

    return Score(
        token_address=address,
        risk_score=p,
        decile=decile,
        top_features=top,
    )


def _explain(model: TrainedModel, row: pd.DataFrame) -> list[tuple[str, float]]:
    """Return the top-5 feature contributions to the score.

    For logistic: standardized coefficient × standardized feature value.
    For LightGBM: SHAP values for the single prediction.
    """
    if model.kind == "logistic":
        base = model.model.estimator if hasattr(model.model, "estimator") else model.model.base_estimator
        coefs = base.coef_[0]
        Xs = model.scaler.transform(row[FEATURE_COLS])[0]
        contribs = list(zip(FEATURE_COLS, coefs * Xs))
    else:
        try:
            import shap  # noqa: F401
        except ImportError:
            log.warning("shap not installed; falling back to feature_importances_")
            base = model.model.estimator if hasattr(model.model, "estimator") else model.model.base_estimator
            imps = base.feature_importances_
            contribs = list(zip(FEATURE_COLS, imps * row[FEATURE_COLS].values[0]))
        else:
            import shap
            base = model.model.estimator if hasattr(model.model, "estimator") else model.model.base_estimator
            explainer = shap.TreeExplainer(base)
            shap_values = explainer.shap_values(row[FEATURE_COLS])
            if isinstance(shap_values, list):
                shap_values = shap_values[1]  # positive class
            contribs = list(zip(FEATURE_COLS, shap_values[0]))

    contribs.sort(key=lambda x: -abs(x[1]))
    return contribs[:5]
