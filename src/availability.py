"""Scheduled availability and short-lived GoBoard evidence; never model inputs.

Schedules use local wall-clock minutes. Forecast windows are one elapsed hour,
so offset-aware timestamps distinguish both occurrences of a fall-back hour.
"""

import json
from pathlib import Path

import pandas as pd

TIMEZONE = "America/New_York"
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
LIVE_MAX_AGE_MINUTES = 15


def local_timestamp(value) -> pd.Timestamp:
    """Interpret legacy naive timestamps as New York time, rejecting DST ambiguity."""
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("timestamp is missing")
    if stamp.tzinfo is None:
        return stamp.tz_localize(TIMEZONE, ambiguous="raise", nonexistent="raise")
    return stamp.tz_convert(TIMEZONE)


def safe_timestamp(value) -> pd.Timestamp | None:
    try:
        return local_timestamp(value)
    except (ValueError, TypeError, KeyError):
        return None
    except Exception as exc:
        # pandas timezone backends use distinct exceptions for ambiguous/missing hours.
        if type(exc).__name__ in ("AmbiguousTimeError", "NonExistentTimeError"):
            return None
        raise


def timestamp_text(value) -> str:
    stamp = safe_timestamp(value)
    return stamp.isoformat() if stamp is not None else pd.Timestamp(value).isoformat()


def minute_of_day(value: str, allow_end: bool = False) -> int:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("times must use HH:MM")
    try:
        hour, minute = map(int, value.split(":"))
    except ValueError:
        raise ValueError("times must use HH:MM") from None
    if allow_end and value == "24:00":
        return 1440
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid time {value!r}")
    return hour * 60 + minute


def validate_intervals(intervals: list, overrides: bool = False) -> None:
    if not isinstance(intervals, list):
        raise ValueError("intervals must be an array")
    spans = []
    for interval in intervals:
        if not isinstance(interval, dict):
            raise ValueError("each interval must be an object with start and end")
        start = minute_of_day(interval.get("start"))
        end = minute_of_day(interval.get("end"), allow_end=True)
        if start >= end:
            raise ValueError("start must precede end; split overnight hours at midnight")
        if overrides and interval.get("status") not in ("open", "closed", "unknown"):
            raise ValueError("override status must be open, closed, or unknown")
        spans.append((start, end))
    spans.sort()
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        raise ValueError("intervals must not overlap")


def date_override(definition) -> tuple[str, list]:
    """Legacy interval arrays retain overlay semantics; objects require an explicit mode."""
    if isinstance(definition, list):
        return "overlay", definition
    if not isinstance(definition, dict) or definition.get("mode") not in ("overlay", "replace"):
        raise ValueError("date override must specify mode 'overlay' or 'replace'")
    intervals = definition.get("intervals")
    if not isinstance(intervals, list):
        raise ValueError("date override intervals must be an array")
    return definition["mode"], intervals


def validate_config(hours: dict, overrides: dict) -> None:
    for config in (hours, overrides):
        if not isinstance(config, dict) or config.get("schema_version") != 1:
            raise ValueError("schema_version must be 1")
        if config.get("timezone") != TIMEZONE:
            raise ValueError(f"timezone must be {TIMEZONE}")
    facilities = hours.get("facilities")
    locations = hours.get("locations")
    if not isinstance(facilities, dict) or not isinstance(locations, dict):
        raise ValueError("hours must contain facilities and locations objects")
    for location, facility in locations.items():
        if not isinstance(facility, str) or facility not in facilities:
            raise ValueError(f"location {location!r} maps to an unknown facility")
    for facility, definition in facilities.items():
        if not isinstance(definition, dict) or not isinstance(definition.get("weekly"), dict):
            raise ValueError(f"facility {facility!r} needs a weekly object")
        for day, intervals in definition["weekly"].items():
            if day not in DAYS:
                raise ValueError(f"unknown weekday {day!r}")
            if intervals is not None:
                validate_intervals(intervals)
    exceptions = overrides.get("facilities")
    if not isinstance(exceptions, dict):
        raise ValueError("overrides must contain a facilities object")
    for facility, dates in exceptions.items():
        if facility not in facilities or not isinstance(dates, dict):
            raise ValueError(f"invalid override facility {facility!r}")
        for date, definition in dates.items():
            try:
                parsed = pd.Timestamp(date)
                valid = parsed.strftime("%Y-%m-%d") == date
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise ValueError(f"override date {date!r} must use YYYY-MM-DD")
            _, intervals = date_override(definition)
            validate_intervals(intervals, overrides=True)


def normalize_hours(hours: dict) -> dict:
    """Accept the maintained weekly_hours/open/close schema without changing its hours."""
    if not isinstance(hours, dict):
        raise ValueError("hours must be an object")
    if "location_facility_map" not in hours:
        return hours
    facilities = {}
    for key, definition in hours.get("facilities", {}).items():
        weekly = {}
        for day, intervals in definition.get("weekly_hours", {}).items():
            if isinstance(intervals, dict):
                intervals = [intervals]
            weekly[day] = (None if intervals is None else
                           [{"start": item["open"], "end": item["close"]}
                            for item in intervals])
        facilities[key] = {**definition, "weekly": weekly}
    return {**hours, "schema_version": hours.get("schema_version", 1),
            "facilities": facilities, "locations": hours["location_facility_map"]}


def load_config(hours_path: str | None, overrides_path: str | None) -> tuple[dict, dict]:
    empty = {"schema_version": 1, "timezone": TIMEZONE, "facilities": {}}
    configs = []
    for path, default in ((hours_path, {**empty, "locations": {}}), (overrides_path, empty)):
        if not path or not Path(path).exists():
            configs.append(default)
            continue
        try:
            configs.append(json.loads(Path(path).read_text()))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read availability file {path}: {exc}") from None
    try:
        configs[0] = normalize_hours(configs[0])
        validate_config(*configs)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f"Fix {hours_path} / {overrides_path}: {exc}") from None
    return tuple(configs)


def status_at_minute(stamp: pd.Timestamp, facility: str, hours: dict,
                     overrides: dict) -> tuple[str, str]:
    minute = stamp.hour * 60 + stamp.minute
    dates = overrides.get("facilities", {}).get(facility, {})
    date = stamp.strftime("%Y-%m-%d")
    mode, intervals = date_override(dates[date]) if date in dates else ("overlay", [])
    for interval in intervals:
        if minute_of_day(interval["start"]) <= minute < minute_of_day(interval["end"], True):
            return interval["status"], "schedule_override"
    if mode == "replace":
        # A replacement defines the whole local date, including its uncovered minutes.
        return "closed", "schedule_override"
    weekly = hours.get("facilities", {}).get(facility, {}).get("weekly", {})
    intervals = weekly.get(DAYS[stamp.weekday()])
    if intervals is None:
        return "unknown", "unknown"
    for interval in intervals:
        if minute_of_day(interval["start"]) <= minute < minute_of_day(interval["end"], True):
            return "open", "published_schedule"
    return "closed", "published_schedule"


def scheduled_availability(location: str, timestamp, hours: dict, overrides: dict) -> dict:
    facility = hours.get("locations", {}).get(location)
    result = {"facility_id": facility, "scheduled_status": "unknown",
              "schedule_source": "unknown", "scheduled_open_minutes": None,
              "override_applied": False}
    start = safe_timestamp(timestamp)
    if start is None or not facility:
        return result
    # Hourly targets start on a minute boundary. Iterate elapsed minutes to handle DST.
    states = [status_at_minute(start + pd.Timedelta(minutes=i), facility, hours, overrides)
              for i in range(60)]
    statuses = {status for status, _ in states}
    status = ("unknown" if "unknown" in statuses else
              next(iter(statuses)) if len(statuses) == 1 else "partial")
    sources = {source for _, source in states}
    source = ("schedule_override" if "schedule_override" in sources else
              "unknown" if "unknown" in sources else "published_schedule")
    return {**result, "scheduled_status": status, "schedule_source": source,
            "override_applied": "schedule_override" in sources,
            "scheduled_open_minutes": None if "unknown" in statuses else
            sum(state == "open" for state, _ in states)}


def read_live_observations(path: str | None) -> dict:
    """Use the latest raw poll per location, never an hourly max of is_open."""
    if not path or not Path(path).exists():
        return {}
    try:
        frame = pd.read_csv(path, usecols=["timestamp", "location_name", "is_open"],
                            dtype=str)
    except (ValueError, pd.errors.EmptyDataError):
        return {}
    latest = {}
    for row in frame.itertuples(index=False):
        # Raw naive polls are UTC, unlike legacy naive forecast/panel timestamps.
        raw_stamp = pd.to_datetime(row.timestamp, utc=True, errors="coerce")
        stamp = None if pd.isna(raw_stamp) else raw_stamp.tz_convert(TIMEZONE)
        if stamp is None:
            continue
        previous = latest.get(row.location_name)
        if previous is None or stamp >= previous["timestamp"]:
            flag = str(row.is_open).lower()
            status = {"true": "open", "1": "open", "false": "closed", "0": "closed"}.get(
                flag, "unknown"
            )
            latest[row.location_name] = {"timestamp": stamp, "status": status}
    return latest


def current_availability(location: str, hours: dict, overrides: dict,
                         observations: dict, now) -> dict:
    """Report the current minute's effective status alongside the containing hour's schedule."""
    now = local_timestamp(now)
    hour = now.tz_convert("UTC").floor("h").tz_convert(TIMEZONE)
    scheduled = scheduled_availability(location, hour, hours, overrides)
    facility = scheduled["facility_id"]
    schedule_now, source_now = (status_at_minute(now, facility, hours, overrides)
                                if facility else ("unknown", "unknown"))
    result = {**scheduled, "time": hour.isoformat(), "evaluated_at": now.isoformat(),
              "scheduled_status_now": schedule_now, "schedule_source_now": source_now,
              "facility_status": schedule_now,
              "status_source": source_now, "schedule_conflict": False,
              "live_status": "unknown", "live_observed_at": None,
              "live_valid_until": None, "live_is_fresh": False}
    observation = observations.get(location)
    if not observation:
        return result
    stamp = observation["timestamp"]
    expires = min(stamp + pd.Timedelta(minutes=LIVE_MAX_AGE_MINUTES),
                  stamp.tz_convert("UTC").floor("h") + pd.Timedelta(hours=1))
    fresh = stamp <= now < expires and observation["status"] in ("open", "closed")
    result.update(live_status=observation["status"], live_observed_at=stamp.isoformat(),
                  live_valid_until=expires.isoformat(), live_is_fresh=fresh)
    if fresh:
        result.update(facility_status=observation["status"], status_source="goboard_live",
                      schedule_conflict=schedule_now in ("open", "closed")
                      and schedule_now != observation["status"])
    return result


def forecast_availability(location: str, timestamp, hours: dict, overrides: dict,
                          current: dict | None = None) -> dict:
    scheduled = scheduled_availability(location, timestamp, hours, overrides)
    result = {**scheduled, "facility_status": scheduled["scheduled_status"],
              "status_source": scheduled["schedule_source"]}
    target = safe_timestamp(timestamp)
    if current and current["live_is_fresh"] and target is not None:
        if target == local_timestamp(current["time"]):
            result.update(facility_status=current["facility_status"], status_source="goboard_live",
                          live_valid_until=current["live_valid_until"],
                          live_observed_at=current["live_observed_at"],
                          schedule_conflict=current["schedule_conflict"])
    return result
