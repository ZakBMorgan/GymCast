"""
features.py

Turns raw collector output (marino_counts.csv) into two things:

  1. An **hourly panel** - one row per (location, hour), regularized onto a
     continuous hourly index, holding the observed count plus context that is
     genuinely knowable ahead of time (calendar, capacity). This is what gets
     written to features.csv.

  2. A **supervised frame**, built on demand by build_supervised_frame() - one
     row per (location, forecast origin, horizon). Every feature on such a row
     is observable at the origin, so what the model trains on is exactly what
     predict.py can assemble at inference time.

Why the two-stage split: a feature time-aligned with the target (say, the
campus-wide count during the target hour) is both unknowable when forecasting
*and* a partial copy of the label. Keeping lag construction out of the panel
and parameterizing it by horizon makes that class of mistake hard to write.

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

TARGET_COL = "count"

# Model inputs. Each one is observable at the forecast origin - see the
# per-column comments in build_supervised_frame() for the argument.
FEATURE_COLS = [
    "horizon",
    "hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "is_weekend",
    "count_at_origin", "count_origin_prev", "rolling_3h_at_origin",
    "count_24h_before_target", "count_168h_before_target",
    "campus_others_mean_at_origin",
]
CATEGORICAL_COLS = ["semester_phase", "location_name"]

# Carried through the supervised frame for filtering and reporting, never fed
# to the model. is_open in particular describes the target hour, so using it as
# a feature would reintroduce a same-hour leak.
PASSTHROUGH_COLS = ["capacity", "is_open"]

DEFAULT_HORIZONS = tuple(range(1, 25))


def load_raw(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Legacy naive values came from the UTC droplet; new polls include an offset.
    # Mixed parsing allows both formats to coexist in the append-only raw CSV.
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed", utc=True)
    df["timestamp"] = df["timestamp"].dt.tz_convert("America/New_York")
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


def regularize_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reindex each location onto a gap-free hourly range.

    Every lag downstream is expressed as shift(N) rows, so a missing poll would
    otherwise silently slide the alignment - an hour of downtime would turn
    "same hour last week" into "one hour off, last week". Missing counts become
    NaN, which the model treats as missing rather than as zero occupancy.
    Idempotent, so it is safe to call again on an already-regular panel.
    """
    frames = []
    for loc, g in df.groupby("location_name", sort=False):
        g = g.set_index("hour_bucket").sort_index()
        if not g.index.is_unique:
            g = g[~g.index.duplicated(keep="last")]

        full_range = pd.date_range(g.index.min(), g.index.max(), freq="h")
        g = g.reindex(full_range)
        g["location_name"] = loc
        if "capacity" in g.columns:
            g["capacity"] = g["capacity"].ffill().bfill()

        frames.append(g.rename_axis("hour_bucket").reset_index())

    return pd.concat(frames, ignore_index=True)


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
    # Drop any prior semester_phase so re-running on an enriched panel replaces
    # the column instead of producing semester_phase_x / semester_phase_y.
    df = df.drop(columns=["semester_phase"], errors="ignore")

    if not calendar_path or not os.path.exists(calendar_path):
        df["semester_phase"] = "unknown"
        return df

    cal = pd.read_csv(calendar_path, parse_dates=["date"])
    if cal.empty:
        # Committed template with only a header row - nothing to join on.
        df["semester_phase"] = "unknown"
        return df

    # Calendar dates are local civil dates, not instants; match the naive CSV dates.
    df["date"] = df["hour_bucket"].dt.tz_localize(None).dt.floor("D")
    df = df.merge(cal, on="date", how="left")
    df["semester_phase"] = df["semester_phase"].fillna("unknown")
    df = df.drop(columns=["date"])
    return df


def add_campus_context(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mean count across *other* locations during the same hour.

    Two deliberate choices:
      - Excludes the row's own count. A plain campus-wide sum puts the target
        inside its own feature, which the model will happily exploit.
      - A mean over reporting peers rather than a sum, so the value doesn't
        lurch when a location drops out of the feed.

    Still same-hour, so it is unknowable at forecast time on its own.
    build_supervised_frame is the only consumer and it shifts the column back
    to the origin before the model ever sees it.
    """
    grp = df.groupby("hour_bucket")[TARGET_COL]
    total = grp.transform("sum")            # skips NaN
    n_reporting = grp.transform("count")    # non-NaN only

    own = df[TARGET_COL].fillna(0)
    own_reporting = df[TARGET_COL].notna().astype(int)
    others_n = (n_reporting - own_reporting).replace(0, np.nan)

    df["campus_others_mean"] = (total - own) / others_n
    return df


def build_supervised_frame(panel: pd.DataFrame, horizons=DEFAULT_HORIZONS) -> pd.DataFrame:
    """
    Expand the hourly panel into one row per (location, origin, horizon).

    A row's target is the count at hour `t`; its origin is `t - horizon`, the
    last hour whose observations you would actually hold when making that
    forecast. Every feature is therefore built from a shift of at least
    `horizon` rows.

    Rows whose target is unknown (NaN count) are kept - train/evaluate drop
    them, and predict.py depends on them to score future hours.
    """
    horizons = sorted({int(h) for h in horizons})
    if not horizons or horizons[0] < 1:
        raise ValueError("horizons must be positive integers (1 = next hour)")

    panel = regularize_hourly(panel)
    frames = []

    for loc, g in panel.groupby("location_name", sort=False):
        g = g.set_index("hour_bucket").sort_index()
        count = g[TARGET_COL]
        others = (
            g["campus_others_mean"] if "campus_others_mean" in g.columns
            else pd.Series(np.nan, index=g.index)
        )

        for h in horizons:
            f = pd.DataFrame(index=g.index)
            f["location_name"] = loc
            f["horizon"] = h
            f["origin_bucket"] = g.index - pd.Timedelta(hours=h)
            f[TARGET_COL] = count

            # --- everything below is observed at or before t - h ---
            f["count_at_origin"] = count.shift(h)             # count[t-h]
            f["count_origin_prev"] = count.shift(h + 1)       # count[t-h-1]
            # trailing 3h mean ending at the origin: count[t-h-2 .. t-h]
            f["rolling_3h_at_origin"] = count.shift(h).rolling(3, min_periods=1).mean()
            # same hour yesterday / last week, but only while those fall at or
            # before the origin. Past the horizon they would be future reads.
            f["count_24h_before_target"] = count.shift(24) if h <= 24 else np.nan
            f["count_168h_before_target"] = count.shift(168) if h <= 168 else np.nan
            f["campus_others_mean_at_origin"] = others.shift(h)

            for col in PASSTHROUGH_COLS + ["semester_phase"]:
                if col in g.columns:
                    f[col] = g[col]

            frames.append(f.rename_axis("hour_bucket").reset_index())

    out = pd.concat(frames, ignore_index=True)
    out = add_cyclical_time_features(out)
    return out.sort_values(["location_name", "hour_bucket", "horizon"]).reset_index(drop=True)


def build_panel(input_path: str, output_path: str, calendar_path: str | None = None) -> pd.DataFrame:
    raw = load_raw(input_path)
    hourly = bucket_to_hour(raw)
    hourly = regularize_hourly(hourly)
    hourly = add_cyclical_time_features(hourly)
    hourly = add_academic_calendar(hourly, calendar_path)
    hourly = add_campus_context(hourly)

    hourly = hourly.sort_values(["location_name", "hour_bucket"]).reset_index(drop=True)
    hourly.to_csv(output_path, index=False)

    span = hourly["hour_bucket"].max() - hourly["hour_bucket"].min()
    observed = int(hourly[TARGET_COL].notna().sum())
    print(f"Wrote {len(hourly)} panel rows ({observed} with observed counts) to {output_path}")
    print(f"Locations: {hourly['location_name'].nunique()}  |  span: {span}")
    return hourly


# Back-compat alias: serve.py and older notebooks call build_features().
build_features = build_panel


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="../data/marino_counts.csv")
    parser.add_argument("--output", default="../data/features.csv")
    parser.add_argument("--calendar", default="../data/academic_calendar.csv",
                        help="academic calendar csv (date, semester_phase); pass --calendar '' to skip")
    args = parser.parse_args()

    build_panel(args.input, args.output, args.calendar)
