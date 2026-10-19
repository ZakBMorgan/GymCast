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

Backends are chosen explicitly, never substituted: LightGBM by default, or
scikit-learn's HistGradientBoostingRegressor with --backend sklearn (or
GYMCAST_BACKEND=sklearn). If the requested backend cannot load or fit, training
fails loudly and the previously published model stays in place.

Usage (defaults point at the repository's data/ and models/ directories):
    python train.py
    python train.py --input ../data/features.csv --model-out ../models/model.pkl
    python train.py --backend sklearn
"""
import argparse
import hashlib
import os
import pickle
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata

import numpy as np
import pandas as pd

import paths
from fileio import atomic_write_bytes
from features import (
    CATEGORICAL_COLS,
    DEFAULT_HORIZONS,
    FEATURE_COLS,
    TARGET_COL,
    build_supervised_frame,
)

# HistGradientBoosting, not GradientBoosting: lag features are legitimately
# NaN wherever history is short or polling dropped, and the plain
# GradientBoostingRegressor rejects NaN outright.
from sklearn.ensemble import HistGradientBoostingRegressor

try:
    import lightgbm as lgb
    LGB_IMPORT_ERROR = None
except (ImportError, OSError) as exc:
    # OSError too, not just ImportError: on macOS the lightgbm wheel imports
    # fine until it dlopens lib_lightgbm.dylib and finds no libomp, which
    # raises OSError. Catching only ImportError would crash `import train`
    # even for an explicit sklearn run. The error is kept so that requesting
    # LightGBM reports it, instead of silently training with sklearn.
    lgb = None
    LGB_IMPORT_ERROR = exc
HAS_LGB = lgb is not None

BACKENDS = ("lightgbm", "sklearn")

DEFAULT_PARAMS = {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31}

# Below this the boosters fail somewhere in their own internals rather than
# telling you the real problem, which is that there is not enough history yet.
MIN_TRAIN_ROWS = 50


class BackendUnavailable(ValueError):
    """The requested model backend cannot be used on this machine."""


class ModelValidationError(ValueError):
    """A freshly trained model failed the checks required before publishing it."""


def resolve_backend(name: str | None = None) -> str:
    """The backend to fit with: the argument, else GYMCAST_BACKEND, else LightGBM."""
    name = name or os.environ.get("GYMCAST_BACKEND") or "lightgbm"
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; choose one of {', '.join(BACKENDS)}")
    if name == "lightgbm" and lgb is None:
        raise BackendUnavailable(
            f"LightGBM could not be loaded ({LGB_IMPORT_ERROR}). On macOS install the "
            "OpenMP runtime with `brew install libomp`, or train with the alternative "
            "backend explicitly: --backend sklearn (or GYMCAST_BACKEND=sklearn)."
        )
    return name


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


def fit_model(train_df: pd.DataFrame, feature_cols=None, params=None,
              backend: str | None = None) -> dict:
    """Fit on already-split rows and return a self-describing bundle."""
    backend = resolve_backend(backend)
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

    used_lgb = backend == "lightgbm"
    X = design_matrix(train_df, feature_cols, levels, used_lgb)
    y = train_df[TARGET_COL]

    if used_lgb:
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
        "backend": backend,
        "used_lgb": used_lgb,
        "horizons": sorted(train_df["horizon"].unique().tolist()),
        "trained_through": train_df["hour_bucket"].max(),
        "n_train_rows": len(train_df),
        "params": params,
    }


def predict_with(bundle: dict, df: pd.DataFrame) -> np.ndarray:
    X = design_matrix(df, bundle["feature_cols"], bundle["category_levels"], bundle["used_lgb"])
    return np.clip(bundle["model"].predict(X), 0, None)


def save_bundle(bundle: dict, path: str) -> None:
    # Serialize fully before touching the file, so a pickling error leaves the
    # previous model in place for the refresh job.
    atomic_write_bytes(path, pickle.dumps(bundle))


def previous_model_path(path: str) -> str:
    root, ext = os.path.splitext(path)
    return f"{root}.previous{ext}"


def validate_bundle(bundle: dict, restored: dict, frame: pd.DataFrame) -> None:
    """
    Refuse to publish a model that cannot serve forecasts.

    `restored` is the bundle after a pickle round trip - exactly what predict.py
    will load. Both must score the most recent week of training rows with
    finite, identical predictions. This catches broken fits and serialization
    problems; it is not an accuracy check, which needs evaluate.py's folds.
    """
    recent = frame[frame["hour_bucket"] > frame["hour_bucket"].max() - pd.Timedelta(days=7)]
    expected = predict_with(bundle, recent)
    if len(expected) != len(recent) or not np.isfinite(expected).all():
        raise ModelValidationError("the new model produced missing or non-finite predictions")
    if not np.array_equal(predict_with(restored, recent), expected):
        raise ModelValidationError("the saved model does not reproduce the trained model's output")


def publish_bundle(bundle: dict, frame: pd.DataFrame, path: str) -> None:
    """Validate, keep the replaced model as <name>.previous, then swap in the new one."""
    data = pickle.dumps(bundle)
    validate_bundle(bundle, pickle.loads(data), frame)
    if os.path.exists(path):
        with open(path, "rb") as f:
            atomic_write_bytes(previous_model_path(path), f.read())
    atomic_write_bytes(path, data)


def load_bundle(path: str) -> dict:
    with open(path, "rb") as f:
        try:
            bundle = pickle.load(f)
        except (ImportError, OSError) as exc:
            # A LightGBM bundle cannot unpickle where LightGBM cannot load.
            raise ValueError(
                f"{path} needs a library that cannot be loaded here ({exc}). Install it, "
                "or retrain with the alternative backend: train.py --backend sklearn."
            ) from None
    if "category_levels" not in bundle:
        raise ValueError(
            f"{path} was saved by an older train.py that did not pin category levels. "
            "Re-run train.py to regenerate it."
        )
    # Bundles saved before backends were explicit record only the flag.
    bundle.setdefault("backend", "lightgbm" if bundle["used_lgb"] else "sklearn")
    return bundle


def load_supervised(input_path: str, horizons=DEFAULT_HORIZONS) -> pd.DataFrame:
    panel = pd.read_csv(input_path, parse_dates=["hour_bucket"])
    if "count_1hr_ago" in panel.columns:
        raise ValueError(
            f"{input_path} looks like the old target-aligned feature table. "
            "Re-run features.py to rebuild it as an hourly panel."
        )
    return build_supervised_frame(panel, horizons)


def git_revision() -> dict | None:
    """Commit the code was trained from, and whether tracked files differed from it."""
    def git(*args):
        # GIT_OPTIONAL_LOCKS=0: `git status` must not rewrite the index of a deployed checkout.
        return subprocess.run(["git", "-C", str(paths.ROOT), *args], capture_output=True,
                              text=True, check=True, timeout=10,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout.strip()
    try:
        return {"commit": git("rev-parse", "HEAD"),
                "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}
    except (OSError, subprocess.SubprocessError):
        return None  # not a checkout, or git missing; the rest of the record still helps


def training_provenance(input_path: str, frame: pd.DataFrame) -> dict:
    """
    What produced a production bundle: code, library versions and training data.

    A model that starts forecasting badly raises one question first - what
    changed since the last good one? The bundle can answer it only if it
    records this at training time.
    """
    with open(input_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    packages = {}
    for name in ("pandas", "numpy", "scikit-learn", "lightgbm"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": git_revision(),
        "python": platform.python_version(),
        "packages": packages,
        "features_file": os.path.abspath(input_path),
        "features_sha256": digest,
        "locations": sorted(frame["location_name"].unique().tolist()),
        "first_target_hour": str(frame["hour_bucket"].min()),
    }


def train_model(input_path: str, model_out: str, horizons=DEFAULT_HORIZONS,
                backend: str | None = None) -> dict:
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

    bundle = fit_model(labelled, backend=backend)
    bundle["provenance"] = training_provenance(input_path, labelled)
    publish_bundle(bundle, labelled, model_out)

    print(f"Trained on {bundle['n_train_rows']} (origin, horizon) rows "
          f"through {bundle['trained_through']}")
    print(f"Horizons: {min(bundle['horizons'])}-{max(bundle['horizons'])}h  |  "
          f"backend: {bundle['backend']}")
    print(f"Saved model to {model_out}")
    print("Run evaluate.py for walk-forward accuracy against the baselines.")
    return bundle


def kept_model_note(model_out: str) -> str:
    if os.path.exists(model_out):
        return f"The previous model at {model_out} was left unchanged."
    return "No model was written."


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(paths.FEATURES))
    parser.add_argument("--model-out", default=str(paths.MODEL))
    parser.add_argument("--max-horizon", type=int, default=max(DEFAULT_HORIZONS),
                        help="train for forecasts 1..N hours ahead")
    parser.add_argument("--backend", choices=BACKENDS, default=None,
                        help="model backend (default: $GYMCAST_BACKEND, else lightgbm)")
    args = parser.parse_args(argv)

    try:
        train_model(args.input, args.model_out, horizons=range(1, args.max_horizon + 1),
                    backend=args.backend)
    except ValueError as exc:
        # Expected conditions (thin history, unavailable backend, failed validation)
        # get a message instead of a traceback, and a non-zero exit for systemd.
        sys.exit(f"train.py: {exc}\n{kept_model_note(args.model_out)}")
    except Exception:
        # Anything else is a bug or a backend failure: say what was kept, then
        # re-raise so the traceback reaches the journal and the unit is marked failed.
        print(f"train.py: training failed unexpectedly. {kept_model_note(args.model_out)}",
              file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
