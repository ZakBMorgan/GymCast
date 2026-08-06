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

from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # allow your website's frontend JS to fetch this cross-origin

BASE_DIR = os.path.dirname(__file__)
PREDICTIONS_FILE = os.path.join(BASE_DIR, "..", "outputs", "predictions.json")


@app.route("/api/predictions", methods=["GET"])
def get_predictions():
    with open(PREDICTIONS_FILE) as f:
        return jsonify(json.load(f))


@app.route("/api/refresh", methods=["POST"])
def refresh():
    # Re-run the pipeline: features -> predict.
    # (Retrain less frequently - e.g. nightly - since training is heavier than inference.)
    subprocess.run(["python", os.path.join(BASE_DIR, "features.py")], check=True, cwd=BASE_DIR)
    subprocess.run(["python", os.path.join(BASE_DIR, "predict.py")], check=True, cwd=BASE_DIR)
    return jsonify({"status": "refreshed"})


if __name__ == "__main__":
    app.run(port=5000)
