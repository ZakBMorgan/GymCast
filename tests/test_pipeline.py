"""
test_pipeline.py

Guards the properties that are easy to break silently and hard to notice: that
no feature depends on the target hour, that walk-forward folds don't overlap,
and that the serving frame matches the training frame column for column.

A leak doesn't announce itself - it shows up as a suspiciously good MAE - so
these run as assertions rather than as something you eyeball.

Runs standalone (no pytest needed):
    python tests/test_pipeline.py
Or under pytest if you have it:
    pytest tests/
"""

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from features import (  # noqa: E402
    CATEGORICAL_COLS,
    FEATURE_COLS,
    TARGET_COL,
    add_campus_context,
    add_cyclical_time_features,
    add_academic_calendar,
    bucket_to_hour,
    load_raw,
    build_panel,
    build_supervised_frame,
    regularize_hourly,
)
from train import design_matrix, fit_model, model_columns, predict_with  # noqa: E402
import evaluate  # noqa: E402
import predict  # noqa: E402


LOCATIONS = {
    "Marino Center - Cardio": 120,
    "Marino Center - Studio A": 33,
    "SquashBusters - 4th Floor": 60,
}
OPEN_HOURS = range(6, 23)


def synthetic_raw(weeks: int = 10, seed: int = 0) -> pd.DataFrame:
    """
    Hourly polls with a plausible hour-of-week shape: a morning bump, an
    evening peak, quieter weekends, and hard zeros while closed.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-01-05 00:00")  # a Monday
    stamps = pd.date_range(start, periods=weeks * 7 * 24, freq="h")

    rows = []
    for loc, capacity in LOCATIONS.items():
        scale = capacity / 120
        for ts in stamps:
            is_open = ts.hour in OPEN_HOURS
            if not is_open:
                count = 0.0
            else:
                morning = 25 * np.exp(-((ts.hour - 8) ** 2) / 6)
                evening = 55 * np.exp(-((ts.hour - 18) ** 2) / 8)
                weekend = 0.55 if ts.weekday() >= 5 else 1.0
                base = (morning + evening) * weekend * scale
                count = max(0.0, base + rng.normal(0, 3))
            rows.append({
                "timestamp": ts,
                "location_name": loc,
                "count": round(count),
                "capacity": capacity,
                "percent": round(100 * count / capacity, 2),
                "is_open": is_open,
            })
    return pd.DataFrame(rows)


def synthetic_panel(weeks: int = 10, seed: int = 0) -> pd.DataFrame:
    hourly = bucket_to_hour(synthetic_raw(weeks, seed))
    hourly = regularize_hourly(hourly)
    hourly = add_cyclical_time_features(hourly)
    hourly = add_academic_calendar(hourly, None)
    hourly = add_campus_context(hourly)
    return hourly.sort_values(["location_name", "hour_bucket"]).reset_index(drop=True)


def _keyed(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.set_index(["location_name", "hour_bucket", "horizon"]).sort_index()


# --------------------------------------------------------------------------

def test_no_feature_depends_on_the_target_hour():
    """
    Perturb one location's count at hour T and assert that no feature on any
    row whose *target* is T moves.

    This is the property the old campus_total_count_same_hour violated: it
    summed every location at the target hour, own count included, so the label
    sat inside its own feature. A chronological split can't catch that, because
    the leak is within-row rather than across-time.
    """
    panel = synthetic_panel(weeks=6)
    horizons = [1, 6, 24]
    baseline = _keyed(build_supervised_frame(panel, horizons))

    loc = "Marino Center - Cardio"
    target_hour = panel["hour_bucket"].quantile(0.6).floor("h")

    bumped = panel.copy()
    hit = (bumped["location_name"] == loc) & (bumped["hour_bucket"] == target_hour)
    assert hit.sum() == 1, "expected exactly one panel row for the perturbed hour"
    bumped.loc[hit, TARGET_COL] += 1000
    bumped = add_campus_context(bumped)  # recompute campus context off the new count

    after = _keyed(build_supervised_frame(bumped, horizons))

    feature_cols = [c for c in FEATURE_COLS if c in baseline.columns]
    at_target = baseline.index.get_level_values("hour_bucket") == target_hour
    before_rows = baseline[at_target][feature_cols]
    after_rows = after.loc[before_rows.index, feature_cols]

    changed = [
        c for c in feature_cols
        if not np.allclose(
            before_rows[c].astype(float), after_rows[c].astype(float),
            equal_nan=True,
        )
    ]
    assert not changed, f"features leak the target hour's count: {changed}"

    # Sanity check on the probe itself: the label did move, so the comparison
    # above is not just confirming that nothing happened at all.
    assert (after.loc[before_rows.index, TARGET_COL]
            - baseline.loc[before_rows.index, TARGET_COL]).abs().max() == 1000


def test_origin_features_read_the_right_hour():
    """count_at_origin must equal the panel's count at origin_bucket, exactly."""
    panel = synthetic_panel(weeks=4)
    frame = build_supervised_frame(panel, [1, 3, 24])

    truth = panel.set_index(["location_name", "hour_bucket"])[TARGET_COL]
    lookup = pd.MultiIndex.from_arrays([frame["location_name"], frame["origin_bucket"]])
    expected = truth.reindex(lookup).to_numpy(dtype=float)
    actual = frame["count_at_origin"].to_numpy(dtype=float)

    ok = np.isclose(expected, actual, equal_nan=True)
    assert ok.all(), f"{(~ok).sum()} rows read the wrong origin hour"


def test_long_horizon_drops_lags_it_cannot_know():
    """
    Beyond 24h ahead, "same hour yesterday" would sit after the origin, so it
    has to go missing rather than quietly become a future read.
    """
    panel = synthetic_panel(weeks=4)
    frame = build_supervised_frame(panel, [24, 25, 48])

    assert frame.loc[frame["horizon"] == 24, "count_24h_before_target"].notna().any()
    for h in (25, 48):
        rows = frame[frame["horizon"] == h]
        assert rows["count_24h_before_target"].isna().all(), \
            f"horizon {h}h still carries a 24h lag it cannot observe"


def test_walk_forward_folds_do_not_overlap():
    """Each fold trains strictly before its test window, and folds march forward."""
    panel = synthetic_panel(weeks=10)
    frame = build_supervised_frame(panel, [1, 12, 24]).dropna(subset=[TARGET_COL])

    folds = evaluate.make_folds(frame, n_folds=4, test_days=7, min_train_days=14)
    assert len(folds) >= 2, f"expected multiple folds from 10 weeks, got {len(folds)}"

    for cutoff, test_end in folds:
        train, test = evaluate.split_fold(frame, cutoff, test_end)
        assert not train.empty and not test.empty
        assert train["hour_bucket"].max() <= cutoff < test["hour_bucket"].min()
        assert test["hour_bucket"].max() <= test_end
        assert test["origin_bucket"].min() >= cutoff
        assert (test["origin_bucket"] == cutoff).any(), "cutoff origins must be retained"
        assert (train["hour_bucket"] == cutoff).any(), "cutoff labels are available"
        assert (test["hour_bucket"] == test_end).any(), "test end is inclusive"
        assert test.attrs == {"cutoff": cutoff, "test_end": test_end}
        for h in (1, 12, 24):
            rows = test[test["horizon"] == h]
            assert rows["hour_bucket"].min() == cutoff + pd.Timedelta(hours=h)
        crossing = frame[
            (frame["origin_bucket"] < cutoff) & (frame["hour_bucket"] > cutoff)
        ]
        assert not crossing.empty, "fixture must exercise origins before model fitting"
        assert test.index.intersection(crossing.index).empty

    cutoffs = [c for c, _ in folds]
    assert cutoffs == sorted(cutoffs), "folds should advance through time"


def test_profile_baseline_is_fitted_in_fold():
    """
    The hour-of-week baseline must not see the test window.

    Fitting it once over the whole dataset is the quiet version of this
    mistake: the baseline gets a peek, looks stronger than it is, and the
    model's margin over it looks smaller than it is.
    """
    panel = synthetic_panel(weeks=8)
    frame = build_supervised_frame(panel, [1, 24]).dropna(subset=[TARGET_COL])
    cutoff = frame["hour_bucket"].quantile(0.7).floor("h")

    train = frame[frame["hour_bucket"] <= cutoff]
    test = frame[frame["hour_bucket"] > cutoff]

    from_train_only = evaluate.baseline_hour_weekday_profile(train, test)
    from_everything = evaluate.baseline_hour_weekday_profile(frame, test)

    assert not np.allclose(from_train_only, from_everything), \
        "baseline ignores its training argument - it is fitted on all rows"


def test_serving_columns_match_training_columns():
    """
    The design matrix at predict time must line up with the one at fit time.

    Previously each side ran astype("category") on its own frame, so a serving
    frame holding only "unknown" for semester_phase encoded it as 0 while
    training had it at some other code - the booster reads those codes as
    integers and there is no error to notice.
    """
    panel = synthetic_panel(weeks=6)
    frame = build_supervised_frame(panel, [1, 24]).dropna(subset=[TARGET_COL])
    bundle = fit_model(frame)

    # A serving frame that sees one location and one calendar phase.
    serving = frame[frame["location_name"] == "Marino Center - Studio A"].head(50).copy()
    serving["semester_phase"] = "unknown"

    levels, used_lgb = bundle["category_levels"], bundle["used_lgb"]
    X_train = design_matrix(frame, bundle["feature_cols"], levels, used_lgb)
    X_serve = design_matrix(serving, bundle["feature_cols"], levels, used_lgb)
    assert list(X_train.columns) == list(X_serve.columns)

    # On the LightGBM path the booster reads raw category codes, so the codes
    # themselves have to agree. On the sklearn path the same pinned levels are
    # what keeps get_dummies from emitting a narrower matrix at serving time,
    # which the column check above already covers.
    if used_lgb:
        for col in CATEGORICAL_COLS:
            if col in levels:
                assert list(X_serve[col].cat.categories) == levels[col]

    preds = predict_with(bundle, serving)
    assert len(preds) == len(serving) and np.isfinite(preds).all() and (preds >= 0).all()


def test_serving_features_match_training_features():
    """
    Forecasting from a panel truncated at T must produce exactly the features
    that the training frame carries for those same (target, horizon) rows.

    This pins down both failure modes at once. If predict.py assembled a
    feature differently from features.py, the two sides would disagree - that
    is train/serve skew, and it is silent, because a stale substitute is still
    a number the model will happily score. And if any feature read data after
    the origin, truncating the panel at T would change it - that is leakage.
    Equality rules out both.
    """
    panel = synthetic_panel(weeks=8)
    hours_ahead = 24

    origin = panel["hour_bucket"].max() - pd.Timedelta(hours=48)
    truncated = panel[panel["hour_bucket"] <= origin].copy()

    bundle = fit_model(
        build_supervised_frame(truncated, [1, 24]).dropna(subset=[TARGET_COL])
    )
    serving = predict.forecast(bundle, truncated, hours_ahead, None)

    reference = build_supervised_frame(panel, range(1, hours_ahead + 1))
    reference = reference[reference["origin_bucket"] == origin]

    key = ["location_name", "hour_bucket", "horizon"]
    # horizon is part of the join key, so it is an index level below, not a column.
    feature_cols = [c for c in FEATURE_COLS if c in reference.columns and c not in key]
    left = serving.set_index(key).sort_index()
    right = reference.set_index(key).sort_index()

    assert not left.empty, "forecast produced no rows"
    assert list(left.index) == list(right.index), "serving and training rows disagree"

    for col in feature_cols:
        assert np.allclose(
            left[col].astype(float), right[col].astype(float), equal_nan=True
        ), f"serving frame disagrees with training frame on {col}"


def test_raw_utc_timestamps_are_converted_before_hourly_bucketing():
    # UTC-derived raw date/hour/weekday deliberately disagree with the New York date.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "raw.csv"
        raw = (
            "timestamp,location_name,count,capacity,percent,is_open,date,hour,weekday\n"
            "2026-10-05T03:47:00,Cardio,20,100,20,True,2026-10-05,3,Monday\n"
            "2026-10-05T03:57:00+00:00,Cardio,40,100,40,True,2026-10-05,3,Monday\n"
            "2026-10-05T04:07:00Z,Cardio,50,100,50,True,2026-10-05,4,Monday\n"
        )
        path.write_text(raw)
        frame = load_raw(str(path))
        assert frame.iloc[0]["timestamp"].isoformat() == "2026-10-04T23:47:00-04:00"
        assert frame.iloc[1]["timestamp"].isoformat() == "2026-10-04T23:57:00-04:00"
        assert frame.iloc[2]["timestamp"].isoformat() == "2026-10-05T00:07:00-04:00"
        assert str(frame["timestamp"].dt.tz) == "America/New_York"
        hourly = add_cyclical_time_features(bucket_to_hour(frame))
        assert hourly["count"].tolist() == [30, 50]
        assert hourly.iloc[0]["hour_bucket"].isoformat() == "2026-10-04T23:00:00-04:00"
        assert hourly["hour"].tolist() == [23, 0]
        assert hourly["weekday"].tolist() == [6, 0]
        assert path.read_text() == raw, "loading must never rewrite raw history"


def test_hour_alignment_survives_dst_and_forecast_serialization():
    for stamps, expected_hours in [
        (["2026-10-09T17:03:27Z", "2026-10-09T18:08:00Z"], [13, 14]),
        (["2026-11-01T05:03:00Z", "2026-11-01T06:03:00Z"], [1, 1]),
        (["2026-03-08T06:03:00Z", "2026-03-08T07:03:00Z"], [1, 3]),
    ]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.csv"
            original = "timestamp,location_name,count,capacity,percent\n" + "".join(
                f"{stamp},Cardio,{count},100,{count}\n"
                for stamp, count in zip(stamps, [10, 20]))
            path.write_text(original)
            hourly = bucket_to_hour(load_raw(str(path)))
            assert hourly["hour_bucket"].dt.hour.tolist() == expected_hours
            assert hourly["hour_bucket"].dt.minute.eq(0).all()
            assert hourly["hour_bucket"].dt.second.eq(0).all()
            assert hourly["count"].tolist() == [10, 20]
            assert hourly["hour_bucket"].is_unique
            assert path.read_text() == original
            panel_path = Path(directory) / "panel.csv"
            hourly.to_csv(panel_path, index=False)
            reloaded = regularize_hourly(pd.read_csv(panel_path, parse_dates=["hour_bucket"]))
            assert reloaded["hour_bucket"].tolist() == hourly["hour_bucket"].tolist()
            panel = add_cyclical_time_features(regularize_hourly(reloaded.iloc[:1]))
            origins = predict.latest_origins(panel)
            extended = predict.extend_panel(panel, origins, 3, None)
            frame = build_supervised_frame(extended, [1, 2, 3])
            frame = frame[frame["origin_bucket"] == origins["Cardio"]].copy()
            frame["predicted_count"] = 30.0
            payload = predict.to_website_json(frame, {"Cardio": 100})
            targets = [pd.Timestamp(row["time"]) for row in payload["Cardio"]]
            assert len(targets) == 3
            assert all(t.minute == 0 and t.second == 0 for t in targets)
            assert all(t - origins["Cardio"] == pd.Timedelta(hours=h)
                       for t, h in zip(targets, [1, 2, 3]))
            # The API returns target strings verbatim, without another timezone conversion.
            import serve
            saved_path = serve.PREDICTIONS_FILE
            try:
                import json
                output = Path(directory) / "predictions.json"
                output.write_text(json.dumps({"locations": payload}))
                serve.PREDICTIONS_FILE = str(output)
                response = serve.app.test_client().get("/api/predictions")
                assert response.get_json()["locations"] == payload
            finally:
                serve.PREDICTIONS_FILE = saved_path


def test_timezone_aware_panel_uses_local_calendar_date():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw = root / "raw.csv"
        raw.write_text(
            "timestamp,location_name,count,capacity,percent,is_open,date,hour,weekday\n"
            "2026-10-05T03:47:00,Cardio,20,100,20,True,2026-10-05,3,Monday\n"
            "2026-10-05T04:47:00+00:00,Cardio,40,100,40,True,2026-10-05,4,Monday\n")
        calendar = root / "calendar.csv"
        calendar.write_text("date,semester_phase\n2026-10-04,break\n2026-10-05,regular\n")
        panel = build_panel(str(raw), str(root / "panel.csv"), str(calendar))
        assert panel["semester_phase"].tolist() == ["break", "regular"]
        assert panel["hour"].tolist() == [23, 0]
        supervised = build_supervised_frame(panel, [1])
        assert supervised.iloc[1]["count_at_origin"] == 20
        assert supervised.iloc[1]["hour"] == 0


def test_model_beats_persistence_on_synthetic_data():
    """
    An end-to-end smoke test. The synthetic series is a clean hour-of-week
    shape, so a model that cannot beat carrying the origin count forward 24h
    is wired up wrong somewhere.
    """
    panel = synthetic_panel(weeks=10)
    frame = build_supervised_frame(panel, [24]).dropna(subset=[TARGET_COL])
    cutoff = frame["hour_bucket"].quantile(0.75).floor("h")

    train = frame[frame["hour_bucket"] <= cutoff]
    test = frame[frame["hour_bucket"] > cutoff]

    bundle = fit_model(train)
    y = test[TARGET_COL].to_numpy(dtype=float)
    model_mae = np.mean(np.abs(predict_with(bundle, test) - y))

    persistence = evaluate.baseline_persistence(train, test)
    m = np.isfinite(persistence)
    persistence_mae = np.mean(np.abs(persistence[m] - y[m]))

    assert model_mae < persistence_mae, \
        f"model MAE {model_mae:.2f} did not beat persistence {persistence_mae:.2f}"
    assert set(model_columns(train)) == set(bundle["feature_cols"])


# --------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
