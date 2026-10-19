"""
serve.py

Minimal API to serve predictions.json and the walk-forward evaluation export
to the frontend, plus a /refresh endpoint to regenerate predictions on demand.
In production the gymcast-refresh systemd timer runs the pipeline directly and
Nginx blocks /api/refresh. Missing or unreadable artifacts return a JSON error,
not an HTML 500 page.

Usage (run from src/):
    python serve.py
    # GET  http://localhost:5000/api/predictions
    # POST http://localhost:5000/api/refresh
"""

import json
import os
import subprocess
import sys

from flask import Flask, jsonify, request
from flask_cors import CORS

import paths

app = Flask(__name__)
CORS(app)  # allow your website's frontend JS to fetch this cross-origin

BASE_DIR = os.path.dirname(__file__)
EVALUATION_FILE = str(paths.EVALUATION_PREDICTIONS)
PREDICTIONS_FILE = str(paths.PREDICTIONS)


# Parsing the full evaluation export costs ~170 MB per request (36 MB of JSON),
# which a 1 GB droplet running two workers cannot absorb under concurrent page
# loads. Each worker keeps only the rows for horizons actually requested, keyed
# to the file version, so a new export from evaluate.py is picked up.
_history_cache: dict = {"version": None, "horizons": {}}


def _file_version(path: str) -> tuple:
    stat = os.stat(path)
    return (path, stat.st_mtime_ns, stat.st_size)


def _history_for_horizon(horizon: int) -> dict:
    version = _file_version(EVALUATION_FILE)
    if _history_cache["version"] != version:
        _history_cache.update(version=version, horizons={})
    cached = _history_cache["horizons"]
    if horizon not in cached:
        with open(EVALUATION_FILE) as f:
            history = json.load(f)
        history["rows"] = [row for row in history["rows"] if row["horizon_hours"] == horizon]
        cached[horizon] = history
    return cached[horizon]


def unavailable(message: str, status: int = 503):
    return jsonify({"error": message}), status


@app.route("/api/predictions", methods=["GET"])
def get_predictions():
    try:
        with open(PREDICTIONS_FILE) as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return unavailable("Forecast unavailable: predictions.json has not been generated yet.")
    except ValueError:
        return unavailable("Forecast unavailable: predictions.json could not be parsed.")


@app.route("/api/evaluation-history", methods=["GET"])
def get_evaluation_history():
    horizon = request.args.get("horizon")
    if horizon is not None:
        try:
            horizon = int(horizon)
            if horizon < 1:
                raise ValueError
        except ValueError:
            return jsonify({"error": "horizon must be a positive integer"}), 400
    try:
        if horizon is not None:
            return jsonify(_history_for_horizon(horizon))
        # Unfiltered requests are not used by the frontend; read without caching.
        with open(EVALUATION_FILE) as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return unavailable("Evaluation history unavailable. Run evaluate.py to export it.", 404)
    except ValueError:
        return unavailable("Evaluation history could not be parsed. Re-run evaluate.py.")


@app.route("/api/refresh", methods=["POST"])
def refresh():
    # Re-run the pipeline: features -> predict.
    # (Retrain less frequently - e.g. nightly - since training is heavier than inference.)
    # sys.executable, not "python": inside a venv (or on a machine where only
    # python3 is on PATH) a bare "python" either misses or resolves to a
    # different interpreter without the project's dependencies.
    for step in ("features.py", "predict.py"):
        subprocess.run([sys.executable, os.path.join(BASE_DIR, step)], check=True, cwd=BASE_DIR)
    return jsonify({"status": "refreshed"})


if __name__ == "__main__":
    app.run(port=5000)
