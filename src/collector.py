"""
collector.py

Polls the GoBoard public facility-count API every 5 minutes and appends
occupancy data to ../data/marino_counts.csv.

Features:
  - Captures an `is_open` flag per location, derived from the confirmed
    `IsClosed` field in the GoBoard response (inverted: is_open = not IsClosed).
  - Captures `location_id` (GoBoard's `LocationId`), a stable key that
    won't break if a location is ever renamed.
  - Logs failed polls to ../data/poll_errors.csv instead of only printing
    them, so gaps in marino_counts.csv can be distinguished from
    "zero occupancy."
  - Redacts the API key from every logged error. The key travels in the query
    string, and `requests` puts the full URL into its exception messages.

Importing this module has no side effects; configuration is read in main(), so
the row-building and logging functions can be tested without a key.
"""

import csv
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus

import requests
from dotenv import load_dotenv

import paths

URL = "https://goboardapi.azurewebsites.net/api/FacilityCount/GetCountsByAccount"
HEADERS = {"User-Agent": "Mozilla/5.0"}

CSV_FILE = str(paths.RAW_COUNTS)
ERROR_LOG_FILE = str(paths.POLL_ERRORS)

POLL_SECONDS = 300  # 5 minutes

CSV_HEADER = [
    "timestamp",
    "date",
    "hour",
    "weekday",
    "facility_name",
    "location_name",
    "location_id",
    "count",
    "capacity",
    "percent",
    "is_open",
]

# Catches the key in any URL even if it differs from the configured value.
KEY_IN_URL = re.compile(r"(AccountAPIKey=)[^&\s'\"]+", re.IGNORECASE)


def fetch_data(api_key: str):
    response = requests.get(URL, params={"AccountAPIKey": api_key},
                            headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def extract_is_open(loc: dict):
    """GoBoard returns `IsClosed` (bool); is_open is just the inverse."""
    is_closed = loc.get("IsClosed")
    if is_closed is None:
        return None  # field missing - shouldn't happen, but don't guess
    return not bool(is_closed)


def build_rows(data: list, now_dt: datetime) -> list[list]:
    """One CSV row per location in a GoBoard response, stamped with the poll instant."""
    now = now_dt.isoformat(timespec="seconds")
    date = now_dt.date().isoformat()
    hour = now_dt.hour
    weekday = now_dt.strftime("%A")

    rows = []
    for loc in data:
        count = loc["LastCount"]
        capacity = loc["TotalCapacity"]
        percent = (count / capacity * 100) if capacity else 0
        rows.append([
            now,
            date,
            hour,
            weekday,
            loc["FacilityName"],
            loc["LocationName"],
            loc.get("LocationId"),
            count,
            capacity,
            round(percent, 2),
            extract_is_open(loc),
        ])
    return rows


def append_rows(rows):
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def redact(message: str, secret: str | None) -> str:
    """Remove the API key from a message, in raw and URL-encoded forms."""
    if secret:
        for form in {secret, quote(secret, safe=""), quote_plus(secret)}:
            message = message.replace(form, "<redacted>")
    return KEY_IN_URL.sub(r"\1<redacted>", message)


def log_error(message: str):
    file_exists = os.path.exists(ERROR_LOG_FILE)
    with open(ERROR_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "error"])
        writer.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), message])


def record_failure(exc: Exception, api_key: str | None) -> None:
    """Print and log a failed poll without the API key."""
    message = redact(str(exc), api_key)
    print(f"Error at {datetime.now(timezone.utc).isoformat(timespec='seconds')}: {message}")
    log_error(message)


def poll_once(api_key: str) -> int:
    now_dt = datetime.now(timezone.utc)
    rows = build_rows(fetch_data(api_key), now_dt)
    append_rows(rows)
    print(f"Saved {len(rows)} rows at {now_dt.isoformat(timespec='seconds')}")
    return len(rows)


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("GOBOARD_API_KEY")
    if not api_key:
        sys.exit("collector.py: GOBOARD_API_KEY not set. Add it to your .env file.")
    os.makedirs(paths.DATA_DIR, exist_ok=True)

    print("Collector started...")
    while True:
        try:
            poll_once(api_key)
        except Exception as exc:
            # Any failure (network, HTTP status, malformed response) is one missed
            # poll, not a reason to stop collecting.
            record_failure(exc, api_key)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
