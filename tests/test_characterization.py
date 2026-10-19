"""
test_characterization.py

Pins the pipeline's current numeric output on the synthetic fixture: the hourly
panel, the supervised frame, a served forecast, and a walk-forward report. The
other suites assert properties (no leakage, fold boundaries); this one asserts
that nothing moved, which is what a behaviour-preserving refactor has to prove.

A failure names the first value that changed. If the change was intended and
approved, regenerate the fixture deliberately and commit it with that change:
    GYMCAST_UPDATE_GOLDEN=1 venv/bin/python tests/test_characterization.py

Runs standalone (no pytest needed):
    venv/bin/python tests/test_characterization.py
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
sys.path.insert(0, str(TESTS_DIR))

import evaluate  # noqa: E402
import predict  # noqa: E402
import train  # noqa: E402
from features import FEATURE_COLS, TARGET_COL, build_panel, build_supervised_frame  # noqa: E402
from test_pipeline import synthetic_raw  # noqa: E402

GOLDEN = TESTS_DIR / "fixtures" / "characterization.json"
# LightGBM's default multithreading changes float summation order with the
# machine's core count, so a laptop and a CI runner can disagree in the last
# digits. A single deterministic thread makes the fixture portable.
DETERMINISTIC_LGB = {"deterministic": True, "force_row_wise": True, "num_threads": 1}
TOLERANCE = 1e-6
HORIZONS = [1, 6, 24]


def _rounded(values) -> list:
    return [None if not np.isfinite(v) else round(float(v), 9) for v in values]


def snapshot() -> dict:
    """Run every stage on the synthetic fixture and collect its numeric output."""
    # The stages print progress and an evaluation report; keep test output readable.
    with tempfile.TemporaryDirectory() as directory, \
         patch.dict(train.DEFAULT_PARAMS, DETERMINISTIC_LGB), \
         contextlib.redirect_stdout(io.StringIO()):
        root = Path(directory)
        raw_path, panel_path = root / "raw.csv", root / "panel.csv"
        synthetic_raw(weeks=10).to_csv(raw_path, index=False)
        panel = build_panel(str(raw_path), str(panel_path), None)

        frame = build_supervised_frame(panel, HORIZONS)
        supervised = {
            str(h): {col: round(float(np.nansum(rows[col].astype(float))), 6)
                     for col in FEATURE_COLS if col in rows}
            for h, rows in frame.groupby("horizon")
        }

        # Fit on everything but the last two days, then serve 24h from that origin.
        cutoff = panel["hour_bucket"].max() - np.timedelta64(48, "h")
        history = panel[panel["hour_bucket"] <= cutoff]
        bundle = train.fit_model(
            build_supervised_frame(history, HORIZONS).dropna(subset=[TARGET_COL]))
        served = predict.forecast(bundle, history, 24, None)
        forecast = {loc: _rounded(rows["predicted_count"])
                    for loc, rows in served.groupby("location_name")}

        report = evaluate.run(str(panel_path), n_folds=2, test_days=7, min_train_days=21,
                              horizons=HORIZONS, open_only=True, report_out=None,
                              predictions_out=str(root / "history.json"))
        exported = json.loads((root / "history.json").read_text())["rows"]

    return {
        "backend": "lightgbm" if train.HAS_LGB else "sklearn",
        "panel": {
            "rows": len(panel),
            "count_sum": round(float(panel["count"].sum()), 6),
            "campus_others_mean_sum": round(float(np.nansum(panel["campus_others_mean"])), 6),
        },
        "supervised_feature_sums": supervised,
        "forecast": forecast,
        "evaluation": {
            "aggregate": report["aggregate"],
            "folds": [{k: f[k] for k in ("cutoff", "n_train", "n_test_scored", "per_horizon")}
                      for f in report["folds"]],
            "exported_rows": len(exported),
            "exported_prediction_sum": round(sum(r["predicted_count"] for r in exported), 6),
        },
    }


def first_difference(expected, actual, path="") -> str | None:
    """Describe the first mismatch between two JSON-like values, or None."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            return f"{path or '<root>'}: keys {sorted(set(expected) ^ set(actual))} differ"
        for key in expected:
            found = first_difference(expected[key], actual[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path}: length {len(expected)} -> {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            found = first_difference(e, a, f"{path}[{i}]")
            if found:
                return found
        return None
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) \
            and not isinstance(expected, bool):
        return None if abs(expected - actual) <= TOLERANCE else f"{path}: {expected} -> {actual}"
    return None if expected == actual else f"{path}: {expected!r} -> {actual!r}"


def test_pipeline_output_matches_recorded_fixture():
    if not train.HAS_LGB:
        print("  SKIP  characterization fixture was recorded with LightGBM")
        return
    # JSON round trip so tuples, numpy scalars and string keys compare like the fixture.
    actual = json.loads(json.dumps(snapshot(), default=str))
    if os.environ.get("GYMCAST_UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n")
        print(f"  wrote {GOLDEN.relative_to(TESTS_DIR.parent)}")
        return
    assert GOLDEN.exists(), "fixture missing: run with GYMCAST_UPDATE_GOLDEN=1 to record it"
    expected = json.loads(GOLDEN.read_text())
    # Compare in pipeline order so a failure names the earliest stage that moved;
    # a panel change would otherwise surface as a downstream evaluation change.
    for stage in ("backend", "panel", "supervised_feature_sums", "forecast", "evaluation"):
        difference = first_difference(expected[stage], actual[stage], stage)
        assert difference is None, f"pipeline output changed at {difference}"


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
