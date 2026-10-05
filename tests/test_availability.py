"""Availability regression tests; run with venv/bin/python tests/test_availability.py."""

import copy
import json
import sys
import tempfile
from unittest.mock import patch
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import availability as a
import predict


def config():
    hours = {
        "schema_version": 1, "timezone": a.TIMEZONE,
        "locations": {"Cardio": "marino", "Weights": "marino"},
        "facilities": {"marino": {"weekly": {
            day: [{"start": "06:30", "end": "22:00"}] for day in a.DAYS
        }}},
    }
    overrides = {"schema_version": 1, "timezone": a.TIMEZONE, "facilities": {}}
    return hours, overrides


def test_schedule_boundaries_mapping_and_unknown():
    hours, overrides = config()
    for time, status, minutes in [("05:00", "closed", 0), ("06:00", "partial", 30),
                                  ("07:00", "open", 60), ("21:00", "open", 60),
                                  ("22:00", "closed", 0)]:
        for location in ("Cardio", "Weights"):
            row = a.scheduled_availability(location, f"2026-10-05 {time}", hours, overrides)
            assert row["scheduled_status"] == status
            assert row["scheduled_open_minutes"] == minutes
            assert row["schedule_source"] == "published_schedule"
    assert a.scheduled_availability("unmapped", "2026-10-05 15:00", hours, overrides)[
        "scheduled_status"] == "unknown"
    hours["facilities"]["marino"]["weekly"]["monday"] = None
    assert a.scheduled_availability("Cardio", "2026-10-05 15:00", hours, overrides)[
        "scheduled_status"] == "unknown"
    hours["facilities"]["marino"]["weekly"]["monday"] = []
    assert a.scheduled_availability("Cardio", "2026-10-05 15:00", hours, overrides)[
        "scheduled_status"] == "closed"


def test_overrides_partial_closure_and_special_opening():
    hours, overrides = config()
    overrides["facilities"] = {"marino": {"2026-10-05": [
        {"start": "05:00", "end": "06:00", "status": "open"},
        {"start": "15:15", "end": "15:45", "status": "closed"},
    ]}}
    row = a.scheduled_availability("Cardio", "2026-10-05 15:00", hours, overrides)
    assert row["scheduled_status"] == "partial" and row["scheduled_open_minutes"] == 30
    assert row["schedule_source"] == "schedule_override"
    assert a.scheduled_availability("Weights", "2026-10-05 05:00", hours, overrides)[
        "scheduled_status"] == "open"
    assert a.scheduled_availability("Cardio", "2026-10-05 16:00", hours, overrides)[
        "schedule_source"] == "published_schedule"
    overrides["facilities"]["marino"]["2026-10-05"] = [
        {"start": "00:00", "end": "24:00", "status": "closed"}]
    assert a.scheduled_availability("Cardio", "2026-10-05 15:00", hours, overrides)[
        "scheduled_status"] == "closed"
    assert a.scheduled_availability("Cardio", "2026-10-06 15:00", hours, overrides)[
        "scheduled_status"] == "open"


def test_unknown_coverage_and_override_precedence():
    hours, overrides = config()
    hours["facilities"]["marino"]["weekly"] = {}
    overrides["facilities"] = {"marino": {"2026-10-05": [
        {"start": "15:00", "end": "15:30", "status": "open"}]}}
    row = a.scheduled_availability("Cardio", "2026-10-05 15:00", hours, overrides)
    assert row["scheduled_status"] == "unknown" and row["scheduled_open_minutes"] is None
    overrides["facilities"]["marino"]["2026-10-05"][0]["end"] = "16:00"
    assert a.scheduled_availability("Cardio", "2026-10-05 15:00", hours, overrides)[
        "scheduled_status"] == "open"


def test_midnight_and_dst_elapsed_hours():
    hours, overrides = config()
    weekly = hours["facilities"]["marino"]["weekly"]
    weekly["monday"] = [{"start": "22:00", "end": "24:00"}]
    weekly["tuesday"] = [{"start": "00:00", "end": "02:00"}]
    assert a.scheduled_availability("Cardio", "2026-10-05 23:00", hours, overrides)[
        "scheduled_status"] == "open"
    assert a.scheduled_availability("Cardio", "2026-10-06 00:00", hours, overrides)[
        "scheduled_status"] == "open"
    weekly["sunday"] = [{"start": "01:30", "end": "02:00"}]
    for offset in ("-04:00", "-05:00"):
        row = a.scheduled_availability("Cardio", f"2026-11-01T01:00:00{offset}", hours, overrides)
        assert row["scheduled_open_minutes"] == 30
    assert a.scheduled_availability("Cardio", "2026-11-01 01:00", hours, overrides)[
        "scheduled_status"] == "unknown"
    assert a.scheduled_availability("Cardio", "2026-03-08 02:00", hours, overrides)[
        "scheduled_status"] == "unknown"
    weekly["sunday"] = [{"start": "00:00", "end": "24:00"}]
    assert a.scheduled_availability("Cardio", "2026-03-08T01:00:00-05:00", hours, overrides)[
        "scheduled_open_minutes"] == 60
    assert a.scheduled_availability("Cardio", "2026-10-05T19:00:00Z", *config())[
        "scheduled_status"] == "open"


def test_live_conflict_only_current_hour_and_location():
    hours, overrides = config()
    observations = {"Cardio": {"timestamp": a.local_timestamp("2026-10-05 15:03"),
                               "status": "closed"}}
    current = a.current_availability("Cardio", hours, overrides, observations, "2026-10-05 15:05")
    assert current["facility_status"] == "closed"
    assert current["scheduled_status"] == "open" and current["schedule_conflict"]
    assert current["status_source"] == "goboard_live"
    same = a.forecast_availability("Cardio", "2026-10-05 15:00", hours, overrides, current)
    assert same["facility_status"] == "closed" and same["scheduled_status"] == "open"
    future = a.forecast_availability("Cardio", "2026-10-05 16:00", hours, overrides, current)
    assert future["facility_status"] == "open" and future["status_source"] == "published_schedule"
    other = a.current_availability("Weights", hours, overrides, observations, "2026-10-05 15:05")
    assert other["status_source"] == "published_schedule"
    for now in ("2026-10-05 15:02", "2026-10-05 15:18", "2026-10-05 16:00"):
        stale = a.current_availability("Cardio", hours, overrides, observations, now)
        assert not stale["live_is_fresh"] and not stale["schedule_conflict"]
        assert stale["status_source"] == "published_schedule"
    observations["Cardio"]["timestamp"] = a.local_timestamp("2026-10-05 15:59")
    assert not a.current_availability("Cardio", hours, overrides, observations,
                                      "2026-10-05 16:00")["live_is_fresh"]


def test_raw_latest_flag_not_hourly_max_or_last_known():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "polls.csv"
        path.write_text("timestamp,location_name,is_open\n"
                        "2026-10-05T15:05:00,Cardio,False\n"
                        "2026-10-05T15:00:00,Cardio,True\n")
        observations = a.read_live_observations(str(path))
        assert observations["Cardio"]["status"] == "closed"
        assert observations["Cardio"]["timestamp"].isoformat() == "2026-10-05T11:05:00-04:00"
        with path.open("a") as stream:
            stream.write("2026-10-05T15:10:00,Cardio,\n")
        assert a.read_live_observations(str(path))["Cardio"]["status"] == "unknown"
        path.write_text("timestamp,location_name\n2026-10-05,Cardio\n")
        assert a.read_live_observations(str(path)) == {}
    assert a.read_live_observations(None) == {}


def test_json_preserves_predictions_and_all_rows():
    hours, overrides = config()
    frame = pd.DataFrame({"location_name": ["Cardio"] * 3,
                          "hour_bucket": pd.to_datetime(["2026-10-05 05:00", "2026-10-05 06:00",
                                                         "2026-10-05 07:00"]),
                          "horizon": [1, 2, 3], "predicted_count": [42.3, 41.2, 99.0],
                          "is_open": [True, False, None]})
    original = frame.copy(deep=True)
    rows = predict.to_website_json(frame, {"Cardio": 100}, hours, overrides)["Cardio"]
    assert len(rows) == 3
    assert [row["facility_status"] for row in rows] == ["closed", "partial", "open"]
    assert [row["predicted_count"] for row in rows] == [42.3, 41.2, 99.0]
    assert [row["predicted_percent"] for row in rows] == [42.3, 41.2, 99.0]
    assert rows[0]["time"].endswith("-04:00")
    pd.testing.assert_frame_equal(frame, original)
    unknown = predict.to_website_json(frame, {})["Cardio"]
    assert all(row["facility_status"] == "unknown" for row in unknown)
    assert all(row["predicted_percent"] is None for row in unknown)


def test_configuration_validation_and_optional_files():
    hours, overrides = config()
    a.validate_config(hours, overrides)
    bad_cases = []
    for intervals in ([{"start": "22:00", "end": "02:00"}],
                      [{"start": "06:00", "end": "25:00"}],
                      [{"start": "06:00", "end": "12:00"},
                       {"start": "11:00", "end": "13:00"}]):
        bad = copy.deepcopy(hours)
        bad["facilities"]["marino"]["weekly"]["monday"] = intervals
        bad_cases.append((bad, overrides))
    bad = copy.deepcopy(overrides)
    bad["facilities"] = {"marino": {"2026-10-05": [
        {"start": "06:00", "end": "12:00", "status": "partial"}]}}
    bad_cases.append((hours, bad))
    bad = copy.deepcopy(hours)
    bad["locations"]["Cardio"] = "typo"
    bad_cases.append((bad, overrides))
    for pair in bad_cases:
        try:
            a.validate_config(*pair)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid availability configuration was accepted")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "hours.json"
        path.write_text(json.dumps(hours))
        assert a.load_config(str(path), None)[0] == hours
        path.write_text("{bad")
        try:
            a.load_config(str(path), None)
        except ValueError as exc:
            assert str(path) in str(exc)
        else:
            raise AssertionError("malformed JSON was accepted")
    assert a.load_config(None, None)[0]["locations"] == {}


def test_seeded_hours_and_empty_overrides():
    root = Path(__file__).resolve().parents[1]
    hours, overrides = a.load_config(str(root / "data/facility_hours.json"),
                                    str(root / "data/facility_overrides.json"))
    cardio = "Marino Center - 3rd Floor Select & Cardio"
    assert a.scheduled_availability(cardio, "2026-10-05 05:00", hours, overrides)[
        "scheduled_open_minutes"] == 30
    assert a.scheduled_availability(cardio, "2026-10-05 23:00", hours, overrides)[
        "scheduled_status"] == "open"
    row = a.scheduled_availability("SquashBusters - 4th Floor", "2026-10-09 21:00",
                                   hours, overrides)
    assert row["scheduled_status"] == "closed"
    assert not row["override_applied"]
    assert overrides["facilities"] == {}


def test_main_attaches_metadata_without_altering_forecast():
    hours, overrides = config()
    frame = pd.DataFrame({"location_name": ["Cardio"],
                          "hour_bucket": [pd.Timestamp("2026-10-05 05:00")],
                          "horizon": [1], "predicted_count": [42.3]})
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "hours.json").write_text(json.dumps(hours))
        (root / "overrides.json").write_text(json.dumps(overrides))
        (root / "panel.csv").write_text(
            "location_name,hour_bucket,count,capacity\nCardio,2026-10-05 04:00,30,100\n")
        with patch.object(predict, "load_bundle", return_value={}), \
                patch.object(predict, "forecast", return_value=frame) as forecast:
            predict.main("unused", str(root / "panel.csv"), 1, str(root / "output.json"),
                         None, str(root / "hours.json"), str(root / "overrides.json"), None)
            assert forecast.call_count == 1
        output = json.loads((root / "output.json").read_text())
        row = output["locations"]["Cardio"][0]
        assert row["predicted_count"] == 42.3 and row["facility_status"] == "closed"
        assert output["current_availability"]["Cardio"]["live_status"] == "unknown"
        assert output["unrecorded_exceptions_possible"] is True


def test_explicit_overlay_and_replacement_schedules():
    hours, overrides = config()
    intervals = [{"start": "10:30", "end": "14:00", "status": "open"}]
    dates = {"2026-10-05": {"mode": "replace", "intervals": intervals}}
    overrides["facilities"] = {"marino": dates}
    a.validate_config(hours, overrides)
    for time, status, minutes in [("07:00", "closed", 0), ("10:00", "partial", 30),
                                  ("11:00", "open", 60), ("14:00", "closed", 0),
                                  ("23:00", "closed", 0)]:
        row = a.scheduled_availability("Cardio", f"2026-10-05 {time}", hours, overrides)
        assert row["scheduled_status"] == status
        assert row["scheduled_open_minutes"] == minutes
        assert row["override_applied"] and row["schedule_source"] == "schedule_override"
    # The next date resumes weekly hours; a replacement never spills across midnight.
    assert a.scheduled_availability("Cardio", "2026-10-06 07:00", hours, overrides)[
        "schedule_source"] == "published_schedule"
    dates["2026-10-05"]["mode"] = "overlay"
    a.validate_config(hours, overrides)
    row = a.scheduled_availability("Cardio", "2026-10-05 07:00", hours, overrides)
    assert row["scheduled_status"] == "open" and not row["override_applied"]
    dates["2026-10-05"]["intervals"] = [
        {"start": "15:15", "end": "15:45", "status": "closed"}]
    row = a.scheduled_availability("Cardio", "2026-10-05 15:00", hours, overrides)
    assert row["scheduled_status"] == "partial" and row["scheduled_open_minutes"] == 30


def test_empty_replacement_unknown_intervals_and_validation():
    hours, overrides = config()
    dates = {"2026-10-05": {"mode": "replace", "intervals": []}}
    overrides["facilities"] = {"marino": dates}
    a.validate_config(hours, overrides)
    row = a.scheduled_availability("Cardio", "2026-10-05 12:00", hours, overrides)
    assert row["scheduled_status"] == "closed" and row["override_applied"]
    dates["2026-10-05"] = {"mode": "overlay", "intervals": []}
    assert a.scheduled_availability("Cardio", "2026-10-05 12:00", hours, overrides)[
        "scheduled_status"] == "open"
    dates["2026-10-05"] = {"mode": "replace", "intervals": [
        {"start": "12:00", "end": "12:30", "status": "unknown"}]}
    row = a.scheduled_availability("Cardio", "2026-10-05 12:00", hours, overrides)
    assert row["scheduled_status"] == "unknown" and row["scheduled_open_minutes"] is None
    assert a.scheduled_availability("Cardio", "2026-10-05 13:00", hours, overrides)[
        "scheduled_status"] == "closed"
    for invalid in ({"intervals": []}, {"mode": "replacement", "intervals": []},
                    {"mode": "replace"}, {"mode": "overlay", "intervals": None},
                    {"mode": "replace", "intervals": [
                        {"start": "10:00", "end": "12:00", "status": "open"},
                        {"start": "11:00", "end": "13:00", "status": "closed"}]},
                    {"mode": "replace", "intervals": [
                        {"start": "10:00", "end": "12:00", "status": "partial"}]}):
        dates["2026-10-05"] = invalid
        try:
            a.validate_config(hours, overrides)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid date override accepted: {invalid}")


def test_current_minute_status_preserves_hourly_classification():
    hours, overrides = config()
    hours["facilities"]["marino"]["weekly"]["monday"] = [
        {"start": "05:30", "end": "22:00"}]
    for time, expected in (("05:15", "closed"), ("05:30", "open"), ("05:45", "open")):
        current = a.current_availability("Cardio", hours, overrides, {}, f"2026-10-05 {time}")
        assert current["scheduled_status_now"] == expected
        assert current["facility_status"] == expected
        assert current["scheduled_status"] == "partial"
        assert current["scheduled_open_minutes"] == 30
        assert current["schedule_source_now"] == current["status_source"] == "published_schedule"
        assert current["evaluated_at"] == a.local_timestamp(f"2026-10-05 {time}").isoformat()
        row = a.forecast_availability("Cardio", "2026-10-05 05:00", hours, overrides, current)
        assert row["scheduled_status"] == row["facility_status"] == "partial"
    unknown = a.current_availability("unmapped", hours, overrides, {}, "2026-10-05 05:45")
    assert unknown["scheduled_status_now"] == unknown["facility_status"] == "unknown"


def test_current_minute_override_source_and_live_conflicts():
    hours, overrides = config()
    overrides["facilities"] = {"marino": {"2026-10-05": {
        "mode": "overlay", "intervals": [
            {"start": "15:30", "end": "16:00", "status": "closed"}]}}}
    for time, expected, source in (("15:15", "open", "published_schedule"),
                                   ("15:45", "closed", "schedule_override")):
        stamp = a.local_timestamp(f"2026-10-05 {time}")
        current = a.current_availability("Cardio", hours, overrides, {}, stamp)
        assert current["scheduled_status"] == "partial"
        assert current["scheduled_status_now"] == current["facility_status"] == expected
        assert current["schedule_source"] == "schedule_override"
        assert current["schedule_source_now"] == current["status_source"] == source
        # A closed reading conflicts only with the open minute, not with the partial bucket.
        observations = {"Cardio": {"timestamp": stamp, "status": "closed"}}
        live = a.current_availability("Cardio", hours, overrides, observations, stamp)
        assert live["facility_status"] == "closed" and live["status_source"] == "goboard_live"
        assert live["scheduled_status_now"] == expected and live["scheduled_status"] == "partial"
        assert live["schedule_conflict"] == (expected == "open")
        future = a.forecast_availability("Cardio", "2026-10-05 16:00", hours, overrides, live)
        assert future["facility_status"] == "open" and future["status_source"] == "published_schedule"
        stale = a.current_availability("Cardio", hours, overrides, observations,
                                       stamp + pd.Timedelta(minutes=15))
        assert not stale["live_is_fresh"]
        assert stale["facility_status"] == stale["scheduled_status_now"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  PASS  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
