"""
serve.py

Minimal API to serve predictions.json to your website's frontend, plus
a /refresh endpoint to regenerate it on demand (call this from a cron
job or your existing collector's poll loop).

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

app = Flask(__name__)
CORS(app)  # allow your website's frontend JS to fetch this cross-origin

BASE_DIR = os.path.dirname(__file__)
EVALUATION_FILE = os.path.join(BASE_DIR, "..", "outputs", "evaluation_predictions.json")
PREDICTIONS_FILE = os.path.join(BASE_DIR, "..", "outputs", "predictions.json")


@app.route("/api/predictions", methods=["GET"])
def get_predictions():
    with open(PREDICTIONS_FILE) as f:
        return jsonify(json.load(f))


@app.route("/api/evaluation-history", methods=["GET"])
def get_evaluation_history():
    try:
        with open(EVALUATION_FILE) as f:
            history = json.load(f)
        horizon = request.args.get("horizon")
        if horizon is not None:
            try:
                horizon = int(horizon)
                if horizon < 1:
                    raise ValueError
            except ValueError:
                return jsonify({"error": "horizon must be a positive integer"}), 400
            history["rows"] = [
                row for row in history["rows"] if row["horizon_hours"] == horizon
            ]
        return jsonify(history)
    except FileNotFoundError:
        return jsonify({"error": "Evaluation history unavailable. Run evaluate.py to export it."}), 404


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
