"""
train.py

Trains a gradient-boosted model (LightGBM) to predict `count` at a given
location/hour, using the feature table produced by features.py.

Compares against a naive baseline (last week's same hour) so you can tell
if the model is actually earning its keep.

Usage (run from src/, defaults point at ../data and ../models):
    python train.py
    python train.py --input ../data/features.csv --model-out ../models/model.pkl
"""

import argparse
import pickle

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False
    from sklearn.ensemble import GradientBoostingRegressor


FEATURE_COLS = [
    "hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "is_weekend",
    "count_1hr_ago", "count_24hr_ago", "count_168hr_ago", "rolling_avg_3hr",
    "campus_total_count_same_hour",
]
CATEGORICAL_COLS = ["semester_phase", "location_name"]
TARGET_COL = "count"


def prepare_data(df: pd.DataFrame):
    df = df.dropna(subset=["count_168hr_ago"]).copy()  # need at least a week of history

    for col in CATEGORICAL_COLS:
        if col in df.columns:
            df[col] = df[col].astype("category")

    feature_cols = FEATURE_COLS + [c for c in CATEGORICAL_COLS if c in df.columns]
    df = df.dropna(subset=[TARGET_COL])
    return df, feature_cols


def chronological_split(df: pd.DataFrame, test_weeks: int = 3):
    cutoff = df["hour_bucket"].max() - pd.Timedelta(weeks=test_weeks)
    train = df[df["hour_bucket"] <= cutoff]
    test = df[df["hour_bucket"] > cutoff]
    return train, test


def naive_baseline_mae(test: pd.DataFrame) -> float:
    valid = test.dropna(subset=["count_168hr_ago", TARGET_COL])
    return mean_absolute_error(valid[TARGET_COL], valid["count_168hr_ago"])


def train_model(input_path: str, model_out: str):
    df = pd.read_csv(input_path, parse_dates=["hour_bucket"])
    df, feature_cols = prepare_data(df)
    train, test = chronological_split(df)

    X_train, y_train = train[feature_cols], train[TARGET_COL]
    X_test, y_test = test[feature_cols], test[TARGET_COL]

    if HAS_LGB:
        cat_features = [c for c in CATEGORICAL_COLS if c in feature_cols]
        model = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31)
        model.fit(X_train, y_train, categorical_feature=cat_features)
    else:
        # Fallback: one-hot encode categoricals for sklearn
        X_train = pd.get_dummies(X_train, columns=[c for c in CATEGORICAL_COLS if c in feature_cols])
        X_test = pd.get_dummies(X_test, columns=[c for c in CATEGORICAL_COLS if c in feature_cols])
        X_test = X_test.reindex(columns=X_train.columns, fill_value=0)
        model = GradientBoostingRegressor(n_estimators=300, learning_rate=0.05)
        model.fit(X_train, y_train)

    preds = model.predict(X_test)
    model_mae = mean_absolute_error(y_test, preds)
    baseline_mae = naive_baseline_mae(test)

    print(f"Model MAE:    {model_mae:.2f} people")
    print(f"Baseline MAE: {baseline_mae:.2f} people (naive: same hour last week)")
    if model_mae < baseline_mae:
        print("-> Model beats the naive baseline.")
    else:
        print("-> Model is NOT beating the naive baseline yet. Needs more data/features.")

    with open(model_out, "wb") as f:
        pickle.dump({"model": model, "feature_cols": feature_cols, "used_lgb": HAS_LGB}, f)
    print(f"Saved model to {model_out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="../data/features.csv")
    parser.add_argument("--model-out", default="../models/model.pkl")
    args = parser.parse_args()

    train_model(args.input, args.model_out)
