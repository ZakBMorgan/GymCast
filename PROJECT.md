# PROJECT.md — GymCast

Shared project context. Read [AGENTS.md](AGENTS.md) for working conventions,
[README.md](README.md) for the product introduction and local usage, and the
[pipeline walkthrough](docs/pipeline_walkthrough.md) for detailed explanations.

## Purpose

Help Northeastern students choose when to work out by forecasting hourly
occupancy at Marino Center and SquashBusters locations. Default output is the
next 24 hours per location. The pipeline, JSON API, and a basic local HTML/CSS/JavaScript frontend exist.
The frontend uses a responsive, restrained v2 layout with a native SVG chart,
forecast summary, and availability table, with quietest-hour
highlighting restricted to fully scheduled-open hours.

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

_Last updated: 2026-10-09_

- Collection, hourly panel construction, training, evaluation, prediction, and
  Flask API are implemented. Eleven synthetic pipeline tests cover timezone loading,
  feature leakage, fold
  boundaries, representation consistency, serving parity, and a model smoke test.
  Run `venv/bin/python tests/test_pipeline.py` for the standalone suite.
- The local September artifacts contain weeks of observations, not the original
  single poll. Generated data and model files remain gitignored; development
  and regression checks should use the synthetic fixture.
- The current `outputs/eval.json` reports 117,791 scored predictions over four
  weekly folds ending September 13, 20, 27, and October 4, 2026 (New York time).
  Open-hour evaluation covers horizons 1–24. Model MAE is 7.5749 occupants vs.
  8.5061 for the strongest overall baseline, `seasonal_naive_168h`: 10.9% lower
  MAE. Aggregate bias is -1.0408. These supersede the earlier September report;
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
The local export was generated with four default walk-forward folds: 117,791
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
  shows overall underprediction (bias -1.04 occupants).
- The frontend retains all forecast data and hides closed rows by default behind
  a Show closed hours toggle. It displays closed, partial (with scheduled open
  minutes), unknown, and special-hour labels. Quietest-hour highlighting requires
  both scheduled and effective status to be fully open. Current live reports and
  conflicts appear beside timestamps; expiry is rechecked every 30 seconds.
  Expired current evidence is presented as a timestamped schedule snapshot.
  A capacity-first overview stacks the target time, capped visual bar, and secondary
  predicted people count. The floating menu has a clear border/shadow; the forecast
  SVG uses a 760px minimum width with internal mobile scrolling; a secondary
  swipe/scroll hint appears only on narrow viewports with actual chart overflow.
  The dropdown has one visible keyboard focus ring and a down/up SVG chevron.
  Desktop overview padding is slightly tighter; mobile spacing is retained.
  The in-page location listbox groups recognizable facility names,
  retains unmatched locations, supports keyboard navigation, and enhances the
  native select only after successful population. The top quietest upcoming
  recommendation has a distinct restrained treatment. The optional
  `node tests/preview_frontend.cjs` uses hour-aligned synthetic targets (the earlier
  temporary preview retained wall-clock minutes). Synthetic-fixture Chrome
  previews at 1440, 375, 390, and 430px show no page-wide overflow. Final checks
  include keyboard-focused/open menus, viewport containment and stacking, chart
  scrolling/hint visibility, empty/expired states, and two-series history; iOS Safari
  and assistive technology have not been directly tested.
  A compact summary lists up to three quietest fully open hours; local date
  headings group the table. Metadata is compact and explanations are collapsible.
  The v2 layout uses CSS tokens and one continuous forecast surface; a native
  SVG chart includes all hours with closed/partial shading and preserves gaps.
  Summary metrics select the earliest strictly future forecast with `facility_status == "open"`,
  labelled Next fully open hour. Quietest recommendations also exclude elapsed hours.
  Browser time is rechecked every 30 seconds; metadata shows generation age and an
  expired-horizon warning. The current production forecast chart uses one consistent
  line for all points, retains closed/partial shading, and shows a Now marker when
  in range. Historical comparisons belong only to How GymCast performs. Loading uses GET only, never an automatic refresh POST.
  Summaries have no-upcoming/open-hours fallbacks. They are not live occupancy
  (which is absent from the API). No chart libraries or backend changes.
  Dependency-free Node rendering checks cover row/value preservation, status
  labels, highlighting, expiry, location switching, and chart rendering. Desktop
  and 390px mobile previews were inspected in headless Chrome using a saved-data
  fixture; neither viewport had page-wide overflow. Live API integration was
  not exercised by this visual check.
- The calendar join works, but `academic_calendar.csv` is header-only. Populate
  known dates and measure whether the feature helps; it is currently `unknown`.
- No automatic retraining or prediction intervals. Verify and maintain schedule
  exceptions; the weekly baseline alone does not establish real-time availability.
- Buckets are labelled at their start; the latest live bucket can be partial.
  Historical evaluation assumes origin-bucket observations are available.
  Completed-hour issuance and partial-hour behavior need explicit validation.
- Location names still key the panel; captured IDs do not yet protect against
  renames. Open-only evaluation training also differs from production training.

### Next work

1. Repeat walk-forward evaluation on later data and inspect coverage and bias.
2. Populate the academic calendar and evaluate on additional periods.
3. Connect the v2 frontend to the deployed API and review freshness on live data.
4. Define completed-hour refresh timing and automate collection supervision,
   prediction refresh, and retraining as separate jobs.
5. Consider intervals and forecast weather inputs after baseline validation.
