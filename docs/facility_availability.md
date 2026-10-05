# Facility availability

Availability is metadata attached after occupancy inference. It does not enter
`FEATURE_COLS`, change historical `is_open`, zero closed-hour predictions, or
remove forecast rows. The frontend labels availability and retains all hourly
predictions in the data. Closed rows are hidden by default and can be restored
with Show closed hours; partial and unknown rows stay visible. Local date headings
group the table, and a summary lists up to three quietest fully open hours.
Only fully scheduled-open hours with an effective open status are
eligible for quietest-hour highlighting; partial and unknown hours are excluded.

## Maintained files

`data/facility_hours.json` contains the user-supplied baseline hours. They are
not a verified inventory of Fall 2026 exceptions. Both schedule files are
committed configuration, not generated occupancy data.

The weekly file uses this structure (abbreviated):

```json
{
  "schema_version": 1,
  "timezone": "America/New_York",
  "source": "Northeastern University Recreation",
  "facilities": {
    "marino": {
      "display_name": "Marino Recreation Center",
      "weekly_hours": {
        "monday": {"open": "05:30", "close": "24:00"}
      }
    }
  },
  "location_facility_map": {
    "Marino Center - 3rd Floor Select & Cardio": "marino"
  }
}
```

- Weekday keys are lowercase English names. Every day is interpreted in
  `America/New_York`. `source` and `source_url` record provenance, not verification.
- A day can contain one `{open, close}` interval or an array of those objects
  for multiple sessions. Outside its supplied intervals, the day is closed.
- `[]` means confirmed closed all day; a missing day or `null` means unknown.
- Intervals include their start and exclude their end. `24:00` is accepted
  only as an end. For overnight hours, split at midnight across two weekdays.
- Mappings are exact names, including known spacing variants. Unmapped names
  stay unknown rather than being guessed from a prefix. Multiple locations
  may share a facility. A room-specific schedule can later be a separate
  facility definition and explicit mapping.

`load_config()` normalizes this representation to `locations` and `weekly`
interval lists using `start`/`end` internally. It also accepts that normalized
version-1 representation. The maintained file does not need to be converted.
Missing schedule files give unknown availability. Malformed configuration,
invalid times, overlapping intervals, or invalid facility mappings produce
an actionable error rather than silently guessing.

## Date-specific overrides

`data/facility_overrides.json` currently contains no exceptions:

```json
{
  "schema_version": 1,
  "timezone": "America/New_York",
  "facilities": {}
}
```

To add a verified exception, place it under the facility ID and local date.
The following is **schema illustration only**, not an actual closure:

```json
{
  "schema_version": 1,
  "timezone": "America/New_York",
  "facilities": {
    "marino": {
      "2026-10-05": {
        "mode": "overlay",
        "intervals": [
          {"start": "15:15", "end": "16:00", "status": "closed"}
        ]
      }
    }
  }
}
```

Every date object requires a `mode` and an `intervals` array:

- **`overlay`:** listed intervals override the weekly schedule. Uncovered time
  falls back to the weekly schedule. An empty array changes nothing.
- **`replace`:** the override defines the entire local date. Uncovered time is
  closed, even if the weekly schedule says open or unknown. An empty array
  closes the entire date. Explicit `unknown` intervals can mark uncertain times.

For example, a hypothetical replacement with only 10:30–14:00 open is:

```json
{
  "mode": "replace",
  "intervals": [
    {"start": "10:30", "end": "14:00", "status": "open"}
  ]
}
```

Place that object under the facility/date key. Unlike an overlay, it closes
weekly opening hours outside 10:30–14:00 without requiring separate closure
intervals. Replacement defaults are attributed to `schedule_override` and set
`override_applied: true`, including hours outside the listed intervals.

Override interval statuses are `open`, `closed`, or `unknown`; `partial` is
calculated from the resulting hour, not entered manually. Intervals cannot
overlap. Missing/invalid object modes and missing/non-array intervals are
rejected. Overnight exceptions must be split across their local dates; a
replacement affects only its specified date.

For compatibility, the earlier bare interval-array format is still accepted
and always means `overlay`. Prefer explicit objects for new entries.

No date entry, or an empty overlay, means **no known override**. It never means
that exceptions were checked and none exist. An empty replacement is different:
it explicitly closes that date. Add only verified exceptions; do not infer
holidays, breaks, maintenance, or events from the calendar.

## Hour classification

`scheduled_availability(location, timestamp, hours, overrides)` examines the
60 elapsed minutes starting at the forecast timestamp:

| Result | Meaning |
|---|---|
| `open` | All 60 minutes are scheduled open. |
| `closed` | All 60 minutes are scheduled closed. |
| `partial` | Some minutes are open and some closed, with no unknown coverage. |
| `unknown` | Any part has unknown coverage, or the location/timestamp is unresolved. |

Intervals have minute precision. The hourly pipeline emits minute-aligned
starts. `scheduled_open_minutes` is 0–60 when coverage is known, otherwise null.
`override_applied` is true when an override affects any minute. An affected
hour uses `schedule_source: "schedule_override"`, even when part of the hour
comes from the weekly schedule; otherwise the source is `published_schedule`
or `unknown`.

Aware timestamps are converted to New York time. Legacy naive timestamps are
interpreted as New York local time. Ambiguous fall-back or nonexistent
spring-forward naive timestamps yield unknown availability rather than choosing
an offset silently. Explicit offsets distinguish repeated hours. This does not
reinterpret legacy naive derived panels as UTC. Rebuild those panels from raw
history after the timezone fix. Raw collector timestamps have a different
contract: legacy naive polls mean UTC, and new polls include explicit offsets.
Both `features.load_raw()` and the raw live-evidence reader convert them to New
York time. Schedule timestamps continue to represent local facility time.

## Prediction JSON

Each original prediction retains its occupancy fields and gains:

```json
{
  "time": "2026-10-05T15:00:00-04:00",
  "horizon_hours": 4,
  "predicted_count": 62.4,
  "predicted_percent": 69.3,
  "facility_id": "marino",
  "scheduled_status": "open",
  "schedule_source": "published_schedule",
  "scheduled_open_minutes": 60,
  "override_applied": false,
  "facility_status": "open",
  "status_source": "published_schedule"
}
```

`scheduled_status` always classifies the entire forecast hour. In forecast rows,
`facility_status` normally matches it; only fresh live evidence for the current
hour can override it. The separate current-state object uses minute-level
status as described below.
`time`, `forecast_origin`, and `generated_at` now carry offsets where resolvable.
Unresolvable legacy DST timestamps retain their original form and unknown status.
Model timestamps and features are not converted before inference.

Top-level metadata includes `availability_timezone`,
`unrecorded_exceptions_possible: true`, and `current_availability`, keyed by
forecast location name. Counts and percentages remain independent of availability.

## Current GoBoard evidence

Prediction reads the latest raw CSV poll for each location, using the existing
`is_open` flag collected as the inverse of GoBoard `IsClosed`. It does not call
the API or use the hourly panel's maximum open flag. A later unknown flag
supersedes an earlier known flag.

Live evidence is valid only from its observation time until the earlier of:

- 15 minutes after observation;
- the end of that observation's hour.

Future-dated, stale, or unknown readings cannot override a schedule. Evidence
is scoped to the exact reported location: one closed room does not imply all
rooms sharing the facility schedule are closed.

`current_availability[location]` keeps both time scopes explicit:

| Field | Scope |
|---|---|
| `time` | Start of the containing forecast hour. |
| `evaluated_at` | Instant at which the current status was evaluated. |
| `scheduled_status`, `schedule_source`, `scheduled_open_minutes`, `override_applied` | Entire containing hour. |
| `scheduled_status_now`, `schedule_source_now` | Schedule at the current minute, including overrides. |
| `facility_status`, `status_source` | Current-minute schedule, overridden only by fresh live evidence. |

For a 05:30 opening, `scheduled_status_now` is closed at 05:15 and open at
05:45, while `scheduled_status` remains partial for the 05:00–06:00 bucket.
Minute-level schedule status is open, closed, or unknown, never partial.
Even the source can differ: an override later in the hour affects the hourly
source without becoming the source for the current minute.

The object also includes `live_status`, `live_observed_at`, `live_valid_until`,
`live_is_fresh`, and `schedule_conflict`. Conflict compares the live reading
with `scheduled_status_now`, including verified overrides. A fresh closed reading takes precedence
for the current hour even when it is scheduled open; later hours retain their
scheduled status. Fresh open readings likewise remain current-state evidence.

If the current hour occurs in the forecast timeline, it receives the live
status plus observation/expiry/conflict metadata. Otherwise the current-state
object exposes the evidence independently of the forecast horizon.

These are snapshots at JSON generation time. The frontend checks
`live_valid_until` against the clock and shows live conflicts separately.
It rechecks cached data every 30 seconds without new API requests. For forecast
rows, expired live evidence falls back to hourly `scheduled_status`. The separate
current-status line follows the backend snapshot's `live_is_fresh` flag: true
shows `live_status` and the GoBoard observation time, with any schedule conflict;
false shows the timestamped `scheduled_status_now` and no fresh live confirmation.
A missing freshness flag shows current status unavailable. The current-status
line does not independently recalculate freshness from `live_valid_until`; a new
backend snapshot is needed to update that flag. Reading the saved JSON does not
refresh GoBoard evidence. Only scheduled-open hours should participate in
quietest-hour comparisons, excluding a currently live-closed hour as well.

## Running and testing

From `src/`, defaults attach the maintained configuration and raw poll evidence:

```bash
../venv/bin/python predict.py
```

Optional paths are `--facility-hours`, `--facility-overrides`, and
`--live-observations`. Pass `--live-observations ''` to disable current evidence.
`POST /api/refresh` inherits these default paths when it runs `predict.py`.
No model retraining is required to change schedule configuration.

From the repository root:

```bash
venv/bin/python tests/test_pipeline.py
venv/bin/python tests/test_availability.py
```

The availability suite covers schedule boundaries, shared mappings, unknown
coverage, overrides, midnight, DST, latest raw polls, live expiry/conflicts,
JSON preservation, seeded hours, and prediction-output integration.
