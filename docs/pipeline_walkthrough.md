# GymCast Pipeline Walkthrough

For the product introduction and commands, start with [README.md](../README.md).
For concise architecture and current status, see [PROJECT.md](../PROJECT.md).
Examples below are illustrative; they are not measured forecasts or a guarantee
that every named location is currently present in the feed.

This document is meant to explain GymCast at a level where I can describe not only **what each stage does**, but **why it exists**, what information it receives, and what information it is allowed to use.

A recurring question throughout the forecasting pipeline is:

> **Could GymCast actually know this value at the forecast origin?**

That question is central to avoiding data leakage.

---

# 1. Pipeline Overview

GymCast starts with live occupancy observations from Northeastern's GoBoard API and eventually produces future occupancy forecasts that can be consumed by a frontend.

At a high level:

```text
GoBoard API
    ↓
collector.py
    ↓
raw ~5-minute observations
    ↓
features.py
    ↓
hourly panel
```

Each workflow constructs supervised forecasting examples from the panel using
the shared `build_supervised_frame()` function. Those rows are built in memory;
`features.csv` stores the hourly panel.

From there, the system branches into three related workflows:

```text
                         hourly panel
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
      TRAINING            EVALUATION          LIVE PREDICTION
```

Training creates a model:

```text
historical data
    ↓
train.py
    ↓
trained model bundle
    ↓
saved model file
```

Evaluation measures whether the modeling approach actually works:

```text
historical data
    ↓
evaluate.py
    ↓
walk-forward folds
    ↓
model + baseline predictions
    ↓
evaluation metrics
```

Live prediction uses the saved trained model:

```text
latest hourly panel
      +
saved model bundle
      ↓
predict.py
      ↓
predictions.json
      ↓
serve.py
      ↓
frontend
```

The important distinction is:

```text
training
→ creates the learned model

evaluation
→ measures whether the modeling approach works

prediction
→ uses the learned model on current data
```

Evaluation is therefore not a required step between training and prediction.

---

# 2. Collecting Historical Data

## `collector.py`

GymCast's source of truth is the GoBoard occupancy API.

`collector.py` repeatedly polls the API and records observations such as:

```text
timestamp
location_name
count
capacity
percent
is_open
```

Important GoBoard fields include:

```text
LastCount
→ current occupancy count

TotalCapacity
→ gym capacity

open/closed state
→ normalized into is_open
```

The collector runs continuously and appends observations to a historical CSV.

Conceptually:

```text
GoBoard
   ↓
fetch_data()
   ↓
extract useful fields
   ↓
append_rows()
   ↓
historical observations
```

## Polling Interval

A setting such as:

```python
POLL_SECONDS = 300
```

means the running collector waits roughly five minutes between polling cycles.

It does **not** mean an external scheduler launches `collector.py` every five minutes.

## `is_open`

`is_open` is collected as contextual metadata.

It is preserved through later parts of the pipeline and is especially useful for deciding which rows should be scored during evaluation.

It is **not** one of the model's predictive input features.

### Key Takeaway

> **`collector.py` records what is happening now so that GymCast can eventually learn from what happened in the past.**

---

# 3. Converting Raw Observations Into Hourly Data

## `bucket_to_hour()`

The collector operates at approximately five-minute resolution, but GymCast's forecasting pipeline operates at hourly resolution.

`bucket_to_hour()` bridges those two resolutions.

Before bucketing, `load_raw()` reads the raw timestamp strings, parses them
with `pd.to_datetime(..., format="mixed", utc=True)`, and converts them to
`America/New_York`. Old naive timestamps came from the UTC droplet; new collector
timestamps include an explicit UTC offset. For example, `2026-10-05T03:47:00`
becomes `2026-10-04T23:47:00-04:00`. The raw CSV is not modified.

Raw `date`, `hour`, and `weekday` describe the UTC observation and are ignored
when constructing local time features. Calendar joins use the converted local
civil date.

The bucketing function then floors each converted timestamp to its hour:

```python
df["hour_bucket"] = df["timestamp"].dt.floor("h")
```

So:

```text
2:05 PM
2:10 PM
2:55 PM
```

all belong to:

```text
2:00 PM
```

The dataframe is then grouped by:

```python
["location_name", "hour_bucket"]
```

so each location receives its own hourly summary.

The aggregation rules are:

```text
count
→ mean

percent
→ mean

capacity
→ max

is_open
→ max, if present
```

For example:

```text
2:05 PM → 40
2:10 PM → 44
2:15 PM → 48
2:20 PM → 52
        ↓
bucket_to_hour()
        ↓
2:00 PM → mean occupancy during the hour
```

The hourly count is:

```text
(40 + 44 + 48 + 52) / 4
= 46
```

Using the mean makes the hourly value represent the typical observed occupancy during that hour rather than one arbitrary five-minute snapshot.

## Capacity

Capacity should normally remain stable.

GymCast uses the maximum reported value within the hour:

```text
capacity
→ max
```

rather than averaging a property that conceptually should not fluctuate.

## `is_open`

If represented as:

```text
closed = 0
open   = 1
```

then:

```python
agg["is_open"] = "max"
```

means:

```text
max(0, 0, 1, 1)
= 1
```

Therefore:

> **If the gym was observed as open during any polling interval in that hour, the hourly row is treated as open.**

### Key Takeaway

> **One hourly row summarizes the observations collected throughout that hour.**

---

# 4. Creating a Reliable Hourly Timeline

## `regularize_hourly()`

Even after hourly aggregation, an hour may be completely absent if GymCast failed to collect any observations during that hour.

For example:

```text
1 PM → 20
2 PM → 30
4 PM → 50
```

There is no row for 3 PM.

`regularize_hourly()` creates a continuous hourly timeline independently for each location:

```text
1 PM → 20
2 PM → 30
3 PM → NaN
4 PM → 50
```

Conceptually, it:

```text
separates each location
      ↓
sorts chronologically
      ↓
handles duplicate hourly timestamps
      ↓
creates every expected hourly timestamp
      ↓
reindexes onto the full hourly range
```

The important operations are equivalent to:

```python
full_range = pd.date_range(
    g.index.min(),
    g.index.max(),
    freq="h"
)

g = g.reindex(full_range)
```

`pd.date_range()` creates every expected hour.

`reindex()` forces the location's observations onto that complete timeline.

Missing occupancy observations therefore become `NaN`.

## Why This Matters for Lag Features

Later, GymCast uses operations such as:

```python
shift(1)
shift(24)
shift(168)
```

`shift(N)` means:

> Move back **N rows**.

It does not inherently mean:

> Move back N hours.

Those become equivalent only when:

```text
1 dataframe row = exactly 1 real hour
```

Without regularization:

```text
Time       Count

1 PM        20
2 PM        30
4 PM        50
```

a one-row shift on the 4 PM row would return the 2 PM count.

That would incorrectly imply:

```text
lag_1h at 4 PM = count at 2 PM
```

After regularization:

```text
Time       Count

1 PM        20
2 PM        30
3 PM       NaN
4 PM        50
```

a one-row shift correctly gives:

```text
lag_1h at 4 PM = NaN
```

because GymCast genuinely does not know what happened at 3 PM.

This becomes particularly important for:

```python
shift(168)
```

because:

```text
168 hours = 7 days
```

GymCast can interpret 168 rows ago as **the same hour one week earlier** only because each real hour occupies one row.

## Missing Capacity vs. Missing Occupancy

Capacity can be carried across inserted rows because it is treated as a stable property.

For example:

```text
2 PM    capacity = 270
3 PM    capacity = NaN
4 PM    capacity = 270
```

can safely become:

```text
2 PM    capacity = 270
3 PM    capacity = 270
4 PM    capacity = 270
```

using forward/backward filling.

Occupancy cannot be handled the same way.

```text
count = 0
```

means:

> The gym was observed and had zero occupancy.

while:

```text
count = NaN
```

means:

> GymCast has no observation for this hour.

Those meanings are fundamentally different.

## Why Insert Missing Rows If Training Later Drops Some of Them?

A missing target row may eventually be unusable for supervised training.

It still needs to exist **before lag features are created**.

Without:

```text
3 PM → NaN
```

the 4 PM row could accidentally treat 2 PM as one hour earlier.

So even when an inserted row never becomes a valid training target, it protects later rows from temporal misalignment.

### Key Takeaway

> **`regularize_hourly()` makes dataframe row distance correspond to actual elapsed time.**

---

# 5. Turning the Hourly Panel Into Forecasting Examples

## 5.1 Hourly Panel vs. Supervised Data

`features.py` produces a general-purpose hourly panel.

The hourly panel says:

```text
At this location, at this hour, what was happening?
```

It is not yet organized into explicit forecasting examples.

Training later calls:

```python
load_supervised()
```

which loads the panel and passes it into:

```python
build_supervised_frame()
```

The supervised frame instead says:

```text
Given what was known at one point in time,
predict occupancy at a later target time.
```

## 5.2 Schema Sanity Check

`load_supervised()` can detect older panel formats by checking for columns such as:

```text
count_1hr_ago
```

This is a **schema/version sanity check**.

It is not a test for stale data, time alignment, or leakage.

## 5.3 Defensive Regularization

`build_supervised_frame()` regularizes the panel again before constructing row-based lag features.

This defensively preserves the invariant:

```text
1 row = 1 real hour
```

even if the supplied panel was not perfectly regular.

## 5.4 Target, Origin, and Horizon

Every supervised example has three important time concepts:

```text
hour_bucket
→ target time

origin_bucket
→ time the forecast is issued

horizon
→ distance from origin to target
```

Formally:

```text
origin = target - horizon
```

Suppose the target is:

```text
6 PM
```

Then GymCast can construct several examples:

```text
target = 6 PM, origin = 5 PM, horizon = 1
target = 6 PM, origin = 4 PM, horizon = 2
target = 6 PM, origin = 3 PM, horizon = 3
```

The target hour is the same, but each example answers a different forecasting question.

This distinction drives:

- leakage prevention
- fold splitting
- live-origin filtering
- per-horizon evaluation

## 5.5 Unknown Future Targets Are Preserved

`build_supervised_frame()` does not immediately discard rows where:

```text
target count = NaN
```

That is intentional.

For historical training/evaluation:

```text
target must eventually be known
```

because GymCast needs the actual answer.

For live prediction:

```text
future target count is intentionally unknown
```

because that is exactly what GymCast is trying to predict.

This lets the same feature builder serve both historical and future examples.

## 5.6 Basic Leakage Rule

Suppose a feature uses:

```text
count[t-k]
```

for a forecast with horizon `h`.

The value is safe only when:

```text
k >= h
```

because an observed occupancy feature must come from the forecast origin or
earlier. This restriction does not require shifting the known target timestamp
or its calendar context; section 7 explains that distinction.

The universal question is:

> **Was this value actually available when the forecast was issued?**

## 5.7 Supervised Row vs. Model Input

A supervised dataframe can contain columns used for:

```text
training
evaluation
bookkeeping
output
```

without every column entering the model.

For example:

```text
is_open
hour_bucket
origin_bucket
capacity
```

may exist in the supervised dataframe while not being part of `X`.

The design matrix is selected later.

### Key Takeaway

> **A supervised row describes an entire forecasting example; the model later receives only the subset of columns explicitly selected as features.**

---

# 6. Giving the Model Historical Context

## Lag Features

`build_supervised_frame()` constructs historical features that were available at or before the forecast origin.

These include:

```text
count_at_origin
count_origin_prev
rolling_3h_at_origin
count_24h_before_target
count_168h_before_target
campus_others_mean_at_origin
```

## `count_at_origin`

For target `t` and horizon `h`:

```python
count_at_origin = count.shift(h)
```

represents:

```text
count[t-h]
```

which is the occupancy observed exactly at the forecast origin.

## `count_origin_prev`

```python
count_origin_prev = count.shift(h + 1)
```

represents occupancy one hour before the forecast origin.

## `rolling_3h_at_origin`

The recent trailing average is constructed from:

```python
count.shift(h).rolling(3, min_periods=1).mean()
```

Suppose:

```text
target  = 6 PM
horizon = 3
origin  = 3 PM
```

Then the three-hour rolling feature summarizes approximately:

```text
1 PM
2 PM
3 PM
```

leading up to and including the origin.

Because:

```python
min_periods=1
```

the calculation can still exist when fewer than all three observations are available.

## Target-Relative Seasonal Features

GymCast also uses:

```text
count_24h_before_target
count_168h_before_target
```

These use the target time as their reference.

### 24-Hour Feature

```text
count_24h_before_target
= count[t - 24]
```

This is safe only when:

```text
h <= 24
```

If the forecast horizon exceeds 24 hours, `t - 24` may still lie in the future relative to the origin.

In that case, the feature becomes unavailable rather than leaking future information.

### 168-Hour Feature

Likewise:

```text
count_168h_before_target
= count[t - 168]
```

is safe only when:

```text
h <= 168
```

Unsafe target-relative historical features become `NaN`.

---

## Campus Context

### `add_campus_context()`

GymCast also asks:

> What are the other gyms doing at this time?

For each location and hour, `add_campus_context()` computes the mean occupancy among **other reporting locations**.

Suppose at 5 PM:

```text
Marino          80
SquashBusters   40
Cabot           30
```

For Marino:

```text
campus_others_mean
= mean(40, 30)
= 35
```

For SquashBusters:

```text
campus_others_mean
= mean(80, 30)
= 55
```

The row's own occupancy is deliberately excluded.

## Why Exclude the Current Location?

A naive campus-wide total or average could contain the target location's own value.

If supplied at the target hour, the model could partially exploit the thing
it is supposed to predict. An aggregate at the origin would be historical;
excluding the current location makes this feature specifically peer context.
The shift to the origin is still required to avoid future information.

Instead:

```text
campus_others_mean
→ external cross-location context
```

## Why Use a Mean Rather Than a Sum?

A sum would be highly sensitive to how many gyms happened to report during that hour.

For example:

```text
Hour A:
7 peers report
sum = 210

Hour B:
4 peers report
sum = 130
```

The sum might fall mostly because several locations disappeared from the feed.

Using the mean of reporting peers makes the feature more stable with respect to reporting count.

## Missing Peer Data

The function tracks how many peers actually reported.

If there are no other reporting locations:

```text
campus_others_mean = NaN
```

rather than dividing by zero or inventing a value.

## Preventing Leakage

Raw:

```text
campus_others_mean[t]
```

is a same-target-hour quantity.

That would not be knowable when forecasting the future.

Therefore `build_supervised_frame()` creates:

```python
campus_others_mean_at_origin = others.shift(h)
```

which represents:

```text
campus_others_mean[t-h]
```

Suppose:

```text
target  = 6 PM
horizon = 3
origin  = 3 PM
```

The model sees:

```text
mean occupancy of the other gyms at 3 PM
```

not their future 6 PM activity.

### Key Takeaway

> **GymCast combines the target gym's own historical behavior with historical context from the rest of campus, while restricting all occupancy information to what was available at the forecast origin.**

---

# 7. Giving the Model Time and Calendar Context

Occupancy depends not only on recent history, but also on **when the target hour occurs**.

## 7.1 Time Features

GymCast derives timestamp information such as:

```text
hour
weekday
is_weekend
```

and cyclical encodings:

```text
hour_sin
hour_cos
weekday_sin
weekday_cos
```

These are safe future features because the target timestamp is known in advance.

## Why Cyclical Features?

Raw clock values have a wrap-around structure.

For example:

```text
23:00
00:00
```

are one hour apart in reality, even though raw integers `23` and `0` look far apart numerically.

The same issue exists for:

```text
Sunday
Monday
```

Sine/cosine encodings place these values on a circle so cyclical neighbors remain close in representation.

The raw values describe **which time it is**.

The sine/cosine pairs describe the **cyclical position** of that time.

---

## 7.2 Academic Calendar Context

Occupancy can also vary with the academic calendar.

GymCast associates each date with information such as:

```text
semester_phase
```

The process is conceptually:

```text
hour_bucket
    ↓
convert to date
    ↓
left join calendar table
    ↓
semester_phase
```

Before merging, an old `semester_phase` column is removed to avoid accidental duplicated merge columns.

If the calendar is unavailable or a date has no matching entry:

```text
semester_phase = "unknown"
```

The temporary date column is then removed.

Because the calendar phase depends on the target date rather than future occupancy, it is safe to use for forecasting.

## Future Rows

This becomes especially useful during live prediction.

A future row may have:

```text
count = NaN
```

because future occupancy is unknown.

But GymCast can still know:

```text
hour
weekday
weekend status
semester phase
```

because those depend only on the future timestamp/date.

### Key Takeaway

> **Time and academic-calendar features describe known properties of the target hour rather than unknown future occupancy.**

---

# 8. Constructing One Forecasting Example

Suppose GymCast constructs:

```text
location_name                 = Marino
hour_bucket                   = Friday 6 PM
origin_bucket                 = Friday 3 PM
horizon                       = 3

count                         = 80
```

Here:

```text
count = y
```

the true target GymCast is trying to predict.

The row may also contain:

```text
count_at_origin               = 55
count_origin_prev             = 47
rolling_3h_at_origin          = 44

count_24h_before_target       = 72
count_168h_before_target      = 76

campus_others_mean_at_origin  = 38

hour                          = 18
weekday                       = 4
is_weekend                    = 0

hour_sin                      = ...
hour_cos                      = ...
weekday_sin                   = ...
weekday_cos                   = ...

semester_phase                = fall
```

These features fall into several conceptual groups.

```text
Identity / forecast context
→ location_name
→ horizon

Recent history
→ count_at_origin
→ count_origin_prev
→ rolling_3h_at_origin

Seasonal history
→ count_24h_before_target
→ count_168h_before_target

Campus context
→ campus_others_mean_at_origin

Known future context
→ cyclical time features
→ is_weekend
→ semester_phase

Answer
→ count
```

## Supervised Row vs. `X`

The complete row may also contain:

```text
hour_bucket
origin_bucket
capacity
is_open
```

These can be useful for:

```text
identification
evaluation
website formatting
```

without entering the model itself.

So:

```text
supervised row
≠
model input X
```

`design_matrix()` later selects only the feature columns the model is allowed to consume.

### Key Leakage Question

Before allowing a feature into a forecasting example:

> **Could GymCast actually know this value at the forecast origin?**

---

# 9. Training the Model

## `train.py`

Training turns historical supervised examples into a learned forecasting model.

Conceptually:

```text
hourly panel
    ↓
load_supervised()
    ↓
labelled examples
    ↓
fit_model()
    ↓
trained bundle
    ↓
save_bundle()
```

---

## 9.1 Training Requires Known Targets

`train_model()` removes rows where the target is unknown:

```python
labelled = frame.dropna(subset=[TARGET_COL])
```

This is different from `build_supervised_frame()`, which preserves unknown future targets.

```text
feature construction
→ may preserve target NaN

training
→ requires known target y
```

If no labelled rows exist, training fails.

---

## 9.2 Short-History Warning

`train_model()` checks how much calendar time the labelled data spans.

If there are fewer than roughly 14 days, it prints a warning.

This is not a hard failure.

The warning exists because:

```text
168h weekly lag
→ needs at least one full week

walk-forward evaluation
→ needs several weeks to create useful folds
```

A model can still be produced, but it should be treated as a smoke test.

This is distinct from hard eligibility checks inside `fit_model()`.

```text
train_model()
→ warns about weak historical coverage

fit_model()
→ enforces sufficient usable training rows
```

---

## 9.3 Selecting Model Features

The supervised dataframe contains more information than the model receives.

The actual feature set is:

### Numeric / Direct Model Features

```text
horizon

hour_sin
hour_cos
weekday_sin
weekday_cos
is_weekend

count_at_origin
count_origin_prev
rolling_3h_at_origin

count_24h_before_target
count_168h_before_target

campus_others_mean_at_origin
```

### Categorical Features

```text
semester_phase
location_name
```

Notably, the model does **not** directly receive:

```text
count
hour_bucket
origin_bucket
capacity
is_open
raw hour
raw weekday
```

`count` is the target `y`.

The others are used for bookkeeping, evaluation, output, or intermediate feature construction.

---

## 9.4 Building the Design Matrix

Conceptually:

```text
supervised dataframe
      ↓
model feature list
      ↓
design_matrix()
      ↓
X
```

The design matrix is the actual representation sent to the machine-learning model.

---

## 9.5 Categorical Vocabulary

Training records the category levels that existed when the model was fit.

For example:

```text
location_name:
- Marino
- SquashBusters
- Cabot

semester_phase:
- fall
- break
- unknown
```

These category definitions are later reused at prediction time.

This prevents the same categorical value from silently receiving a different representation after training.

---

## 9.6 LightGBM and the scikit-learn Fallback

GymCast's default model is:

```text
LightGBM LGBMRegressor
```

If LightGBM cannot be imported, GymCast falls back to:

```text
scikit-learn HistGradientBoostingRegressor
```

GymCast is not normally training both and selecting whichever wins.

The sklearn model is a compatibility fallback.

For LightGBM, categorical variables can remain categorical.

For the sklearn fallback, categorical variables are converted into numeric indicator columns.

---

## 9.7 Fitting the Model

Conceptually:

```text
X
+
y
↓
model.fit(X, y)
↓
learned model
```

Before fitting:

```text
the estimator knows its algorithm and configuration
but has not learned GymCast occupancy patterns
```

After fitting:

```text
the estimator contains learned parameters derived from historical X → y examples
```

A deeper explanation of gradient boosting and LightGBM internals can be treated separately from this pipeline walkthrough.

---

## 9.8 The Model Bundle

`fit_model()` returns more than the estimator itself.

The bundle contains information such as:

```text
model
feature_cols
category_levels
used_lgb
horizons
trained_through
n_train_rows
params
```

The distinction is:

```text
trained estimator
→ learned prediction function

model bundle
→ estimator + everything GymCast needs to use it correctly later
```

---

## 9.9 Saving the Bundle

`train_model()` calls:

```python
bundle = fit_model(labelled)
save_bundle(bundle, model_out)
```

`save_bundle()` uses:

```python
with open(path, "wb") as f:
    pickle.dump(bundle, f)
```

Conceptually:

```text
bundle in Python memory
      ↓
pickle.dump()
      ↓
serialized binary file on disk
```

`"wb"` means:

```text
write binary
```

Serialization allows the model and its metadata to survive after the training process exits.

---

## 9.10 Loading the Bundle

Prediction later calls:

```python
bundle = load_bundle(model_path)
```

which performs:

```python
with open(path, "rb") as f:
    bundle = pickle.load(f)
```

`"rb"` means:

```text
read binary
```

So:

```text
saved model file
      ↓
pickle.load()
      ↓
bundle restored in Python memory
```

`load_bundle()` also checks that the bundle contains:

```text
category_levels
```

If not, it rejects the old model and asks for retraining.

That prevents prediction from guessing how an older incompatible model encoded categorical data.

---

## 9.11 Training Lifecycle

The complete lifecycle is:

```text
train_model()
    ↓
load_supervised()
    ↓
known-target rows
    ↓
fit_model()
    ↓
bundle in memory
    ↓
save_bundle()
    ↓
model file on disk

later...

predict.py
    ↓
load_bundle()
    ↓
validate bundle compatibility
    ↓
bundle restored in memory
    ↓
forecast()
```

### Key Takeaway

> **Training creates both a learned estimator and a saved contract describing exactly how that estimator must be used later.**

---

# 10. Evaluating the Model

## `evaluate.py`

GymCast uses **walk-forward validation** because forecasting must respect the ordering of time.

Randomly mixing old and future rows could allow unrealistic information flow.

Instead:

```text
train on the past
      ↓
predict a later period
      ↓
move forward
      ↓
repeat
```

---

## 10.1 Creating Walk-Forward Folds

`make_folds()` produces:

```text
(cutoff, test_end)
```

pairs.

Conceptually:

```text
PAST                         FUTURE

training history │ evaluation period
─────────────────│──────────────────
                 ↑
               cutoff
```

GymCast uses an **expanding-window** strategy.

For example:

```text
Fold 1
TRAIN: earliest data -------- cutoff 1
TEST:                        cutoff 1 ---- test end 1

Fold 2
TRAIN: earliest data ---------------- cutoff 2
TEST:                                cutoff 2 ---- test end 2

Fold 3
TRAIN: earliest data ------------------------ cutoff 3
TEST:                                        cutoff 3 ---- test end 3
```

Older history is retained as the training window expands.

This is useful while the dataset is relatively small.

## Minimum Training History

Before accepting a fold:

```python
if cutoff - t_min < pd.Timedelta(days=min_train_days):
    continue
```

prevents evaluation from trying to fit a model before sufficient historical data exists.

---

## 10.2 Preparing Evaluation Rows

Evaluation begins with:

```python
frame = load_supervised(input_path, horizons)
```

and removes rows without a known target:

```python
frame = frame.dropna(subset=[TARGET_COL])
```

Evaluation needs both:

```text
prediction
and
actual observed answer
```

---

## 10.3 Evaluating Open Hours Only

When open-only evaluation is enabled:

```python
frame = frame[
    frame["is_open"].fillna(True).astype(bool)
]
```

`is_open` therefore acts as evaluation metadata.

```text
is_open
   ↓
decide whether row should be scored
```

It is not a predictive model feature.

This filter runs before `split_fold()`, so it restricts both training and test
rows in evaluation. Production `train.py` uses all labelled hours.

Closed hours may often have trivially low occupancy.

Including large numbers of closed hours could make all forecasting methods look artificially good while hiding differences during the hours users actually care about.

If `is_open` is missing, GymCast keeps the row rather than automatically treating it as closed.

---

## 10.4 Splitting Each Fold Without Temporal Leakage

The fold split must consider both:

```text
target time
and
forecast origin
```

Training rows are:

```python
train_df = frame[
    frame["hour_bucket"] <= cutoff
]
```

The test rows are:

```python
test_df = frame[
    (frame["hour_bucket"] > cutoff)
    & (frame["hour_bucket"] <= test_end)
    & (frame["origin_bucket"] >= cutoff)
].copy()
```

The conditions mean:

```text
hour_bucket > cutoff
→ target occurs after training

hour_bucket <= test_end
→ target stays inside this fold's test window

origin_bucket >= cutoff
→ forecast was not issued before this model existed
```

### Why Target Time Alone Is Not Enough

Suppose:

```text
cutoff  = Monday 12 PM
target  = Monday 1 PM
horizon = 24 hours
origin  = Sunday 1 PM
```

A target-only split would put this row in the test set because:

```text
Monday 1 PM > Monday 12 PM
```

But the forecast would supposedly have been issued:

```text
Sunday 1 PM
```

while the model was not trained until:

```text
Monday 12 PM
```

That would mean:

```text
model trained Monday
      ↓
pretend it made a prediction Sunday
```

which is impossible.

It would introduce look-ahead leakage into the historical simulation.

### Why `origin_bucket >= cutoff`

The model is considered available beginning at the cutoff.

Therefore:

```text
origin == cutoff
```

is permitted.

This assumes the cutoff bucket's observations are available when fitting
finishes. Buckets are labelled by the start of the hour, while their mean can
include polls throughout that hour. The latest live bucket can also be partial.
The evaluation enforces ordering between buckets; it does not yet validate
exact issuance timing or partial-hour parity with live prediction.

---

## 10.5 Fixed-Model Deployment Simulation

The model is trained once at the fold cutoff.

It remains fixed throughout that fold.

For example:

```text
cutoff = Monday noon

1 PM forecast origin
→ may use observations available through 1 PM

2 PM forecast origin
→ may use observations available through 2 PM

3 PM forecast origin
→ may use observations available through 3 PM
```

but:

```text
model parameters
→ still the model trained at Monday noon
```

So:

```text
new observations
≠
new model training
```

This resembles a deployed model receiving fresh inputs between retraining cycles.

---

## 10.6 Evaluating One Fold

Every fold trains a fresh model:

```python
bundle = fit_model(train_df)
```

The saved production model is not reused.

GymCast predicts:

```python
preds = {
    "model": predict_with(bundle, test_df)
}
```

and runs every baseline on the same fold:

```python
for name, fn in BASELINES.items():
    preds[name] = fn(train_df, test_df)
```

The true answers are:

```python
y = test_df[TARGET_COL]
```

Conceptually:

```text
y
→ what actually happened

model prediction
→ what GymCast predicted

baseline prediction
→ what the simpler rule predicted
```

---

## 10.7 Scoring Everyone on the Same Rows

Different forecasting methods may not produce a valid value for every example.

For example, a weekly seasonal baseline may be unavailable when there is insufficient previous-week data.

GymCast constructs a shared validity mask:

```python
common = np.isfinite(y)

for p in preds.values():
    common &= np.isfinite(p)
```

A row is scored only when:

```text
actual target is valid
model prediction is valid
every baseline prediction is valid
```

Example:

```text
Row   Actual   Model   24h Baseline   168h Baseline   Scored?

A       50       48         52              49          yes
B       40       43         41             NaN          no
C       70       68         72              74          yes
```

This gives an apples-to-apples comparison.

Otherwise, one method might appear better simply because it was evaluated on easier rows.

This also limits coverage: beyond horizon 24, the 24-hour baseline is unavailable,
so the current common mask excludes those horizons entirely. Dropped-row counts
refer to invalid predictions within the eligible test set, not rows already
excluded by the origin cutoff or open-hours filter.

---

## 10.8 Fold Results

For each fold, evaluation records information such as:

```text
cutoff
test_end
training rows
scored test rows
dropped test rows
model/baseline metrics
MAE by horizon
```

Per-horizon results answer questions like:

```text
How accurate is GymCast 1 hour ahead?
How accurate is it 6 hours ahead?
How accurate is it 24 hours ahead?
```

---

## 10.9 Aggregating Folds

After usable folds are evaluated:

```python
agg = aggregate(results)
```

combines them into an overall report. Each metric is averaged across folds,
weighted by the number of scored rows. For MAE and bias this matches pooling
all scored errors. Aggregate RMSE is a weighted mean of fold RMSEs, which is
not the same as taking the square root of the pooled mean squared error.

The report can include:

```text
evaluation configuration
aggregate metrics
individual fold results
```

The current saved report scores 117,791 predictions across four weekly folds
with open-hour targets and horizons 1–24. Model MAE is 7.57 occupants, 10.9%
lower than the strongest overall baseline (same hour last week, MAE 8.51).
Use unrounded values when calculating the improvement. These aggregate results
supersede the September 30 report; see [Current State](../PROJECT.md#current-state)
for scope and remaining evaluation questions.

### Key Takeaway

> **A valid walk-forward fold aligns both target time and forecast origin with the point at which the model became available.**

---

# 11. Baselines

GymCast should not be judged only by whether its prediction error is "low."

It should be compared against simple forecasting strategies called **baselines**.

The question is:

> **Does the trained model provide predictive value beyond a much simpler rule?**

GymCast currently compares against four baselines.

---

## 11.1 Persistence

Persistence predicts that occupancy stays where it is now:

```python
return test_df["count_at_origin"].to_numpy(dtype=float)
```

Example:

```text
origin = 3 PM
target = 6 PM
count at origin = 52

prediction = 52
```

Its assumption is:

> **Occupancy will remain at its current level.**

Persistence can be especially competitive at short horizons.

---

## 11.2 Seasonal Naive — 24 Hours

The 24-hour baseline uses:

```python
count_24h_before_target
```

Example:

```text
target = Tuesday 6 PM
Monday 6 PM occupancy = 73

prediction = 73
```

Its assumption is:

> **Today will look like yesterday at the same time.**

The feature is only valid when that historical observation was already available at the forecast origin.

---

## 11.3 Seasonal Naive — 168 Hours

The weekly baseline uses:

```python
count_168h_before_target
```

Since:

```text
168 hours = 7 days
```

a Monday 6 PM forecast uses the previous Monday at 6 PM.

Its assumption is:

> **This week's occupancy pattern will repeat last week's pattern.**

Gym traffic often has strong weekly structure, making this a potentially strong baseline.

Again, the value is only valid where the historical observation was available at the forecast origin.

---

## 11.4 Hour/Weekday Profile

This baseline learns the historical mean for:

```text
(location_name, weekday, hour)
```

using only the training portion of the fold.

Conceptually:

```python
key = ["location_name", "weekday", "hour"]

profile = (
    train_df
    .groupby(key, observed=True)[TARGET_COL]
    .mean()
)
```

Example historical values:

```text
Marino | Monday | 6 PM | 70
Marino | Monday | 6 PM | 80
Marino | Monday | 6 PM | 75
```

produce:

```text
Marino | Monday | 6 PM → 75
```

Its assumption is:

> **This gym will look like it usually does at this time of the week.**

Unlike persistence and seasonal naive methods, it averages across historical observations.

---

## 11.5 Profile Fallbacks

A training fold may never have observed a specific:

```text
(location, weekday, hour)
```

combination.

The fallback hierarchy is:

```text
(location, weekday, hour) mean
        ↓ if unavailable

location mean
        ↓ if unavailable

overall training-set mean
```

The baseline therefore stays as specific as possible while still trying to produce a prediction.

---

## 11.6 What the Baselines Represent

```text
Persistence
→ "Occupancy will stay where it is now."

24-hour seasonal
→ "This hour will look like the same hour yesterday."

168-hour seasonal
→ "This hour will look like the same hour last week."

Hour/weekday profile
→ "This gym will look like it usually does at this time of the week."
```

GymCast's trained model is intended to beat these methods by combining several sources of information.

Evaluation determines whether it actually does.

The strongest baseline is determined **empirically** by the evaluation results.

A baseline is not automatically strongest just because it looks more sophisticated in code.

### Key Takeaway

> **Baselines establish the level of accuracy GymCast needs to beat before its extra complexity can be considered useful.**

---

# 12. Evaluation Metrics

GymCast compares predictions against observed occupancy using:

```python
err = y_pred - y_true
```

Therefore:

```text
positive error
→ prediction was too high

negative error
→ prediction was too low
```

Metrics are calculated on the common valid row set established during evaluation, where the actual target, model prediction, and all baseline predictions are finite.

---

## 12.1 Mean Absolute Error — MAE

```python
np.mean(np.abs(err))
```

Example errors:

```text
+10
-4
+6
```

become absolute errors:

```text
10
4
6
```

MAE averages those magnitudes.

It answers:

> **On average, how many people is the prediction off by?**

The error direction does not matter.

Lower MAE is better.

---

## 12.2 Root Mean Squared Error — RMSE

```python
np.sqrt(np.mean(err ** 2))
```

RMSE squares errors before averaging.

That gives larger misses disproportionate influence.

For example:

```text
errors:
2, 2, 10

squared:
4, 4, 100
```

The 10-person miss has much more influence than either 2-person miss.

RMSE answers:

> **How large are prediction errors when larger mistakes are penalized more heavily?**

If RMSE is substantially larger than MAE, the model likely has some relatively large misses.

Lower RMSE is better.

---

## 12.3 Bias

Bias is:

```python
np.mean(err)
```

Because the sign remains:

```text
bias > 0
→ model tends to overpredict

bias < 0
→ model tends to underpredict

bias ≈ 0
→ positive and negative errors roughly cancel
```

Bias measures **direction**, not simply error size.

For example:

```text
errors = +20, -20

bias = 0
```

even though both predictions were poor.

Therefore:

> **Low bias does not necessarily mean high accuracy.**

---

## 12.4 Per-Horizon MAE

Overall MAE combines forecasts from many horizons.

`per_horizon_mae()` instead groups by:

```text
horizon
```

and calculates MAE separately.

Conceptually:

```text
all scored predictions
        ↓
group by horizon
        ↓
1h MAE
2h MAE
3h MAE
...
```

This answers:

> **How does forecasting accuracy change as GymCast predicts farther into the future?**

Baseline rankings can also change by horizon.

For example, persistence may be difficult to beat at one hour ahead while becoming much weaker farther into the future.

---

## 12.5 Interpreting the Metrics Together

```text
MAE
→ What is the typical size of the error?

RMSE
→ Are there large misses receiving extra weight?

Bias
→ Does the model systematically predict too high or too low?

Per-horizon MAE
→ How does accuracy change with forecast distance?
```

These should be interpreted together.

### Key Takeaway

> **MAE measures typical error size, RMSE emphasizes large misses, bias measures systematic direction, and per-horizon MAE shows how performance changes as the forecast extends farther into the future.**

---

# 13. Generating a Real Forecast

## `predict.py`

Live prediction combines:

```text
saved trained bundle
+
latest hourly panel
+
requested forecast range
+
known calendar context
```

to produce future occupancy forecasts.

---

## 13.1 Loading the Model and Panel

`predict.py` begins with:

```python
bundle = load_bundle(model_path)

panel = pd.read_csv(
    features_path,
    parse_dates=["hour_bucket"]
)
```

So:

```text
saved model
    ↓
load_bundle()

latest feature data
    ↓
panel
```

are reunited before forecasting.

---

## 13.2 Finding the Forecast Origin

`forecast()` finds the latest real observed hour for every location:

```python
origins = latest_origins(panel)
```

These timestamps are the actual forecast origins.

Different locations may theoretically have different latest available observations.

---

## 13.3 Extending the Panel Into the Future

Historical data naturally stops at the latest observation.

To predict beyond that point:

```python
extended = extend_panel(
    panel,
    origins,
    hours_ahead,
    calendar_path
)
```

For example:

```text
latest observation = 3 PM
hours_ahead        = 3
```

creates future rows for:

```text
4 PM
5 PM
6 PM
```

The future occupancy values are:

```text
NaN
```

because those are what GymCast is trying to predict.

Capacity can be carried forward because it is treated as stable.

Time and academic-calendar features can be recomputed because their future values are known.

### Key Distinction

> **`extend_panel()` creates future timeline rows. `build_supervised_frame()` turns those rows into forecasting examples.**

---

## 13.4 Constructing Future Forecasting Examples

Prediction reuses:

```python
build_supervised_frame()
```

with horizons:

```python
range(1, hours_ahead + 1)
```

If:

```text
hours_ahead = 3
```

then:

```text
h = 1
h = 2
h = 3
```

are constructed.

Because the builder is general-purpose, a 6 PM target may initially produce:

```text
target = 6 PM, origin = 5 PM, h = 1
target = 6 PM, origin = 4 PM, h = 2
target = 6 PM, origin = 3 PM, h = 3
```

But if the latest real observation is 3 PM, only the last example can actually be issued now.

---

## 13.5 Keeping Only the Real Forecast Origin

`forecast()` filters candidate examples using:

```python
frame["origin_bucket"] == frame["_origin"]
```

where `_origin` stores the latest real observed hour for that location.

For a real origin of 3 PM:

```text
3 PM → 4 PM, h=1
3 PM → 5 PM, h=2
3 PM → 6 PM, h=3
```

are the live forecasts.

This allows one generalized supervised builder to support training, evaluation, and prediction.

---

## 13.6 Forecasting Beyond the Training Range

The bundle records the horizons used during training.

If GymCast was trained through:

```text
24 hours
```

and is asked for:

```text
48 hours
```

prediction can still technically be attempted.

However, GymCast prints a warning because the requested horizon lies outside the range represented during training.

This is a warning, not a hard rejection.

---

## 13.7 Making the Prediction

`predict_with()` reconstructs the model-ready matrix using saved training metadata:

```python
X = design_matrix(
    df,
    bundle["feature_cols"],
    bundle["category_levels"],
    bundle["used_lgb"]
)
```

Prediction therefore reuses:

```text
same feature columns
same category vocabulary
same backend-specific representation
```

Then:

```python
bundle["model"].predict(X)
```

produces raw predicted counts.

Finally:

```python
np.clip(..., 0, None)
```

forces occupancy to be at least zero.

Example:

```text
raw prediction     final

-3.2               0
24.7               24.7
51.1               51.1
```

because negative gym occupancy is impossible. Counts are not capped at capacity,
so predicted percentages can exceed 100%. The output contains point estimates,
without prediction intervals. Availability is now attached after occupancy
prediction as a separate metadata layer; see
[facility availability](facility_availability.md). It preserves closed-hour
counts, uses maintained weekly hours and verified overrides, and confines live
GoBoard evidence to the current hour. Historical `is_open` remains unchanged.

---

## 13.8 Capacity Lookup

After producing predicted counts, `predict.py` finds each location's most recent known capacity.

Conceptually:

```text
latest known capacity by location
→ capacity_lookup
```

Capacity is therefore:

```text
not a model feature
but
useful output context
```

---

## 13.9 Formatting Forecasts for the Website

`to_website_json()` groups predictions by location.

A flat prediction dataframe such as:

```text
Marino | 4 PM | h=1 | 52.4
Marino | 5 PM | h=2 | 61.8
Cabot  | 4 PM | h=1 | 20.1
```

becomes conceptually:

```text
{
    "Marino": [
        {...4 PM...},
        {...5 PM...}
    ],
    "Cabot": [
        {...4 PM...}
    ]
}
```

Each prediction contains:

```text
time
horizon_hours
predicted_count
predicted_percent
```

For example, if:

```text
predicted_count = 135
capacity        = 270
```

then:

```text
predicted_percent = 50.0
```

If capacity is unavailable:

```text
predicted_percent = null
```

rather than inventing a denominator.

The target timestamp is converted using:

```python
isoformat()
```

so it becomes JSON-friendly text.

---

## 13.10 Writing `predictions.json`

The final output file contains top-level metadata such as:

```text
generated_at
forecast_origin
model_trained_through
locations
```

The complete prediction path is:

```text
saved model file
      ↓
load_bundle()
      ↓
latest hourly panel
      ↓
forecast()
      ↓
prediction dataframe
      ↓
capacity lookup
      ↓
to_website_json()
      ↓
json.dump()
      ↓
predictions.json
```

### Key Takeaway

> **Live prediction creates future rows with unknown occupancy, reconstructs leakage-safe features from the latest real origin, applies the saved training representation, and converts the resulting counts into a frontend-ready JSON artifact.**

---

# 14. Training/Prediction Consistency

Training and live prediction must interpret model inputs in exactly the same way.

GymCast accomplishes this by reusing shared functions and storing the training representation in the model bundle.

---

## 14.1 Shared Feature Construction

Training:

```text
historical panel
      ↓
build_supervised_frame()
      ↓
design_matrix()
      ↓
X
      ↓
model.fit(X, y)
```

Prediction:

```text
extended panel
      ↓
build_supervised_frame()
      ↓
design_matrix()
      ↓
X
      ↓
model.predict(X)
```

This avoids separate training and prediction feature builders drifting apart.

For example, GymCast does not want:

```text
training:
rolling_3h_at_origin means one thing

prediction:
rolling_3h_at_origin means something slightly different
```

Using the same builder reduces that risk.

---

## 14.2 Frozen Categorical Vocabulary

Training records categorical values for columns such as:

```text
location_name
semester_phase
```

Suppose training saw:

```text
Marino
SquashBusters
Cabot
```

A later prediction batch might contain only:

```text
Marino
Marino
Marino
```

Prediction should not redefine the category structure based only on that batch.

Instead, GymCast reapplies the training-time vocabulary.

For the fallback one-hot representation, training might define:

```text
location_Marino
location_SquashBusters
location_Cabot
```

A Marino row remains:

```text
location_Marino          = 1
location_SquashBusters   = 0
location_Cabot           = 0
```

rather than changing the model input shape.

For LightGBM, the exact encoding differs, but the same rule applies:

> **A category must retain the same meaning during prediction that it had during training.**

Previously unseen categories become missing rather than silently redefining
the categorical vocabulary. LightGBM receives that missing category; the sklearn
one-hot representation encodes it as zeros across that field's indicators.

---

## 14.3 The Saved Representation Contract

The bundle records:

```text
feature_cols
category_levels
used_lgb
```

These answer:

```text
feature_cols
→ which inputs should the model receive?

category_levels
→ what do categorical values mean?

used_lgb
→ which design-matrix representation should be used?
```

Prediction therefore does not independently rediscover the model representation.

Conceptually:

```text
training defines representation
        ↓
bundle records representation
        ↓
prediction obeys representation
```

---

## 14.4 Compatibility Guard

`load_bundle()` rejects older bundles that lack:

```text
category_levels
```

Rather than guessing:

```text
old incompatible model
      ↓
fail loudly
      ↓
retrain model
```

This turns train/prediction consistency into an enforced requirement rather than an assumption.

### Key Takeaway

> **The model must receive the same kinds of information, with the same meanings and representation, during prediction that it received during training.**

---

# 15. Serving Predictions

## `serve.py`

`serve.py` is the interface between the completed forecasting pipeline and a frontend.

It uses Flask to expose saved predictions over HTTP.

Importantly:

> **The model does not run every time the frontend requests predictions.**

Instead:

```text
predict.py
    ↓
predictions.json
    ↓
serve.py
    ↓
frontend
```

A dedicated Flask/HTTP walkthrough has not been written; this section covers
the implemented API behavior.

---

## 15.1 Reading Predictions

GymCast exposes:

```text
GET /api/predictions
```

The endpoint reads:

```python
with open(PREDICTIONS_FILE) as f:
    return jsonify(json.load(f))
```

Conceptually:

```text
predictions.json
      ↓
json.load()
      ↓
Python data
      ↓
jsonify()
      ↓
HTTP JSON response
      ↓
frontend
```

The forecasting model has already run before the request occurs.

The API simply exposes the latest saved result.

---

## 15.2 Refreshing Predictions

GymCast also exposes:

```text
POST /api/refresh
```

The refresh path runs:

```text
features.py
    ↓
predict.py
    ↓
predictions.json
```

using `subprocess.run()`.

Refresh does **not** run:

```text
collector.py
train.py
```

`collector.py` is expected to be collecting observations separately.

`train.py` changes the learned model and is intended to run less frequently.

Refresh instead takes already-collected observations, rebuilds the current feature panel, and generates fresh forecasts with the existing model.

---

## 15.3 Subprocess Details

`sys.executable` ensures the refresh scripts use the same Python interpreter/environment as the Flask process.

```text
Flask environment
      ↓
same Python executable
      ↓
features.py / predict.py
```

`check=True` ensures that if a refresh step fails, Python raises an error instead of silently claiming success.

`cwd=BASE_DIR` makes the subprocess execute with the source directory as its working directory so relative paths resolve from the expected location.

---

## 15.4 Training vs. Refreshing

A prediction refresh updates the inputs and reruns inference:

```text
new observations
      ↓
features.py
      ↓
predict.py
      ↓
new predictions
```

Retraining changes the learned model:

```text
historical data
      ↓
train.py
      ↓
updated model bundle
```

So:

```text
refresh
→ update inputs and predictions

retraining
→ update learned model parameters
```

Training is generally more computationally expensive and does not need to occur every time predictions are refreshed.

---

## 15.5 CORS

```python
CORS(app)
```

allows browser-based frontend JavaScript hosted from another origin to request GymCast's API.

The pipeline-level idea is simply:

```text
frontend origin
may differ from
API origin
```

and CORS permits the browser request.

The browser-security mechanics are outside the scope of this pipeline guide.

### Key Takeaway

> **`serve.py` is a thin API layer: it serves the latest saved forecast and can trigger feature/prediction regeneration without retraining the model.**

---

# 16. End-to-End Mental Model

The complete GymCast architecture is easier to understand as a shared data pipeline followed by three branches.

## Shared Data Preparation

```text
GoBoard API
    ↓
collector.py
    ↓
~5-minute occupancy observations
    ↓
bucket_to_hour()
    ↓
hourly summaries
    ↓
regularize_hourly()
    ↓
gap-free hourly timeline
    ↓
time / calendar / campus context
    ↓
hourly panel
```

Then:

```text
                         hourly panel
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
      TRAINING            EVALUATION          LIVE PREDICTION
```

## Training Path

```text
hourly panel
    ↓
load_supervised()
    ↓
build_supervised_frame()
    ↓
labelled examples
    ↓
design_matrix()
    ↓
X + y
    ↓
fit_model()
    ↓
trained bundle
    ↓
save_bundle()
    ↓
saved model file
```

## Evaluation Path

```text
hourly panel
    ↓
load_supervised()
    ↓
walk-forward folds
    ↓
for each fold:
    ├─ fit fresh GymCast model
    └─ run baselines
          ↓
common valid rows
          ↓
MAE / RMSE / bias
          ↓
per-horizon MAE
          ↓
aggregate report
```

Evaluation tells us whether the modeling approach actually adds value.

It does not produce the production model used by the live forecasting path.

## Live Prediction Path

```text
latest hourly panel
        +
saved model file
        ↓
load_bundle()
        ↓
latest_origins()
        ↓
extend_panel()
        ↓
future target rows
        ↓
build_supervised_frame()
        ↓
keep rows anchored at real current origin
        ↓
design_matrix()
        ↓
model.predict()
        ↓
clip negative counts
        ↓
to_website_json()
        ↓
predictions.json
        ↓
serve.py
        ↓
frontend
```

## Shared Functions

Two particularly important functions are reused across major parts of the system:

```text
build_supervised_frame()
design_matrix()
```

This helps keep historical training and live prediction logically consistent.

## The Three Questions to Remember

```text
Training
→ How does GymCast learn a forecasting function?

Evaluation
→ Does that forecasting function actually beat simpler alternatives?

Prediction
→ How does GymCast use the learned function on the latest real data?
```

And for any individual model feature:

> **Could GymCast actually know this value at the forecast origin?**

If the answer is no, that feature does not belong in that forecasting example.

---

# Final Mental Model

I should be able to explain the project as:

```text
collect observations
→ organize them into reliable hourly history
→ construct forecasting examples with information available at each origin
    ├─ train and save a model with its input representation
    ├─ evaluate fresh fold models against simple baselines
    └─ use the saved model with the latest observations
        → predict future occupancy
        → format forecasts as JSON
        → expose them through an API
```

The goal is not merely to know that GymCast works.

The goal is to understand **why each stage exists, what information crosses each boundary, and how the system avoids using information that would not have existed when a real forecast was made.**
## Facility availability after inference

The prediction output now gains schedule metadata from `availability.py` after
`forecast()` has produced its occupancy estimates. The seeded weekly schedule
is the baseline, with no date-specific exceptions currently recorded. Absence
of an override does not establish that a date is exception-free.

See [facility availability](facility_availability.md) for the file schemas,
partial-hour classification, current GoBoard conflict rules, and JSON fields.
The frontend consumes these fields to label every forecast hour and restrict
quietest-hour comparisons to fully scheduled-open hours with effective open
status. Live evidence is checked for expiry, and current-state snapshots are
displayed with their evaluation timestamps.
