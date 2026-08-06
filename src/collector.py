"""
collector.py

Polls the GoBoard public facility-count API every 5 minutes and appends
occupancy data to ../data/marino_counts.csv.

Features:
  - Captures an `is_open` flag per location, if present in the API response
    (checked under a few likely key names since the exact field wasn't
    confirmed from docs - verify against a raw response and adjust
    OPEN_FIELD_CANDIDATES below if needed).
  - Logs failed polls to ../data/poll_errors.csv instead of only printing
    them, so gaps in marino_counts.csv can be distinguished from
    "zero occupancy."
"""

import csv
import os
import time
from datetime import datetime
import requests

URL = "https://goboardapi.azurewebsites.net/api/FacilityCount/GetCountsByAccount?AccountAPIKey=2a2be0d8-df10-4a48-bedd-b3bc0cd628e7"
HEADERS = {"User-Agent": "Mozilla/5.0"}

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)
CSV_FILE = os.path.join(DATA_DIR, "marino_counts.csv")
ERROR_LOG_FILE = os.path.join(DATA_DIR, "poll_errors.csv")

POLL_SECONDS = 300  # 5 minutes

# Verify the actual key name in a raw API response and adjust this list if needed.
OPEN_FIELD_CANDIDATES = ["IsOpen", "LocationOpen", "Open", "Status"]


def fetch_data():
    response = requests.get(URL, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def extract_is_open(loc: dict):
    for key in OPEN_FIELD_CANDIDATES:
        if key in loc:
            value = loc[key]
            if isinstance(value, str):
                return value.strip().lower() in ("open", "true", "1")
            return bool(value)
    return None  # unknown - field not found in response


def append_rows(rows):
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "timestamp",
                "date",
                "hour",
                "weekday",
                "facility_name",
                "location_name",
                "count",
                "capacity",
                "percent",
                "is_open",
            ])
        writer.writerows(rows)


def log_error(message: str):
    file_exists = os.path.exists(ERROR_LOG_FILE)
    with open(ERROR_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "error"])
        writer.writerow([datetime.now().isoformat(timespec="seconds"), message])


if __name__ == "__main__":
    print("Collector started...")

    while True:
        try:
            now_dt = datetime.now()
            now = now_dt.isoformat(timespec="seconds")
            date = now_dt.date().isoformat()
            hour = now_dt.hour
            weekday = now_dt.strftime("%A")

            data = fetch_data()
            rows = []

            for loc in data:
                count = loc["LastCount"]
                capacity = loc["TotalCapacity"]
                percent = (count / capacity * 100) if capacity else 0
                is_open = extract_is_open(loc)

                rows.append([
                    now,
                    date,
                    hour,
                    weekday,
                    loc["FacilityName"],
                    loc["LocationName"],
                    count,
                    capacity,
                    round(percent, 2),
                    is_open,
                ])

            append_rows(rows)
            print(f"Saved {len(rows)} rows at {now}")

        except Exception as e:
            error_msg = str(e)
            print(f"Error at {datetime.now().isoformat(timespec='seconds')}: {error_msg}")
            log_error(error_msg)

        time.sleep(POLL_SECONDS)
