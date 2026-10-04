"""
evaluate.py

Walk-forward (rolling-origin) evaluation of the occupancy model against a
suite of baselines.

The protocol, per fold:

    |<---------- train: targets <= cutoff ---------->|<-- test: cutoff+1 .. cutoff+H -->|
                                                  cutoff
                                                (refit here)

Folds advance the cutoff forward and refit from scratch each time, which is
what "walk-forward" buys you over one holdout: an accuracy number averaged over
several distinct weeks rather than over whichever week happened to land last.
A single holdout on a few months of gym data is mostly measuring that one
week's weather and exam schedule.

Two rules keep the folds honest:

  - Training targets end at the cutoff. Test targets fall after the cutoff
    and through test_end, with origins at or after the cutoff. Earlier origins
    would use a model fitted on observations unavailable when the forecast
    was issued, even though the row's features are origin-safe.
  - Anything fitted from data - the category vocabulary, the hour-of-week
    profile baseline - is fitted inside the fold, on training rows only. A
    profile baseline fitted once over the whole dataset is the classic way to
    hand a baseline a peek at the test period and then conclude the model beat
    it fairly.

Usage (run from src/, defaults point at ../data):
    python evaluate.py
    python evaluate.py --folds 6 --test-days 7 --report-out ../outputs/eval.json
"""

import argparse
import json

import numpy as np
import pandas as pd

from features import DEFAULT_HORIZONS, TARGET_COL
from train import fit_model, load_supervised, predict_with


# ---------------------------------------------------------------- baselines
# Each takes (train_df, test_df) and returns predictions positionally aligned
# to test_df. They take train_df so anything fitted is fitted in-fold.

def baseline_persistence(train_df, test_df):
    """Whatever the count was at the forecast origin, assume it holds."""
    return test_df["count_at_origin"].to_numpy(dtype=float)


def baseline_seasonal_24h(train_df, test_df):
    """Same hour yesterday."""
    return test_df["count_24h_before_target"].to_numpy(dtype=float)


def baseline_seasonal_168h(train_df, test_df):
    """Same hour, same weekday, last week - the original README baseline."""
    return test_df["count_168h_before_target"].to_numpy(dtype=float)


def baseline_hour_weekday_profile(train_df, test_df):
    """
    Mean count for this (location, weekday, hour), learned from the training
    fold only.

    This is the baseline worth beating. Gym traffic is overwhelmingly a
    function of hour-of-week, so a lookup table captures most of the signal and
    is immune to the noise that trips up last-week's-value baselines.
    """
    key = ["location_name", "weekday", "hour"]
    profile = train_df.groupby(key, observed=True)[TARGET_COL].mean().rename("_pred").reset_index()

    merged = test_df[key].merge(profile, on=key, how="left")
    preds = merged["_pred"].to_numpy(dtype=float)

    # Back off through progressively coarser means for hour-of-week cells the
    # training fold never saw (a new location, a 4am hour nobody polled).
    loc_mean = train_df.groupby("location_name", observed=True)[TARGET_COL].mean()
    fallback = test_df["location_name"].map(loc_mean).to_numpy(dtype=float)
    preds = np.where(np.isfinite(preds), preds, fallback)
    return np.where(np.isfinite(preds), preds, train_df[TARGET_COL].mean())


BASELINES = {
    "persistence": baseline_persistence,
    "seasonal_naive_24h": baseline_seasonal_24h,
    "seasonal_naive_168h": baseline_seasonal_168h,
    "hour_weekday_profile": baseline_hour_weekday_profile,
}

# Column headers for the per-horizon table. Plain truncation collapsed
# seasonal_naive_24h and seasonal_naive_168h onto the same label.
SHORT_NAMES = {
    "model": "model",
    "persistence": "persist",
    "seasonal_naive_24h": "naive24h",
    "seasonal_naive_168h": "naive168h",
    "hour_weekday_profile": "profile",
}


# ------------------------------------------------------------------- folds

def make_folds(frame: pd.DataFrame, n_folds: int, test_days: int, min_train_days: int):
    """
    Expanding-window folds ending at the most recent data.

    Expanding rather than sliding: with a dataset this small, throwing away
    early history to hold the window fixed costs more than the staleness it
    avoids. Returns [(cutoff, test_end), ...] oldest first.
    """
    t_min = frame["hour_bucket"].min()
    t_max = frame["hour_bucket"].max()
    width = pd.Timedelta(days=test_days)

    folds = []
    for k in range(n_folds - 1, -1, -1):
        test_end = t_max - width * k
        cutoff = test_end - width
        if cutoff - t_min < pd.Timedelta(days=min_train_days):
            continue  # not enough history behind this cutoff to fit on
        folds.append((cutoff, test_end))
    return folds


def split_fold(
    frame: pd.DataFrame, cutoff: pd.Timestamp, test_end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep known training labels and forecasts issued after the model is available."""
    train_df = frame[frame["hour_bucket"] <= cutoff]
    # A target-only split lets pre-cutoff origins use a model trained in their future.
    test_df = frame[
        (frame["hour_bucket"] > cutoff)
        & (frame["hour_bucket"] <= test_end)
        & (frame["origin_bucket"] >= cutoff)
    ].copy()
    test_df.attrs["cutoff"] = cutoff
    test_df.attrs["test_end"] = test_end
    return train_df, test_df


# ----------------------------------------------------------------- metrics

def score(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    err = y_pred - y_true
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "bias": float(np.mean(err)),
    }


def evaluate_fold(train_df: pd.DataFrame, test_df: pd.DataFrame) -> dict | None:
    """Fit on the fold's training rows, score model and baselines on its test rows."""
    if train_df.empty or test_df.empty:
        return None

    bundle = fit_model(train_df)
    preds = {"model": predict_with(bundle, test_df)}
    for name, fn in BASELINES.items():
        preds[name] = np.asarray(fn(train_df, test_df), dtype=float)

    y = test_df[TARGET_COL].to_numpy(dtype=float)

    # Score everyone on the same rows. Baselines have different coverage -
    # seasonal_naive_168h is undefined for the first week of any location - and
    # comparing MAEs computed over different row sets is meaningless, since the
    # dropped rows are systematically the hard early ones.
    common = np.isfinite(y)
    for p in preds.values():
        common &= np.isfinite(p)
    if not common.any():
        return None

    return {
        "cutoff": test_df.attrs.get("cutoff"),
        "test_end": test_df.attrs.get("test_end"),
        "n_train": int(len(train_df)),
        "n_test_scored": int(common.sum()),
        "n_test_dropped": int(len(test_df) - common.sum()),
        "scores": {name: score(y[common], p[common]) for name, p in preds.items()},
        "per_horizon": per_horizon_mae(
            test_df[common], y[common], {k: v[common] for k, v in preds.items()}
        ),
    }


def per_horizon_mae(test_df: pd.DataFrame, y: np.ndarray, preds: dict) -> dict:
    """MAE bucketed by forecast horizon - where model-vs-baseline usually diverges."""
    out = {}
    horizons = test_df["horizon"].to_numpy()
    for h in sorted(np.unique(horizons)):
        m = horizons == h
        out[int(h)] = {name: float(np.mean(np.abs(p[m] - y[m]))) for name, p in preds.items()}
    return out


def aggregate(fold_results: list[dict]) -> dict:
    """Row-weighted mean across folds, so a fold with more scored rows counts more."""
    names = list(fold_results[0]["scores"])
    weights = np.array([f["n_test_scored"] for f in fold_results], dtype=float)
    agg = {}
    for name in names:
        agg[name] = {
            metric: float(np.average([f["scores"][name][metric] for f in fold_results], weights=weights))
            for metric in ("mae", "rmse", "bias")
        }
    return agg


# ------------------------------------------------------------------ report

def print_report(agg: dict, fold_results: list[dict], open_only: bool) -> None:
    scope = "open hours only" if open_only else "all hours"
    total = sum(f["n_test_scored"] for f in fold_results)
    dropped = sum(f["n_test_dropped"] for f in fold_results)
    print(f"\nWalk-forward: {len(fold_results)} folds, {total} scored predictions ({scope})")
    if dropped:
        # Usually the first week of a location, where the 168h baseline has no
        # value yet. Worth stating rather than silently narrowing the sample.
        print(f"{dropped} test rows excluded: not every baseline was defined there.")
    print()

    ranked = sorted(agg.items(), key=lambda kv: kv[1]["mae"])
    best_baseline = min(
        (n for n in agg if n != "model"), key=lambda n: agg[n]["mae"], default=None
    )

    print(f"{'':<24}{'MAE':>9}{'RMSE':>9}{'bias':>9}")
    for name, m in ranked:
        tag = "  <- model" if name == "model" else ""
        print(f"{name:<24}{m['mae']:>9.2f}{m['rmse']:>9.2f}{m['bias']:>+9.2f}{tag}")

    if best_baseline:
        skill = 1 - agg["model"]["mae"] / agg[best_baseline]["mae"]
        print(f"\nSkill vs best baseline ({best_baseline}): {skill:+.1%}")
        if skill <= 0:
            print("The model is not earning its keep - the baseline is cheaper and better.")
        elif skill < 0.05:
            print("Marginal. Weigh the gain against the retraining pipeline.")

    print("\nMAE by horizon:")
    horizons = sorted({h for f in fold_results for h in f["per_horizon"]})
    names = [n for n, _ in ranked]
    print(f"{'h':>4}" + "".join(f"{SHORT_NAMES.get(n, n):>12}" for n in names))
    for h in horizons:
        cells = []
        for name in names:
            vals = [f["per_horizon"][h][name] for f in fold_results if h in f["per_horizon"]]
            cells.append(f"{np.mean(vals):>12.2f}" if vals else f"{'-':>12}")
        print(f"{h:>4}" + "".join(cells))

    print("\nPer fold (model MAE vs best baseline that fold):")
    for f in fold_results:
        scores = f["scores"]
        best = min((n for n in scores if n != "model"), key=lambda n: scores[n]["mae"])
        print(f"  cutoff {f['cutoff']:%Y-%m-%d %H:%M}  n={f['n_test_scored']:>5}  "
              f"model {scores['model']['mae']:.2f}  |  {best} {scores[best]['mae']:.2f}")


def run(input_path: str, n_folds: int, test_days: int, min_train_days: int,
        horizons, open_only: bool, report_out: str | None) -> dict | None:
    frame = load_supervised(input_path, horizons)
    frame = frame.dropna(subset=[TARGET_COL])

    if open_only and "is_open" in frame.columns:
        # Closed hours are trivially zero and make up a big share of the panel.
        # Leaving them in flatters every model equally and hides the differences
        # during the hours anyone actually cares about.
        frame = frame[frame["is_open"].fillna(True).astype(bool)]

    if frame.empty:
        print("No labelled rows to evaluate. Has collector.py been running?")
        return None

    span = frame["hour_bucket"].max() - frame["hour_bucket"].min()
    folds = make_folds(frame, n_folds, test_days, min_train_days)
    if not folds:
        needed = min_train_days + test_days
        print(f"Not enough history for a single fold: have {span}, need at least "
              f"~{needed} days ({min_train_days}d train + {test_days}d test).")
        print("Keep collector.py running, or lower --min-train-days / --test-days.")
        return None

    results = []
    for cutoff, test_end in folds:
        train_df, test_df = split_fold(frame, cutoff, test_end)

        fold = evaluate_fold(train_df, test_df)
        if fold is None:
            print(f"  skipped fold at {cutoff:%Y-%m-%d} (no scorable rows)")
            continue
        results.append(fold)

    if not results:
        print("Every fold was empty after alignment. Need more contiguous history.")
        return None

    agg = aggregate(results)
    print_report(agg, results, open_only)

    report = {
        "config": {
            "folds": len(results), "test_days": test_days,
            "min_train_days": min_train_days, "open_only": open_only,
            "horizons": [int(h) for h in horizons],
        },
        "aggregate": agg,
        "folds": [
            {**f, "cutoff": str(f["cutoff"]), "test_end": str(f["test_end"])}
            for f in results
        ],
    }
    if report_out:
        with open(report_out, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nWrote report to {report_out}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="../data/features.csv")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--test-days", type=int, default=7,
                        help="length of each test window; also how stale the model "
                             "gets before the next refit")
    parser.add_argument("--min-train-days", type=int, default=21,
                        help="skip folds with less history than this behind the cutoff")
    parser.add_argument("--max-horizon", type=int, default=max(DEFAULT_HORIZONS))
    parser.add_argument("--all-hours", action="store_true",
                        help="include hours the gym was closed (default: open hours only)")
    parser.add_argument("--report-out", default=None)
    args = parser.parse_args()

    run(
        args.input, args.folds, args.test_days, args.min_train_days,
        range(1, args.max_horizon + 1), not args.all_hours, args.report_out,
    )
