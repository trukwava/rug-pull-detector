"""Train + evaluate the rug-pull classifier.

Two models per methodology §6: an L2-regularized logistic regression as
an interpretable baseline, and a LightGBM model as the production model.
Both are calibrated with isotonic regression on a held-out fold.

Temporal split (methodology §7.1):
    train:        T₀ in [2022-07-01, 2024-03-31]
    calibration:  T₀ in [2024-04-01, 2024-05-31]
    test:         T₀ in [2024-06-01, 2025-12-31]
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

try:
    import lightgbm as lgb
except ImportError:  # keep optional so EDA notebooks don't require it
    lgb = None

from .config import get_settings
from .features import BOOL_COLS, CATEGORICAL_COLS, load_features

log = logging.getLogger(__name__)


SPLIT_TRAIN_END = "2024-03-31"
SPLIT_CALIB_END = "2024-05-31"


# Features the model sees. Excludes IDs, timestamps, and the bytecode_hash
# (which feeds the similarity feature computed separately).
FEATURE_COLS = [
    "deployer_wallet_age_days",
    "deployer_prior_token_deployments",
    "deployer_prior_rugs",
    "initial_liquidity_quote",
    "pool_creation_hour_utc",
    "pool_creation_dow",
    "lp_holder_concentration_t0",
    "log_total_supply",
    "top5_holder_concentration",
    "holder_count_t0",
    "share_supply_in_pool",
    "concurrent_token_deployments_24h",
] + BOOL_COLS


@dataclass
class TrainedModel:
    model: object
    scaler: StandardScaler | None  # only for logistic
    feature_cols: list[str]
    kind: str  # "logistic" or "lightgbm"

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        X = X[self.feature_cols].copy()
        if self.scaler is not None:
            X = pd.DataFrame(self.scaler.transform(X), columns=self.feature_cols)
        return self.model.predict_proba(X)[:, 1]


def temporal_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split a feature dataframe by T₀ into train / calibration / test."""
    df = df.copy()
    df["t0"] = pd.to_datetime(df["t0"])
    train = df[df["t0"] <= SPLIT_TRAIN_END]
    calib = df[(df["t0"] > SPLIT_TRAIN_END) & (df["t0"] <= SPLIT_CALIB_END)]
    test  = df[df["t0"] > SPLIT_CALIB_END]
    log.info("Split sizes — train=%d calib=%d test=%d", len(train), len(calib), len(test))
    return train, calib, test


def _prepare_xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    # Drop rows with no label (shouldn't happen if pipeline is consistent, but safe)
    df = df.dropna(subset=["is_rug"])
    X = df[FEATURE_COLS].copy()
    # LightGBM handles NaN; logistic does not. We impute conservatively.
    X = X.fillna(X.median(numeric_only=True))
    y = df["is_rug"].astype(int)
    return X, y


def train_logistic(train_df: pd.DataFrame, calib_df: pd.DataFrame) -> TrainedModel:
    X_train, y_train = _prepare_xy(train_df)
    X_calib, _       = _prepare_xy(calib_df)
    scaler = StandardScaler().fit(pd.concat([X_train, X_calib]))
    Xs = scaler.transform(X_train)
    base = LogisticRegression(
        penalty="l2",
        C=1.0,
        class_weight="balanced",
        max_iter=2000,
        solver="lbfgs",
    )
    # Wrap with isotonic calibration on a held-out calibration set
    model = CalibratedClassifierCV(base, method="isotonic", cv="prefit").fit(
        # CalibratedClassifierCV requires cv="prefit" + a pre-fit base;
        # we have to fit base first.
        scaler.transform(X_train), y_train,
    )
    base.fit(Xs, y_train)
    model = CalibratedClassifierCV(base, method="isotonic", cv="prefit")
    model.fit(scaler.transform(X_calib), _prepare_xy(calib_df)[1])
    return TrainedModel(model=model, scaler=scaler, feature_cols=FEATURE_COLS, kind="logistic")


def train_lightgbm(train_df: pd.DataFrame, calib_df: pd.DataFrame) -> TrainedModel:
    if lgb is None:
        raise ImportError("lightgbm is not installed")
    X_train, y_train = _prepare_xy(train_df)
    X_calib, y_calib = _prepare_xy(calib_df)

    base = lgb.LGBMClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=-1,
        num_leaves=63,
        min_child_samples=20,
        class_weight="balanced",
        random_state=42,
        verbose=-1,
    )
    base.fit(
        X_train, y_train,
        eval_set=[(X_calib, y_calib)],
        callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)],
    )
    model = CalibratedClassifierCV(base, method="isotonic", cv="prefit")
    model.fit(X_calib, y_calib)
    return TrainedModel(model=model, scaler=None, feature_cols=FEATURE_COLS, kind="lightgbm")


def evaluate(model: TrainedModel, test_df: pd.DataFrame, ks: tuple[int, ...] = (10, 50, 100)) -> dict:
    """Compute AUC-PR, AUC-ROC, Brier, calibration deciles, and precision-at-k."""
    X, y = _prepare_xy(test_df)
    p = model.predict_proba(X)

    metrics = {
        "auc_pr":  float(average_precision_score(y, p)),
        "auc_roc": float(roc_auc_score(y, p)),
        "brier":   float(brier_score_loss(y, p)),
        "n_test":  int(len(y)),
        "n_pos":   int(y.sum()),
    }

    # Precision-at-k
    order = np.argsort(-p)
    for k in ks:
        if k <= len(y):
            top_k = y.iloc[order[:k]]
            metrics[f"precision_at_{k}"] = float(top_k.mean())

    # Decile calibration
    df_cal = pd.DataFrame({"y": y.values, "p": p})
    df_cal["decile"] = pd.qcut(df_cal["p"], q=10, labels=False, duplicates="drop")
    calib = df_cal.groupby("decile").agg(
        predicted=("p", "mean"),
        actual=("y", "mean"),
        n=("y", "size"),
    ).reset_index()
    metrics["calibration_by_decile"] = calib.to_dict(orient="records")

    return metrics


def save(model: TrainedModel, path: Path | str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load(path: Path | str) -> TrainedModel:
    with open(path, "rb") as f:
        return pickle.load(f)


def run_training() -> dict:
    """High-level: load features, split, train both, evaluate, persist."""
    settings = get_settings()
    df = load_features(with_labels=True)
    train_df, calib_df, test_df = temporal_split(df)

    log_model = train_logistic(train_df, calib_df)
    lgb_model = train_lightgbm(train_df, calib_df) if lgb is not None else None

    log_metrics = evaluate(log_model, test_df)
    lgb_metrics = evaluate(lgb_model, test_df) if lgb_model else None

    save(log_model, settings.reports_dir / "model_logistic.pkl")
    if lgb_model:
        save(lgb_model, settings.reports_dir / "model_lightgbm.pkl")

    return {"logistic": log_metrics, "lightgbm": lgb_metrics}
