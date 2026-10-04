"""
train.py

Fits the occupancy model and saves it, plus the model-side primitives
(categorical encoding, design matrix, predict) that evaluate.py and predict.py
reuse so training and inference cannot drift apart.

The model is *direct multi-horizon*: one regressor over (origin, horizon) rows,
with the horizon itself as a feature. That way a 24-hours-ahead forecast is
trained on 24-hours-ahead examples rather than on next-hour examples that
happen to get reused at serving time.

Training here fits on all available history and reports nothing about accuracy
on purpose - run evaluate.py for that, since an honest number needs
walk-forward folds rather than a single lucky holdout.

Usage (run from src/, defaults point at ../data and ../models):
    python train.py
    python train.py --input ../data/features.csv --model-out ../models/model.pkl
"""

import argparse
import pickle
import sys

import numpy as np
import pandas as pd

from features import (
    CATEGORICAL_COLS,
    DEFAULT_HORIZONS,
    FEATURE_COLS,
    TARGET_COL,
    build_supervised_frame,
)

try:
    import lightgbm as lgb
    HAS_LGB = True
except (ImportError, OSError):
    # OSError too, not just ImportError: on macOS the lightgbm wheel imports
    # fine until it dlopens lib_lightgbm.dylib and finds no libomp, which
    # raises OSError. Catching only ImportError turns a missing OpenMP runtime
    # into a hard crash on `import train` instead of a fallback.
    HAS_LGB = False
    # HistGradientBoosting, not GradientBoosting: lag features are legitimately
    # NaN wherever history is short or polling dropped, and the plain
    # GradientBoostingRegressor rejects NaN outright.
    from sklearn.ensemble import HistGradientBoostingRegressor

DEFAULT_PARAMS = {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31}

# Below this the boosters fail somewhere in their own internals rather than
# telling you the real problem, which is that there is not enough history yet.
MIN_TRAIN_ROWS = 50


def model_columns(df: pd.DataFrame) -> list[str]:
    """Feature columns present in `df`, numeric first then categoricals."""
    return (
        [c for c in FEATURE_COLS if c in df.columns]
        + [c for c in CATEGORICAL_COLS if c in df.columns]
    )


def fit_category_levels(df: pd.DataFrame, cols=CATEGORICAL_COLS) -> dict[str, list[str]]:
    """
    Freeze the category vocabulary from the training rows only.

    pandas assigns category codes by whatever levels happen to be present, and
    the boosters consume those codes as integers. Deriving them separately at
    fit time and at predict time silently remaps categories - a frame holding
    only "unknown" would encode it as 0 when training had it at 2. Pinning the
    levels in the bundle keeps the encoding stable, and fitting on train rows
    only keeps test-set vocabulary out of the model.
    """
    return {
        c: sorted(df[c].dropna().astype(str).unique())
        for c in cols
        if c in df.columns
    }


def apply_category_levels(df: pd.DataFrame, levels: dict[str, list[str]]) -> pd.DataFrame:
    """Re-encode with the frozen vocabulary. Unseen levels become NaN (missing)."""
    df = df.copy()
    for col, cats in levels.items():
        if col in df.columns:
            df[col] = pd.Categorical(df[col].astype(str), categories=cats)
    return df


def design_matrix(df: pd.DataFrame, feature_cols, levels, used_lgb: bool) -> pd.DataFrame:
    X = apply_category_levels(df, levels)[feature_cols]
    if not used_lgb:
        cat_cols = [c for c in levels if c in feature_cols]
        # Fixed Categorical levels make get_dummies emit the same columns in the
        # same order for train and predict, so no reindex patch-up is needed.
        X = pd.get_dummies(X, columns=cat_cols)
    return X


def fit_model(train_df: pd.DataFrame, feature_cols=None, params=None) -> dict:
    """Fit on already-split rows and return a self-describing bundle."""
    train_df = train_df.dropna(subset=[TARGET_COL])
    if train_df.empty:
        raise ValueError("no training rows with an observed count")

    # A row whose origin was never observed carries no lag signal at all. With
    # only a few hours of history that is every row, and the boosters then fail
    # on an empty binning array instead of on the actual problem.
    usable = int(train_df["count_at_origin"].notna().sum())
    if usable < MIN_TRAIN_ROWS:
        span = train_df["hour_bucket"].max() - train_df["hour_bucket"].min()
        raise ValueError(
            f"only {usable} rows have an observed forecast origin (need {MIN_TRAIN_ROWS}); "
            f"the panel spans {span}. Let collector.py run longer - the 168h lag "
            "alone needs a full week before it produces anything."
        )

    feature_cols = feature_cols or model_columns(train_df)
    params = {**DEFAULT_PARAMS, **(params or {})}
    levels = fit_category_levels(train_df)

    X = design_matrix(train_df, feature_cols, levels, HAS_LGB)
    y = train_df[TARGET_COL]

    if HAS_LGB:
        model = lgb.LGBMRegressor(verbose=-1, **params)
        model.fit(X, y, categorical_feature=[c for c in levels if c in feature_cols])
    else:
        model = HistGradientBoostingRegressor(
            max_iter=params["n_estimators"],
            learning_rate=params["learning_rate"],
            max_leaf_nodes=params["num_leaves"],
        )
        model.fit(X, y)

    return {
        "model": model,
        "feature_cols": feature_cols,
        "category_levels": levels,
        "used_lgb": HAS_LGB,
        "horizons": sorted(train_df["horizon"].unique().tolist()),
        "trained_through": train_df["hour_bucket"].max(),
        "n_train_rows": len(train_df),
        "params": params,
    }


def predict_with(bundle: dict, df: pd.DataFrame) -> np.ndarray:
    X = design_matrix(df, bundle["feature_cols"], bundle["category_levels"], bundle["used_lgb"])
    return np.clip(bundle["model"].predict(X), 0, None)


def save_bundle(bundle: dict, path: str) -> None:
    with open(path, "wb") as f:
        pickle.dump(bundle, f)


def load_bundle(path: str) -> dict:
    with open(path, "rb") as f:
        bundle = pickle.load(f)
    if "category_levels" not in bundle:
        raise ValueError(
            f"{path} was saved by an older train.py that did not pin category levels. "
            "Re-run train.py to regenerate it."
        )
    return bundle


def load_supervised(input_path: str, horizons=DEFAULT_HORIZONS) -> pd.DataFrame:
    panel = pd.read_csv(input_path, parse_dates=["hour_bucket"])
    if "count_1hr_ago" in panel.columns:
        raise ValueError(
            f"{input_path} looks like the old target-aligned feature table. "
            "Re-run features.py to rebuild it as an hourly panel."
        )
    return build_supervised_frame(panel, horizons)


def train_model(input_path: str, model_out: str, horizons=DEFAULT_HORIZONS) -> dict:
    frame = load_supervised(input_path, horizons)
    labelled = frame.dropna(subset=[TARGET_COL])

    if labelled.empty:
        raise ValueError(
            "No rows have an observed count. Has collector.py been running?"
        )

    span = labelled["hour_bucket"].max() - labelled["hour_bucket"].min()
    if span < pd.Timedelta(days=14):
        print(
            f"WARNING: only {span} of history. The 168h lag needs a full week "
            "before it produces anything, and evaluate.py needs several weeks "
            "to build folds. Treat any model from this as a smoke test."
        )

    bundle = fit_model(labelled)
    save_bundle(bundle, model_out)

    print(f"Trained on {bundle['n_train_rows']} (origin, horizon) rows "
          f"through {bundle['trained_through']}")
    print(f"Horizons: {min(bundle['horizons'])}-{max(bundle['horizons'])}h  |  "
          f"backend: {'lightgbm' if bundle['used_lgb'] else 'sklearn'}")
    print(f"Saved model to {model_out}")
    print("Run evaluate.py for walk-forward accuracy against the baselines.")
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="../data/features.csv")
    parser.add_argument("--model-out", default="../models/model.pkl")
    parser.add_argument("--max-horizon", type=int, default=max(DEFAULT_HORIZONS),
                        help="train for forecasts 1..N hours ahead")
    args = parser.parse_args()

    try:
        train_model(args.input, args.model_out, horizons=range(1, args.max_horizon + 1))
    except ValueError as exc:
        # Not enough history is the expected state early on, not a crash.
        sys.exit(f"train.py: {exc}")
