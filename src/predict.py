"""
predict.py

Generates near-future occupancy predictions per location and writes them to a
JSON file the website can fetch directly (as a static file, or via serve.py).

Inference works by extending each location's hourly panel with empty future
hours and running it through the same build_supervised_frame() that training
uses, then keeping the rows whose origin is the latest observed hour. Sharing
that one code path is the point: the alternative - hand-assembling future rows
here - is how a serving frame ends up holding a slightly different feature than
the one the model was fit on, with nothing to raise an error about it.

Usage (run from src/, defaults point at ../models, ../data, ../outputs):
    python predict.py
    python predict.py --model ../models/model.pkl --features ../data/features.csv \
                      --hours-ahead 24 --output ../outputs/predictions.json
"""

import argparse
import json

import pandas as pd

from availability import (
    TIMEZONE,
    current_availability,
    forecast_availability,
    load_config,
    read_live_observations,
    timestamp_text,
)
from features import (
    add_academic_calendar,
    add_cyclical_time_features,
    build_supervised_frame,
)
from train import load_bundle, predict_with


def latest_origins(panel: pd.DataFrame) -> pd.Series:
    """
    Last hour per location with an *observed* count.

    Not simply the last row: regularize_hourly pads the panel to a continuous
    hourly index, so a stalled collector leaves trailing NaN rows. Forecasting
    from one of those would mean seeding every lag feature with NaN.
    """
    observed = panel.dropna(subset=["count"])
    if observed.empty:
        raise ValueError("panel has no observed counts - has collector.py been running?")
    return observed.groupby("location_name")["hour_bucket"].max()


def extend_panel(panel: pd.DataFrame, origins: pd.Series, hours_ahead: int,
                 calendar_path: str | None) -> pd.DataFrame:
    """Append empty future hours so the supervised frame has target rows to score."""
    frames = []
    for loc, g in panel.groupby("location_name", sort=False):
        g = g.sort_values("hour_bucket")
        start = origins[loc] + pd.Timedelta(hours=1)
        future = pd.date_range(start, periods=hours_ahead, freq="h")
        future = future[future > g["hour_bucket"].max()]

        pad = pd.DataFrame({"hour_bucket": future})
        pad["location_name"] = loc
        if "capacity" in g.columns:
            pad["capacity"] = g["capacity"].ffill().iloc[-1] if len(g) else None
        frames.append(pd.concat([g, pad], ignore_index=True))

    out = pd.concat(frames, ignore_index=True)
    # Calendar context for the padded hours is knowable in advance, so it gets
    # recomputed over the extended index rather than carried forward.
    out = add_cyclical_time_features(out)
    out = add_academic_calendar(out, calendar_path)
    return out


def forecast(bundle: dict, panel: pd.DataFrame, hours_ahead: int,
             calendar_path: str | None) -> pd.DataFrame:
    origins = latest_origins(panel)
    extended = extend_panel(panel, origins, hours_ahead, calendar_path)

    frame = build_supervised_frame(extended, horizons=range(1, hours_ahead + 1))
    frame["_origin"] = frame["location_name"].map(origins)
    frame = frame[frame["origin_bucket"] == frame["_origin"]].copy()

    trained = bundle.get("horizons") or []
    if trained and hours_ahead > max(trained):
        print(f"WARNING: forecasting {hours_ahead}h ahead from a model trained on "
              f"horizons up to {max(trained)}h. Retrain with "
              f"--max-horizon {hours_ahead} for honest long-range numbers.")

    frame["predicted_count"] = predict_with(bundle, frame)
    return frame.sort_values(["location_name", "hour_bucket"])


def to_website_json(frame: pd.DataFrame, capacity_lookup: dict, hours: dict | None = None,
                    overrides: dict | None = None, current: dict | None = None) -> dict:
    output = {}
    for loc, group in frame.groupby("location_name"):
        capacity = capacity_lookup.get(loc)
        entries = []
        for _, row in group.iterrows():
            pct = (row["predicted_count"] / capacity * 100) if capacity else None
            entries.append({
                "time": timestamp_text(row["hour_bucket"]),
                "horizon_hours": int(row["horizon"]),
                "predicted_count": round(float(row["predicted_count"]), 1),
                "predicted_percent": round(pct, 1) if pct is not None else None,
                **forecast_availability(loc, row["hour_bucket"], hours or {}, overrides or {},
                                        (current or {}).get(loc)),
            })
        output[loc] = entries
    return output


def main(model_path: str, features_path: str, hours_ahead: int, output_path: str,
         calendar_path: str | None, hours_path: str | None = "../data/facility_hours.json",
         overrides_path: str | None = "../data/facility_overrides.json",
         live_path: str | None = "../data/marino_counts.csv"):
    hours, overrides = load_config(hours_path, overrides_path)
    bundle = load_bundle(model_path)
    panel = pd.read_csv(features_path, parse_dates=["hour_bucket"])

    frame = forecast(bundle, panel, hours_ahead, calendar_path)

    capacity_lookup = (
        panel.dropna(subset=["capacity"])
        .groupby("location_name")["capacity"].last().to_dict()
        if "capacity" in panel.columns else {}
    )
    now = pd.Timestamp.now(tz=TIMEZONE)
    observations = read_live_observations(live_path)
    current = {loc: current_availability(loc, hours, overrides, observations, now)
               for loc in frame["location_name"].unique()}
    result = to_website_json(frame, capacity_lookup, hours, overrides, current)

    origins = {loc: timestamp_text(t) for loc, t in latest_origins(panel).items()}
    with open(output_path, "w") as f:
        json.dump({
            "generated_at": now.isoformat(),
            "availability_timezone": TIMEZONE,
            "unrecorded_exceptions_possible": True,
            "current_availability": current,
            "forecast_origin": origins,
            "model_trained_through": str(bundle.get("trained_through")),
            "locations": result,
        }, f, indent=2)

    print(f"Wrote {hours_ahead}h predictions for {len(result)} locations to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="../models/model.pkl")
    parser.add_argument("--features", default="../data/features.csv")
    parser.add_argument("--hours-ahead", type=int, default=24)
    parser.add_argument("--output", default="../outputs/predictions.json")
    parser.add_argument("--calendar", default="../data/academic_calendar.csv")
    parser.add_argument("--facility-hours", default="../data/facility_hours.json")
    parser.add_argument("--facility-overrides", default="../data/facility_overrides.json")
    parser.add_argument("--live-observations", default="../data/marino_counts.csv",
                        help="raw polls for current-hour evidence; pass '' to disable")
    args = parser.parse_args()

    try:
        main(args.model, args.features, args.hours_ahead, args.output, args.calendar,
             args.facility_hours, args.facility_overrides, args.live_observations)
    except ValueError as exc:
        parser.exit(1, f"predict.py: {exc}\n")
