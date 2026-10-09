# Forecast timestamp investigation

The October 9 screenshots with `:03` targets used a temporary browser fixture,
not API predictions. That fixture generated targets as
`new Date(previewNow + (i + 1) * 3600000)`, preserving the wall clock's minutes
and seconds. `web/app.js::formatTimestamp()` correctly displayed those supplied
minutes. No frontend rounding is appropriate.

The replacement `tests/preview_frontend.cjs` starts its synthetic origin at an
hour boundary and checks that every target is aligned. Its generation timestamp
still records the real clock, since generation time need not be on the hour.

## Actual code path

1. `src/collector.py CLI` writes the poll instant with an explicit
   UTC offset; raw observations legitimately have nonzero minutes.
2. `src/features.py::load_raw()` interprets legacy naive timestamps as UTC and
   converts aware instants to America/New_York. `bucket_to_hour()` averages polls
   into hour-start buckets. Aware instants now floor in UTC before conversion
   back to their timezone, avoiding ambiguity during the repeated fall-back hour.
   New York UTC offsets are whole hours, so local boundaries remain correct.
   Naive synthetic inputs retain their existing floor behavior.
3. `src/predict.py::latest_origins()` selects an observed bucket;
   `extend_panel()` adds elapsed hourly steps from that origin. `forecast()`
   reuses `build_supervised_frame()` and keeps targets for the selected origin.
   Saved panels with mixed DST offsets are normalized to one New York-aware
   datetime axis in `regularize_hourly()`; prediction applies it before extension.
4. `to_website_json()` serializes each target bucket using
   `src/availability.py::timestamp_text()`, with its New York offset.
   `src/serve.py::get_predictions()` returns those strings unchanged.
5. `web/app.js::formatTimestamp()` parses the supplied instant and displays it
   in America/New_York, preserving minutes. No model times are derived from the
   generation timestamp or browser clock.

Read-only inspection of local artifacts found all 192 saved forecast targets
and all 11,528 saved panel buckets aligned to the hour. This does not verify
current deployed artifacts.

## Separate DST correction and regression coverage

The previous local-time `.dt.floor("h")` raised a ValueError for offset-bearing
polls at both November 1, 2026 1:03 AM instants. UTC flooring preserves those as
separate 1:00 AM EDT and 1:00 AM EST buckets, rather than inferring which one
was meant. Spring-forward still skips the nonexistent 2 AM hour. This changes
neither elapsed-hour horizons nor forecasting features on ordinary hours.

`tests/test_pipeline.py::test_hour_alignment_survives_dst_and_forecast_serialization`
covers raw loading, aggregation, future target construction across both DST
transitions, saved-panel CSV round trips with mixed offsets, supervised
origins/horizons, serialization, and API string parity.
Frontend checks preserve both DST instants and explicitly verify that a supplied
`:03` timestamp is displayed as `:03`, rather than concealed.

## Artifact and deployment implications

No raw history was modified and no local or production artifacts were regenerated.
The screenshot defect requires no retraining or data regeneration. Existing
hour-aligned artifacts remain valid under this DST bucketing correction.

For artifacts containing fall-back observations that failed to bucket, rebuild
only the hourly panel and predictions using the existing compatible model:

```bash
cd src
../venv/bin/python features.py
../venv/bin/python predict.py
```

This is separate from the earlier legacy UTC interpretation correction documented
in PROJECT.md. Artifacts built under that older interpretation require the full
rebuild, performed deliberately rather than automatically:

```bash
cd src
../venv/bin/python features.py
../venv/bin/python train.py
../venv/bin/python predict.py
../venv/bin/python evaluate.py
```

No merge or deployment was performed. iOS Safari, assistive technology, and
current deployed API integration were not directly tested.
