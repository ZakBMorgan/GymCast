# PROJECT.md — GymCast

Shared project context. Read [AGENTS.md](AGENTS.md) for working conventions,
[README.md](README.md) for the product introduction and local usage, and the
[pipeline walkthrough](docs/pipeline_walkthrough.md) for detailed explanations.

## Purpose

Help Northeastern students choose when to work out by forecasting hourly
occupancy at Marino Center and SquashBusters locations. Default output is the
next 24 hours per location. The pipeline and JSON API exist; the student-facing
frontend does not yet exist.

## Architecture and data flow

```text
collector.py → raw CSV → features.py → hourly panel
                                         ├─ train.py → saved model bundle
                                         ├─ evaluate.py → fold metrics vs. baselines
                                         └─ predict.py + saved bundle → predictions.json
                                                                         ↓
                                                                     serve.py
                                                                         ↓
                                                                  frontend (planned)
```

Training, evaluation, and live prediction are separate consumers of the panel.
Evaluation refits in each fold and never loads the production bundle. Storage
is flat CSV plus generated pickle/JSON artifacts; there is no database.

- **Collection:** a continuous loop polls public GoBoard counts about every
  300 seconds. It appends occupancy and metadata to `data/marino_counts.csv`,
  including `location_id` and inverted `IsClosed` as `is_open`. Missing closure
  flags remain unknown. Request failures go to `poll_errors.csv`.
- **Panel:** `bucket_to_hour()` groups by location name and hour: mean count and
  percentage, max capacity and open flag. `regularize_hourly()` inserts missing
  hours with unknown counts. Capacity is filled as stable metadata. Time,
  calendar, and same-hour peer context are added; `features.csv` contains no lags.
- **Supervised rows:** `build_supervised_frame()` regularizes again and expands
  the panel into `(location, origin, horizon)` examples. `hour_bucket` is the
  target hour; `origin_bucket = hour_bucket - horizon`. Unknown targets survive
  construction for prediction; training/evaluation drop unlabelled targets.

## Information available at forecast time

Observed occupancy inputs must come from the origin or earlier. For a historical
input `count[t-k]` and horizon `h`, this requires `k >= h`. Target-hour clock and
calendar information is allowed because it is known in advance; those features
are not shifted. `capacity` and observed target-hour `is_open` are passthrough
metadata, not model inputs.

Numeric inputs are `horizon`, `hour_sin/cos`, `weekday_sin/cos`, `is_weekend`,
`count_at_origin`, `count_origin_prev`, `rolling_3h_at_origin`,
`count_24h_before_target`, `count_168h_before_target`, and
`campus_others_mean_at_origin`. Categoricals are `semester_phase` and `location_name`.

The trailing mean ends at the origin and uses up to three observations. Seasonal
lags become NaN beyond horizons 24 and 168 respectively. Campus context is the
mean of other reporting locations, excluding the location itself; it reaches
the model only after shifting to the origin. Exclusion alone would not make
same-target-hour peer observations safe.

Hourly regularization must precede positional shifts. Prediction pads future
rows with unknown counts, reuses the supervised builder, and retains each
location's latest observed origin. Shared construction prevents training and
serving from assigning different meanings to a feature.

## Model and representation

One direct multi-horizon regressor uses `horizon` as an input. LightGBM is
preferred; `ImportError` or `OSError` selects scikit-learn's
`HistGradientBoostingRegressor`, which accepts missing numeric lag values.
These are alternative backends, not an ensemble or automatic model comparison.

`fit_model()` requires at least 50 rows with observed origins. This is an
eligibility check, not a filter removing every missing-origin row. Training
warns below 14 days of history; weekly features need at least a week.

The bundle stores `model`, `feature_cols`, `category_levels`, `used_lgb`,
`horizons`, `trained_through`, `n_train_rows`, and `params`. `design_matrix()`
reapplies the training vocabulary: pandas categoricals for LightGBM, fixed
one-hot columns for sklearn. Unseen categories become missing, represented
as all-zero indicators in the fallback. Old bundles lacking category metadata
are rejected with a retraining instruction.

## Evaluation

Expanding-window walk-forward defaults: 4 folds, 7 test days, 21 minimum training
days. Roughly 28 days permit one fold. Each fold uses:

```text
training: target <= cutoff
testing:  cutoff < target <= test_end AND origin >= cutoff
```

A model fitted through the cutoff cannot simulate forecasts issued before it.
Origins equal to the cutoff are allowed, assuming that bucket's observations
are available. Longer horizons lose the first `horizon - 1` target hours of each
fold. The fitted model stays fixed while origin observations advance.

Baselines are persistence, same-hour-yesterday, same-hour-last-week, and a
location × weekday × hour mean with location/global fallbacks. The strongest
baseline is determined empirically. Category vocabulary and profile means are
fitted only on training rows.

The default open-hours filter is applied before splitting, so it restricts
both training and testing; unknown flags are retained. Production `train.py`
uses all labelled hours. All methods score on the same rows where the target
and every prediction are finite. The report includes dropped rows, MAE/RMSE/bias,
per-horizon MAE, and skill against the best baseline.

Aggregate metrics are scored-row-weighted means of fold metrics. Aggregate
RMSE is consequently a mean of fold RMSEs, not RMSE pooled over all errors.
The common mask excludes horizons beyond 24 because the 24h baseline is NaN
there. Repeated tuning against these folds invalidates their held-out role.

## Prediction and API

`predict.py` uses the saved bundle and latest observed hour per location.
Forecasting beyond the trained range warns but proceeds. Counts are clipped at
zero, not capacity. Output includes `generated_at`, per-location
`forecast_origin`, `model_trained_through`, and `locations` containing
`time`, `horizon_hours`, `predicted_count`, and `predicted_percent`.
Percentages are null without capacity; they can exceed 100%.

`GET /api/predictions` reads the saved JSON without inference.
`POST /api/refresh` runs features → predict using `sys.executable` and the source
directory as working directory. It neither collects nor retrains. Collection,
refresh, and model retraining have separate lifecycles.

## Current State

_Last updated: 2026-10-04_

- Collection, hourly panel construction, training, evaluation, prediction, and
  Flask API are implemented. Eight synthetic tests cover feature leakage, fold
  boundaries, representation consistency, serving parity, and a model smoke test.
  All 8 passed on October 4 using `venv/bin/python tests/test_pipeline.py`.
- The local September artifacts contain weeks of observations, not the original
  single poll. Generated data and model files remain gitignored; development
  and regression checks should use the synthetic fixture.
- The September 30 evaluation reported 126,840 scored predictions over four
  folds: MAE 7.82 vs. 8.85 for the weekly baseline, 11.7% skill. **These numbers
  predate the origin-cutoff correction and are not current validated scores.**
- The origin-cutoff regression is fixed. The walkthrough now explains the
  separate workflows, known future context, backend encoding, and fold timing.
  README and agent guidance have been aligned with those distinctions.
- The previous local runs used sklearn because LightGBM could not load `libomp`.
  Check the printed backend for each new run; neither backend is tuned.

### Unfinished and open questions

- Rerun corrected evaluation before making accuracy claims. Check additional
  weeks, seasonal changes, per-horizon rankings, and underprediction seen in the
  old report. The earlier first-fold weakness needs reassessment too.
- `web/` is a placeholder. Build hour/location comparisons and show data freshness
  using `forecast_origin`, not just file creation time.
- The calendar join works, but `academic_calendar.csv` is header-only. Populate
  known dates and measure whether the feature helps; it is currently `unknown`.
- No automatic retraining, prediction intervals, or opening-hours schedule.
- Buckets are labelled at their start; the latest live bucket can be partial.
  Historical evaluation assumes origin-bucket observations are available.
  Completed-hour issuance and partial-hour behavior need explicit validation.
- Location names still key the panel; captured IDs do not yet protect against
  renames. Open-only evaluation training also differs from production training.

### Next work

1. Rerun corrected walk-forward evaluation and inspect coverage and bias.
2. Populate the academic calendar and evaluate on additional periods.
3. Build the dashboard with freshness information.
4. Define completed-hour refresh timing and automate collection supervision,
   prediction refresh, and retraining as separate jobs.
5. Consider intervals and forecast weather inputs after baseline validation.
