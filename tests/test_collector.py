"""Collector row building and key redaction; run with venv/bin/python tests/test_collector.py."""

import contextlib
import csv
import io
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Importing must work without a key; it used to raise before any test could run.
with patch.dict(os.environ, {}, clear=True):
    import collector  # noqa: E402

# Characters that change under URL encoding, so encoded leaks are exercised too.
SECRET = "Ab+c/d=Secret123"

RESPONSE = [
    {"FacilityName": "Marino Recreation Center", "LocationName": "Marino Center - Studio A",
     "LocationId": 9531, "LastCount": 3, "TotalCapacity": 33, "IsClosed": False},
    {"FacilityName": "SquashBusters Center", "LocationName": "SquashBusters - 4th Floor",
     "LocationId": 6522, "LastCount": 0, "TotalCapacity": 0, "IsClosed": True},
    {"FacilityName": "Marino Recreation Center", "LocationName": "Marino Center - Gymnasium",
     "LastCount": 12, "TotalCapacity": 80},
]


def test_rows_keep_the_existing_csv_format():
    # 03:49 UTC is still the previous evening in New York; raw date/hour stay UTC.
    rows = collector.build_rows(RESPONSE, datetime(2026, 8, 6, 3, 49, 59, tzinfo=timezone.utc))
    assert rows == [
        ["2026-08-06T03:49:59+00:00", "2026-08-06", 3, "Thursday", "Marino Recreation Center",
         "Marino Center - Studio A", 9531, 3, 33, 9.09, True],
        ["2026-08-06T03:49:59+00:00", "2026-08-06", 3, "Thursday", "SquashBusters Center",
         "SquashBusters - 4th Floor", 6522, 0, 0, 0, False],
        # Missing LocationId and IsClosed stay unknown rather than guessed.
        ["2026-08-06T03:49:59+00:00", "2026-08-06", 3, "Thursday", "Marino Recreation Center",
         "Marino Center - Gymnasium", None, 12, 80, 15.0, None],
    ]


def test_append_writes_header_once():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "counts.csv")
        rows = collector.build_rows(RESPONSE[:1], datetime(2026, 8, 6, tzinfo=timezone.utc))
        with patch.object(collector, "CSV_FILE", path):
            collector.append_rows(rows)
            collector.append_rows(rows)
        with open(path, newline="") as f:
            content = list(csv.reader(f))
        assert content[0] == collector.CSV_HEADER
        assert len(content) == 3 and content[1] == content[2]


def test_redact_covers_raw_encoded_and_unknown_keys():
    message = (f"key={SECRET} quoted=Ab%2Bc%2Fd%3DSecret123 plus=Ab%2Bc%2Fd%3DSecret123 "
               "url=https://x/api?AccountAPIKey=some-other-key&y=1")
    cleaned = collector.redact(message, SECRET)
    assert "Secret123" not in cleaned and "some-other-key" not in cleaned
    assert "AccountAPIKey=<redacted>&y=1" in cleaned


def test_real_request_failure_is_logged_without_the_key():
    """A real requests exception embeds the full URL; nothing logged may contain the key."""
    with tempfile.TemporaryDirectory() as directory:
        error_log = os.path.join(directory, "poll_errors.csv")
        printed = io.StringIO()
        # Port 9 on localhost refuses connections, so this fails fast without a network.
        with patch.object(collector, "URL", "http://127.0.0.1:9/api/FacilityCount"), \
             patch.object(collector, "ERROR_LOG_FILE", error_log), \
             contextlib.redirect_stdout(printed):
            try:
                collector.poll_once(SECRET)
            except Exception as exc:
                assert "Secret123" in str(exc) or "Secret123" in repr(exc.args), \
                    "probe should carry the key, or this test proves nothing"
                collector.record_failure(exc, SECRET)
            else:
                raise AssertionError("expected the request to fail")
        logged = Path(error_log).read_text()
        assert "Secret123" not in logged and "Secret123" not in printed.getvalue()
        assert "AccountAPIKey=<redacted>" in logged
        assert printed.getvalue().startswith("Error at ")


def test_main_without_key_exits_with_instructions():
    with patch.dict(os.environ, {}, clear=True), patch.object(collector, "load_dotenv"):
        try:
            collector.main()
        except SystemExit as exc:
            assert "GOBOARD_API_KEY" in str(exc.code)
        else:
            raise AssertionError("expected main() to exit without a key")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
