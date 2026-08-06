"""
predict.py

Generates near-future occupancy predictions per location and writes them
to a JSON file your website can fetch directly (e.g. served as a static
file, or returned from a small Flask endpoint - see serve.py).

This does NOT need live sensor data to predict ahead: it uses the most
recent known counts (count_1hr_ago, rolling_avg_3hr, etc.) plus the
calendar-based features for each future hour, since those are known in
advance regardless of what happens between now and then.

Usage (run from src/, defaults point at ../models, ../data, ../outputs):
    python predict.py
    python predict.py --model ../models/model.pkl --features ../data/features.csv \
                       --hours-ahead 24 --output ../outputs/predictions.json
"""

import argparse
import json
import pickle
from datetime import timedelta

import numpy as np
import pandas as pd

from features import add_cyclical_time_features, add_academic_calendar


def load_model(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


def latest_known_state(features_df: pd.DataFrame) -> pd.DataFrame:
    """Grab each location's most recent row to seed lag features."""
    idx = features_df.groupby("location_name")["hour_bucket"].idxmax()
    return features_df.loc[idx].set_index("location_name")


def build_future_rows(latest: pd.DataFrame, hours_ahead: int, calendar_path: str | None) -> pd.DataFrame:
    rows = []
    for loc, row in latest.iterrows():
        start = row["hour_bucket"]
        for h in range(1, hours_ahead + 1):
            future_time = start + timedelta(hours=h)
            rows.append({
                "location_name": loc,
                "hour_bucket": future_time,
                # Lag features: for h=1 we know the real latest count;
                # beyond that we fall back to the same rolling average
                # (a simple assumption - refine by chaining predictions if needed).
                "count_1hr_ago": row["count"] if h == 1 else row["rolling_avg_3hr"],
                "count_24hr_ago": row.get("count_24hr_ago", np.nan),
                "count_168hr_ago": row.get("count_168hr_ago", np.nan),
                "rolling_avg_3hr": row["rolling_avg_3hr"],
                "campus_total_count_same_hour": row.get("campus_total_count_same_hour", np.nan),
            })
    future = pd.DataFrame(rows)
    future = add_cyclical_time_features(future)
    future = add_academic_calendar(future, calendar_path)
    return future


def predict(model_bundle, future_df: pd.DataFrame) -> pd.DataFrame:
    model = model_bundle["model"]
    feature_cols = model_bundle["feature_cols"]

    for col in ["semester_phase", "location_name"]:
        if col in future_df.columns:
            future_df[col] = future_df[col].astype("category")

    X = future_df[feature_cols]
    if not model_bundle["used_lgb"]:
        X = pd.get_dummies(X, columns=[c for c in ["semester_phase", "location_name"] if c in feature_cols])

    future_df["predicted_count"] = model.predict(X)
    future_df["predicted_count"] = future_df["predicted_count"].clip(lower=0)
    return future_df


def to_website_json(future_df: pd.DataFrame, capacity_lookup: dict) -> dict:
    output = {}
    for loc, group in future_df.groupby("location_name"):
        capacity = capacity_lookup.get(loc)
        entries = []
        for _, row in group.sort_values("hour_bucket").iterrows():
            pct = (row["predicted_count"] / capacity * 100) if capacity else None
            entries.append({
                "time": row["hour_bucket"].isoformat(),
                "predicted_count": round(float(row["predicted_count"]), 1),
                "predicted_percent": round(pct, 1) if pct is not None else None,
            })
        output[loc] = entries
    return output


def main(model_path: str, features_path: str, hours_ahead: int, output_path: str, calendar_path: str | None):
    model_bundle = load_model(model_path)
    features_df = pd.read_csv(features_path, parse_dates=["hour_bucket"])

    latest = latest_known_state(features_df)
    capacity_lookup = latest["capacity"].to_dict()

    future = build_future_rows(latest, hours_ahead, calendar_path)
    future = predict(model_bundle, future)

    result = to_website_json(future, capacity_lookup)
    with open(output_path, "w") as f:
        json.dump({"generated_at": pd.Timestamp.now().isoformat(), "locations": result}, f, indent=2)

    print(f"Wrote predictions for {len(capacity_lookup)} locations to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="../models/model.pkl")
    parser.add_argument("--features", default="../data/features.csv")
    parser.add_argument("--hours-ahead", type=int, default=24)
    parser.add_argument("--output", default="../outputs/predictions.json")
    parser.add_argument("--calendar", default="../data/academic_calendar.csv")
    args = parser.parse_args()

    main(args.model, args.features, args.hours_ahead, args.output, args.calendar)
