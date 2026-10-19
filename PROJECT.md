# PROJECT.md — GymCast

Shared project context. Read [AGENTS.md](AGENTS.md) for working conventions,
[README.md](README.md) for the product introduction and local usage, and the
[pipeline walkthrough](docs/pipeline_walkthrough.md) for detailed explanations.

## Purpose

Help Northeastern students choose when to work out by forecasting hourly
occupancy at Marino Center and SquashBusters locations. Default output is the
next 24 hours per location. The pipeline, JSON API, and a dependency-free HTML/CSS/JavaScript frontend
exist; the frontend is described under Current State.

## Architecture and data flow

```text
collector.py → raw CSV → features.py → hourly panel
                                         ├─ train.py → saved model bundle
                                         ├─ evaluate.py → fold metrics vs. baselines
                                         └─ predict.py + saved bundle → predictions.json
                                                                         ↓
                                                                     serve.py
                                                                         ↓
                                                                  local frontend
```

Training, evaluation, and live prediction are separate consumers of the panel.
Evaluation refits in each fold and never loads the production bundle. Storage
is flat CSV plus generated pickle/JSON artifacts; there is no database.

- **Collection:** a continuous loop polls public GoBoard counts about every
  300 seconds. It appends occupancy and metadata to `data/marino_counts.csv`,
  including `location_id` and inverted `IsClosed` as `is_open`. Missing closure
  flags remain unknown. Request failures go to `poll_errors.csv`. New poll and
  error timestamps use explicit UTC offsets. Legacy naive raw timestamps came
  from the UTC droplet and are interpreted as UTC by the raw loader.
- **Panel:** `load_raw()` parses mixed naive/aware raw timestamps as UTC and
  converts them to `America/New_York` before bucketing. Raw date/hour/weekday
  fields are ignored; local calendar dates and time features come from the
  converted buckets. `bucket_to_hour()` groups by location name and hour: mean count and
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

One direct multi-horizon regressor uses `horizon` as an input. The backend is
explicit: LightGBM by default, or scikit-learn's `HistGradientBoostingRegressor`
(which accepts missing numeric lag values) via `--backend sklearn` or
`GYMCAST_BACKEND`. An unavailable backend is an error, never a silent
substitution. These are alternatives, not an ensemble or automatic comparison.
`train.py` publishes through `publish_bundle()`: the pickled bundle is reloaded
and must reproduce finite predictions on the latest week before it replaces
`model.pkl`, and the replaced model is kept as `model.previous.pkl`.

`fit_model()` requires at least 50 rows with observed origins. This is an
eligibility check, not a filter removing every missing-origin row. Training
warns below 14 days of history; weekly features need at least a week.

The bundle stores `model`, `feature_cols`, `category_levels`, `used_lgb`,
`horizons`, `trained_through`, `n_train_rows`, and `params`. Bundles saved by
`train.py` also carry `provenance`: git commit (and whether tracked files were
modified), Python and library versions, and the features file's SHA-256. `design_matrix()`
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
`POST /api/refresh` (development server only) runs features → predict using
`sys.executable` and the source directory as working directory. It neither
collects nor retrains. CORS is also development-only: production serves the page
and API from one origin, and `create_app()` builds each variant. Collection,
refresh, and model retraining have separate lifecycles.

## Current State

_Last updated: 2026-10-19_

- Production runs collection, a 15-minute refresh, nightly retraining and the
  API under systemd; see [operations](docs/operations.md).
- Reliability pass (October 19): every artifact is written atomically through
  `fileio.py`; collector errors are redacted (the key had leaked into
  `poll_errors.csv`); the live-status read is ~190x faster; the history API
  caches per horizon; bundles record provenance. A characterization test and
  CI guard numeric output, which was verified identical on real data.
- Collection, hourly panel construction, training, evaluation, prediction, and
  Flask API are implemented. Eleven synthetic pipeline tests cover timezone loading,
  feature leakage, fold
  boundaries, representation consistency, serving parity, and a model smoke test.
  Run `venv/bin/python tests/test_pipeline.py` for the standalone suite.
- The local September artifacts contain weeks of observations, not the original
  single poll. Generated data and model files remain gitignored; development
  and regression checks should use the synthetic fixture.
- The current `outputs/eval.json` reports 116,928 scored predictions over four
  weekly folds ending September 26, October 3, 10, and 17, 2026 (New York time).
  Open-hour evaluation covers horizons 1–24. Model MAE is 6.1179 occupants vs.
  6.7490 for the strongest overall baseline, `seasonal_naive_168h`: 9.4% lower
  MAE. Aggregate bias is 1.7593. These supersede the earlier September report;
  they are aggregate results, not a claim of superiority at every horizon/fold.
- The origin-cutoff regression is fixed. The walkthrough now explains the
  separate workflows, known future context, backend encoding, and fold timing.
  README and agent guidance have been aligned with those distinctions.
- The previous local runs used sklearn because LightGBM could not load `libomp`.
  Check the printed backend for each new run; neither backend is tuned.

### Availability layer

`availability.py` attaches scheduled status after inference using the seeded
`data/facility_hours.json`, exact location-to-facility mappings, and date/time
intervals in `data/facility_overrides.json`. The overrides file is intentionally
empty: unrecorded Fall 2026 exceptions remain possible. No hours were invented.
Open/closed/partial/unknown statuses retain every forecast row and count; the
historical `is_open` column and model construction remain unchanged. Date overrides
now explicitly distinguish `overlay` (weekly fallback) from `replace` (uncovered
time closed). Legacy interval arrays retain overlay semantics. Replacement defaults
are attributed to the override; an empty replacement closes the entire date.

`scheduled_status` continues to classify the full forecast hour. Current-state
objects additionally expose `scheduled_status_now` and `schedule_source_now` at
`evaluated_at`; their effective `facility_status` uses that minute-level schedule
unless fresh live evidence overrides it. A 05:30 opening is current-closed at
05:15 and current-open at 05:45, with the hourly schedule remaining partial.

Current raw GoBoard flags are separate location-scoped evidence, expiring after
15 minutes or at the hour boundary. Current conflicts can override the effective
status for that hour only; scheduled status is preserved. Output records live
observation/expiry times and conflict metadata. Future clients must enforce
expiry even when reading cached JSON. See
[facility availability](docs/facility_availability.md) for schemas and semantics.
The standalone availability tests supplement the pipeline tests.

### Historical accuracy

Evaluation optionally collects the exact model outputs on the unchanged common
scoring mask; the CLI exports them by default to
`outputs/evaluation_predictions.json`. Rows retain target, origin, horizon, and
fold cutoff; no cross-horizon averaging or production-model reconstruction.
`GET /api/evaluation-history?horizon=6` filters the saved export without inference.
The frontend loads history separately and displays actuals versus fixed 6-hour
walk-forward predictions for the selected location, with series-specific MAE,
scope and date range. Unscored hours and fold boundaries remain chart gaps.
Current forecasting, availability, models, splits, baselines, and metrics are unchanged.
Run `venv/bin/python tests/test_evaluation_history.py` for export/API checks.
The local export was generated with four default walk-forward folds: 116,928
scored rows, including 5,112 at horizon 6. This is a new evaluation run, not
recovered predictions from the older aggregate report. Export/API and frontend
checks pass; saved-data desktop and 390px mobile previews show both series without
page-wide overflow.

### Raw timestamp correction

Legacy naive raw timestamps now mean UTC, not New York local time. The collector
writes explicit UTC offsets going forward. Hourly aggregation and ML behavior
are otherwise unchanged; calendar joins use the local civil date, and the live
raw-poll reader applies the same UTC interpretation. Aware hourly bucketing floors
in UTC before converting back to New York, retaining both repeated fall-back
hours without ambiguity. Mixed-offset saved panels normalize to one timezone-aware
axis before shifts and forecast extension. Synthetic coverage traces ordinary and DST buckets
through forecast target construction, JSON serialization, and the API. Raw CSVs
are not rewritten. See [timestamp investigation](docs/timestamp_investigation.md).
Rebuild features, retrain, regenerate predictions, and rerun evaluation before
using artifacts produced under the previous timestamp interpretation.

### Unfinished and open questions

- Extend evaluation to additional weeks and seasonal changes. Inspect per-horizon
  and per-fold rankings alongside aggregate improvement; the current report still
  shows overall overprediction (aggregate bias +1.76 occupants).
- **Frontend (redesigned October 19).** Two cards lead: *Right now* (the forecast
  for the hour in progress, or the next hour when none exists, plus the next
  three hours) and *Best time to go*. The best time is a window: consecutive
  fully open upcoming hours forecast within the location's 6-hour-ahead MAE of
  the quietest one (5% of capacity, min 2 people, until history loads), with
  alternatives only if they start within the same band. Crowd levels are percent
  of capacity: Quiet < 35%, Moderate < 65%, Busy otherwise; windows spanning
  levels say so ("Quiet to moderate"). A 24-hour HTML bar chart (roving tab stop,
  arrow keys, tap/hover readout) shows closed hours as hatched stubs and brackets
  the best window. The hour-by-hour table is collapsed by default; the accuracy
  section leads with "Typically within about N people". Dark mode follows the
  system setting; colors pass WCAG AA (text 4.5:1, bars 3:1) in both themes.
  Eligibility rules are unchanged: recommendations need scheduled and effective
  status fully open and a strictly future start; data is never mutated; loading
  is GET-only with a 10-minute background refetch. `node tests/test_frontend.js`
  covers the rules; `node tests/preview_frontend.cjs` (needs Chrome) checks real
  layout at 1440/375/390/430 px. iOS Safari and screen readers are untested.
- The calendar join works, but `academic_calendar.csv` is header-only. Populate
  known dates and measure whether the feature helps; it is currently `unknown`.
- No prediction intervals, and no scheduled evaluation: the model retrains
  nightly, but the deployed accuracy export dates from October 7. Verify and maintain schedule
  exceptions; the weekly baseline alone does not establish real-time availability.
- Buckets are labelled at their start; the latest live bucket can be partial.
  Historical evaluation assumes origin-bucket observations are available.
  Completed-hour issuance and partial-hour behavior need explicit validation.
- Location names still key the panel; captured IDs do not yet protect against
  renames. Open-only evaluation training also differs from production training.

### Next work

1. Repeat walk-forward evaluation on later data and inspect coverage and bias.
2. Populate the academic calendar and evaluate on additional periods.
3. Schedule `evaluate.py` so the accuracy chart tracks the nightly model.
4. Define completed-hour refresh timing. Run the services as a non-root user
   with memory limits (see [operations](docs/operations.md)).
5. Consider intervals and forecast weather inputs after baseline validation.
