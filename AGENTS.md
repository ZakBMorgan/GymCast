# AGENTS.md — instructions for coding agents

Read [PROJECT.md](PROJECT.md) first for what this project is and why it is
built the way it is. This file covers how to work in the repo. The
[pipeline walkthrough](docs/pipeline_walkthrough.md) explains the stages with
examples; [README.md](README.md) introduces the product and local usage.

## Setup

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
brew install libomp        # macOS only; LightGBM needs it
```

Use `venv/bin/python` explicitly. A bare `python` is not on PATH on the
maintainer's machine — `python3` or the venv binary.

Without `libomp`, LightGBM imports and then fails to load its shared library.
`train.py` catches that and falls back to sklearn. This is silent apart from
the backend line `train.py` prints, so check it before comparing runs.

## Running

All scripts default to `../data`, `../models`, `../outputs` relative to `src/`,
so run them from `src/` with no flags:

```bash
cd src
../venv/bin/python collector.py     # continuous poller; keep one instance running
../venv/bin/python features.py      # marino_counts.csv -> features.csv (hourly panel)
../venv/bin/python train.py         # features.csv -> model.pkl
../venv/bin/python evaluate.py      # walk-forward accuracy vs baselines
../venv/bin/python predict.py       # model.pkl + features.csv -> predictions.json
../venv/bin/python serve.py         # Flask API on :5000
```

The collector sleeps between polls itself; do not schedule overlapping copies.
Run tests from the repository root. `POST /api/refresh` runs features → predict
with the saved model; it does not collect or retrain.

Useful flags: `evaluate.py --folds N --test-days N --min-train-days N
--max-horizon N --all-hours --report-out PATH`; `train.py --max-horizon N`;
`predict.py --hours-ahead N`. Pass `--calendar ''` to skip the academic calendar.

## Tests

```bash
venv/bin/python tests/test_pipeline.py    # standalone runner, no pytest dependency
venv/bin/python -m pytest tests/ # also works if pytest is installed
```

All 8 must pass before you hand work back. They generate their own synthetic
data — no real CSV needed, and they are the fastest way to verify a change.

Do not add pytest as a required dependency; the standalone runner is
deliberate so the tests run anywhere.

## Important files

| File | Role |
|---|---|
| `src/features.py` | Panel construction **and** `build_supervised_frame()` — the origin/horizon expansion everything else depends on |
| `src/train.py` | Model primitives (`fit_model`, `design_matrix`, `predict_with`, bundle save/load) shared by evaluate and predict |
| `src/evaluate.py` | Walk-forward folds, baselines, metrics, reporting |
| `src/predict.py` | Serving path; reuses `build_supervised_frame()` |
| `src/collector.py` | GoBoard poller |
| `src/serve.py` | Flask API |
| `tests/test_pipeline.py` | Leakage and skew assertions — the real spec |

## Do not change casually

These encode correctness properties that failed silently before. Changing them
without understanding the reasoning reintroduces bugs no error message reports.

1. **Never use occupancy observed after the forecast origin.** For a target
   `t` and horizon `h`, observed inputs `count[t-k]` require `k >= h`. Target
   clock/calendar features are allowed because they are known in advance.
   Prove new occupancy inputs safe via
   `test_no_feature_depends_on_the_target_hour` and serving-parity checks.
2. **`is_open` and `capacity` stay in `PASSTHROUGH_COLS`.** `is_open` describes
   the target hour; promoting it to a feature is a same-hour leak.
3. **`predict.py` must keep calling `build_supervised_frame()`.** Hand-assembling
   serving rows is exactly how train/serve skew returns.
4. **`evaluate.py` must keep refitting per fold and must not load `model.pkl`.**
   Use `split_fold()`: training targets `<= cutoff`; test targets in
   `(cutoff, test_end]` with origins `>= cutoff`. Origin-safe features alone
   cannot justify a model fitted after forecast issue time.
5. **Anything fitted from data gets fitted in-fold** — category levels, the
   profile baseline. Baselines take `train_df` for this reason even when unused.
6. **Keep the common-mask scoring** in `evaluate_fold`. Comparing MAEs computed
   over different row sets is meaningless here.
7. **Keep category levels frozen in the bundle.** LightGBM receives categorical
   columns; sklearn receives fixed one-hot columns. Re-deriving vocabulary at
   prediction time can change category meanings or the input shape.
8. **Keep the LightGBM guard catching `OSError`,** not just `ImportError`.
9. **Keep `regularize_hourly()` before any shift.** Lags are positional; a
   polling gap otherwise slides "same hour last week" off by an hour.

Safe to change freely: hyperparameters in `DEFAULT_PARAMS`, report formatting,
baseline additions, CLI defaults, docs.

## Conventions

- Plain functions, no classes. Each `src/*.py` is a module plus an
  `argparse` CLI under `if __name__ == "__main__"`.
- Modules import each other flat (`from features import ...`), relying on being
  run from `src/`. Tests insert `src/` on `sys.path`. Keep the dependency
  direction `features → train → evaluate/predict`; never import `evaluate` from
  `train` (it would cycle).
- pandas/numpy only. No new dependencies without asking.
- Comments explain *why*, especially where the obvious implementation is wrong.
  Match that density; don't narrate what the code already says.
- Fail with actionable messages, not tracebacks: say what is missing and what
  to do (see `MIN_TRAIN_ROWS` in `train.py`).
- Thin data is the expected early state, not an error. Degrade with an
  explanation.
- Line length ~100. Type hints on new function signatures where natural; the
  codebase is not fully annotated and does not need to be.

## Data realities

- `data/*.csv`, `models/*.pkl`, `outputs/*.json` are gitignored and
  regenerated. `data/academic_calendar.csv` is committed and header-only.
- Local artifacts now contain weeks of September observations, but fresh clones
  have no collected history. **Develop against the synthetic generator in
  `tests/test_pipeline.py::synthetic_panel`**, not the real CSV.
- September evaluation scores predate the origin-cutoff fix; rerun evaluation
  before citing them as current performance.
- Never commit collected occupancy CSVs.
- `features.csv` format changed in August 2026. If you hit an old file,
  `train.py` raises a clear error — re-run `features.py`.

## Updating project context

After a substantial architectural or methodological change, update
[PROJECT.md](PROJECT.md) in the same session — specifically the **Current
State** section (what works / unfinished / recent changes / open questions /
next tasks) and any design decision that no longer holds. Update this file if
commands, conventions, or the do-not-change list changed. Keep
[README.md](README.md) focused on the user benefit, implemented product status,
setup, and usage. Keep detailed teaching examples in the
[pipeline walkthrough](docs/pipeline_walkthrough.md); update affected explanations
when behavior changes.

Keep both documents short enough to read at the start of a session. Prune stale
entries rather than appending — a growing changelog defeats the purpose.

Do not document aspirations as if implemented. If something is untested or
unverified, say so; the next agent cannot tell the difference from the code.
