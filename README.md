# GymCast

Predicting gym occupancy at Northeastern's Marino Center (and other campus
fitness locations) using historical occupancy data, so you can check how
busy a location is *likely* to be before you go — not just how busy it is
right now.

## How it works

```
collector.py  →  marino_counts.csv  →  features.py  →  features.csv
                                                              ↓
                                                          train.py → model.pkl
                                                              ↓
                                                          predict.py → predictions.json
                                                              ↓
                                                          serve.py (API) → website
```

1. **`collector.py`** polls the [GoBoard](https://goboardapi.azurewebsites.net) public
   facility-count API every 5 minutes and appends raw counts to `marino_counts.csv`.
2. **`features.py`** buckets the raw 5-minute polls into hourly records per
   location and builds model-ready features:
   - cyclical time encodings (hour, weekday, weekend flag)
   - lag features (count 1hr / 24hr / 168hr ago, rolling 3-hour average)
   - campus-wide total occupancy at that hour
   - optional academic calendar context (regular week / dead week / finals / break)
3. **`train.py`** trains a LightGBM regressor (falls back to scikit-learn's
   `GradientBoostingRegressor` if LightGBM isn't installed) to predict
   occupancy count, evaluated against a naive baseline (same hour last week)
   so it's clear whether the model is actually adding value.
4. **`predict.py`** generates a rolling forecast (default: next 24 hours) per
   location and exports it as `predictions.json`.
5. **`serve.py`** is a small Flask API (`GET /api/predictions`,
   `POST /api/refresh`) that serves the latest predictions to a frontend.

## Data source

Occupancy data comes from the public GoBoard API used by Northeastern's
Marino Recreation Center and SquashBusters facilities. This project only
reads publicly exposed count data; it does not access any private or
authenticated endpoints.

## Setup

```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install lightgbm pandas scikit-learn flask flask-cors requests
```

## Running the pipeline

```bash
# 1. Start the collector (run continuously, e.g. via cron/systemd/screen)
python collector.py

# 2. Once you have a few weeks of data, build features
python features.py --input marino_counts.csv --output features.csv

# 3. Train a model
python train.py --input features.csv --model-out model.pkl

# 4. Generate predictions
python predict.py --model model.pkl --features features.csv --output predictions.json

# 5. Serve predictions to a frontend
python serve.py
```

## Notes / known limitations

- The model needs at least ~2-3 weeks of continuous polling before the
  "same time last week" lag feature has enough history to be useful.
- Academic calendar context (finals week, breaks, etc.) is not available
  from the API and must be supplied manually via a `date,semester_phase`
  CSV passed to `--calendar`.
- Gaps in polling (collector downtime, network errors) should ideally be
  logged separately so they aren't misread as "zero occupancy."

## Roadmap

- [ ] Frontend dashboard (donut/line charts per location, current vs. predicted)
- [ ] Academic calendar integration
- [ ] Poll-failure logging in the collector
- [ ] Retraining automation (nightly cron)
- [ ] Weather as an auxiliary feature
