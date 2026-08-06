"""
features.py

Turns the raw collector output (marino_counts.csv) into a model-ready
feature table. One row per (location, timestamp-bucketed-to-hour) with:
  - cyclical time encodings
  - lag features (same hour yesterday / last week, rolling averages)
  - academic calendar context (optional, via a manual csv you maintain)

Usage (run from src/, defaults point at ../data):
    python features.py
    python features.py --input ../data/marino_counts.csv --output ../data/features.csv
"""

import argparse
import os

import numpy as np
import pandas as pd


ACADEMIC_CALENDAR_COLUMNS = ["date", "semester_phase"]
# semester_phase examples: "regular", "dead_week", "finals", "break", "summer_session"


def load_raw(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values(["location_name", "timestamp"]).reset_index(drop=True)
    return df


def bucket_to_hour(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse 5-minute polls into one row per location per hour.
    We take the mean count/percent within the hour, and the max capacity
    (capacity shouldn't change, but max is a safe aggregator).
    Also carries forward an is_open flag if present.
    """
    df["hour_bucket"] = df["timestamp"].dt.floor("h")

    agg = {
        "count": "mean",
        "capacity": "max",
        "percent": "mean",
    }
    if "is_open" in df.columns:
        agg["is_open"] = "max"  # if open at all during the hour, treat as open

    grouped = (
        df.groupby(["location_name", "hour_bucket"])
        .agg(agg)
        .reset_index()
    )
    return grouped


def add_cyclical_time_features(df: pd.DataFrame, time_col: str = "hour_bucket") -> pd.DataFrame:
    dt = df[time_col].dt
    df["hour"] = dt.hour
    df["weekday"] = dt.weekday  # 0=Monday
    df["is_weekend"] = df["weekday"].isin([5, 6]).astype(int)

    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["weekday_sin"] = np.sin(2 * np.pi * df["weekday"] / 7)
    df["weekday_cos"] = np.cos(2 * np.pi * df["weekday"] / 7)
    return df


def add_academic_calendar(df: pd.DataFrame, calendar_path: str | None) -> pd.DataFrame:
    if not calendar_path or not os.path.exists(calendar_path):
        df["semester_phase"] = "unknown"
        return df

    cal = pd.read_csv(calendar_path, parse_dates=["date"])
    if cal.empty:
        # Committed template with only a header row — nothing to join on.
        df["semester_phase"] = "unknown"
        return df

    df["date"] = df["hour_bucket"].dt.floor("D")
    df = df.merge(cal, on="date", how="left")
    df["semester_phase"] = df["semester_phase"].fillna("unknown")
    df = df.drop(columns=["date"])
    return df


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds, per location:
      - count_1hr_ago
      - count_24hr_ago   (same hour yesterday)
      - count_168hr_ago  (same hour, same weekday, last week)
      - rolling_avg_3hr  (trailing 3-hour smoothed average, excluding current row)
    All computed per-location on an hourly-regularized index so gaps
    in polling don't silently shift the lag alignment.
    """
    out_frames = []
    for loc, g in df.groupby("location_name"):
        g = g.set_index("hour_bucket").sort_index()

        # Reindex to a full hourly range so lag-by-N-hours is always correct,
        # even if polling had gaps. Missing counts become NaN (handled downstream).
        full_range = pd.date_range(g.index.min(), g.index.max(), freq="h")
        g = g.reindex(full_range)
        g["location_name"] = loc

        g["count_1hr_ago"] = g["count"].shift(1)
        g["count_24hr_ago"] = g["count"].shift(24)
        g["count_168hr_ago"] = g["count"].shift(168)
        g["rolling_avg_3hr"] = g["count"].shift(1).rolling(window=3, min_periods=1).mean()

        g = g.rename_axis("hour_bucket").reset_index()
        out_frames.append(g)

    result = pd.concat(out_frames, ignore_index=True)
    return result


def add_cross_location_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds campus_total_count_same_hour: sum of all locations' counts at that
    hour_bucket, useful as an "is today unusually busy overall" signal.
    """
    totals = (
        df.groupby("hour_bucket")["count"]
        .sum()
        .rename("campus_total_count_same_hour")
        .reset_index()
    )
    df = df.merge(totals, on="hour_bucket", how="left")
    return df


def build_features(input_path: str, output_path: str, calendar_path: str | None = None):
    raw = load_raw(input_path)
    hourly = bucket_to_hour(raw)
    hourly = add_cyclical_time_features(hourly)
    hourly = add_academic_calendar(hourly, calendar_path)
    hourly = add_lag_features(hourly)
    hourly = add_cross_location_features(hourly)

    # Recompute cyclical/calendar cols after reindexing introduced gap rows
    hourly = add_cyclical_time_features(hourly)
    if calendar_path:
        hourly = add_academic_calendar(hourly, calendar_path)

    hourly.to_csv(output_path, index=False)
    print(f"Wrote {len(hourly)} rows to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="../data/marino_counts.csv")
    parser.add_argument("--output", default="../data/features.csv")
    parser.add_argument("--calendar", default="../data/academic_calendar.csv",
                         help="academic calendar csv (date, semester_phase); pass --calendar '' to skip")
    args = parser.parse_args()

    build_features(args.input, args.output, args.calendar)
