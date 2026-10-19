"""Run with venv/bin/python tests/test_evaluation_history.py."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import evaluate
import serve
from features import TARGET_COL


class EvaluationHistoryTests(unittest.TestCase):
    def test_export_uses_exact_common_mask_outputs_and_preserves_horizons(self):
        cutoff = pd.Timestamp("2026-10-04T00:00:00-04:00")
        target = cutoff + pd.Timedelta(hours=12)
        frame = pd.DataFrame({
            "location_name": ["Marino"] * 4,
            "hour_bucket": [target] * 4,
            "origin_bucket": [target - pd.Timedelta(hours=h) for h in (1, 6, 12, 24)],
            "horizon": [1, 6, 12, 24],
            TARGET_COL: [10., 10., 10., 10.],
        })
        frame.attrs.update(cutoff=cutoff, test_end=target)
        model = np.array([9., 13., 8., 20.])
        baseline = lambda train, test: np.array([10., 10., np.nan, 10.])
        exported = []
        with patch.object(evaluate, "fit_model", return_value={}) as fit, \
             patch.object(evaluate, "predict_with", return_value=model) as predict, \
             patch.object(evaluate, "BASELINES", {"baseline": baseline}):
            without = evaluate.evaluate_fold(frame, frame)
            with_export = evaluate.evaluate_fold(frame, frame, exported)
        self.assertEqual(without, with_export)
        self.assertEqual(fit.call_count, 2)
        self.assertEqual(predict.call_count, 2)
        self.assertEqual([r["horizon_hours"] for r in exported], [1, 6, 24])
        self.assertEqual([r["predicted_count"] for r in exported], [9., 13., 20.])
        self.assertEqual(len({r["target_time"] for r in exported}), 1)
        self.assertEqual(exported[1]["absolute_error"], 3.)
        self.assertEqual(pd.Timestamp(exported[1]["origin_time"]), target - pd.Timedelta(hours=6))
        self.assertEqual(pd.Timestamp(exported[1]["fold_cutoff"]), cutoff)
        json.dumps(exported, allow_nan=False)

    def test_run_writes_separate_file_without_changing_report(self):
        cutoff = pd.Timestamp("2026-10-04T00:00:00Z")
        frame = pd.DataFrame({"hour_bucket": [cutoff], TARGET_COL: [10.]})
        fold = {
            "cutoff": cutoff, "test_end": cutoff, "n_train": 1,
            "n_test_scored": 1, "n_test_dropped": 0,
            "scores": {"model": {"mae": 2., "rmse": 2., "bias": 2.}},
            "per_horizon": {6: {"model": 2.}},
        }
        row = {"location_name": "Marino", "horizon_hours": 6, "predicted_count": 12.,
               "actual_count": 10., "target_time": cutoff.isoformat()}

        def score_fold(train, test, rows):
            rows.append(row)
            return fold

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(evaluate, "load_supervised", return_value=frame), \
             patch.object(evaluate, "make_folds", return_value=[(cutoff, cutoff)]), \
             patch.object(evaluate, "split_fold", return_value=(frame, frame)), \
             patch.object(evaluate, "evaluate_fold", side_effect=score_fold), \
             patch.object(evaluate, "print_report"):
            output = Path(directory) / "nested" / "history.json"
            report = evaluate.run("", 1, 7, 21, [6], False, None, str(output))
            history = json.loads(output.read_text())
            self.assertEqual(history["rows"], [row])
            self.assertEqual(history["config"], report["config"])
            self.assertNotIn("rows", report["folds"][0])
            self.assertEqual(history["evaluation_type"], "walk_forward")
            with patch.object(serve, "EVALUATION_FILE", str(output)):
                response = serve.app.test_client().get("/api/evaluation-history")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, history)
                client = serve.app.test_client()
                self.assertEqual(client.get("/api/evaluation-history?horizon=6").json["rows"], [row])
                self.assertEqual(client.get("/api/evaluation-history?horizon=1").json["rows"], [])
                self.assertEqual(client.get("/api/evaluation-history?horizon=bad").status_code, 400)
                self.assertEqual(serve.app.test_client().post("/api/evaluation-history").status_code, 405)

    def test_missing_export_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(serve, "EVALUATION_FILE", str(Path(directory) / "missing.json")):
            response = serve.app.test_client().get("/api/evaluation-history")
            self.assertEqual(response.status_code, 404)
            self.assertIn("evaluate.py", response.json["error"])


class ApiReliabilityTests(unittest.TestCase):
    def write_history(self, path, predicted):
        rows = [{"location_name": "Marino", "horizon_hours": h, "predicted_count": predicted,
                 "actual_count": 10., "target_time": "2026-10-04T12:00:00-04:00"} for h in (1, 6)]
        path.write_text(json.dumps({"evaluation_type": "walk_forward", "config": {}, "rows": rows}))

    def test_history_parses_once_per_export_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            self.write_history(path, 12.)
            client = serve.app.test_client()
            with patch.object(serve, "EVALUATION_FILE", str(path)), \
                 patch.object(serve.json, "load", wraps=json.load) as load:
                first = client.get("/api/evaluation-history?horizon=6").json
                second = client.get("/api/evaluation-history?horizon=6").json
                self.assertEqual(first, second)
                self.assertEqual(load.call_count, 1, "repeat requests must not re-parse the export")
                self.assertEqual([r["horizon_hours"] for r in first["rows"]], [6])
                # A new export (different size and mtime) replaces the cached rows.
                self.write_history(path, 123.)
                os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
                refreshed = client.get("/api/evaluation-history?horizon=6").json
                self.assertEqual(refreshed["rows"][0]["predicted_count"], 123.)
                self.assertEqual(load.call_count, 2)

    def test_corrupt_history_is_a_json_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text('{"rows": [')
            with patch.object(serve, "EVALUATION_FILE", str(path)):
                response = serve.app.test_client().get("/api/evaluation-history?horizon=6")
            self.assertEqual(response.status_code, 503)
            self.assertIn("evaluate.py", response.json["error"])

    def test_missing_or_corrupt_predictions_are_json_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.json"
            client = serve.app.test_client()
            with patch.object(serve, "PREDICTIONS_FILE", str(path)):
                missing = client.get("/api/predictions")
                path.write_text('{"locations": {')
                corrupt = client.get("/api/predictions")
            for response in (missing, corrupt):
                self.assertEqual(response.status_code, 503)
                self.assertTrue(response.is_json, "errors must be JSON, not Flask's HTML page")
                self.assertIn("Forecast unavailable", response.json["error"])


class DeploymentModeTests(unittest.TestCase):
    """Gunicorn's app has no CORS and no refresh route; the development server has both."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.predictions = Path(self.directory.name) / "predictions.json"
        self.predictions.write_text(json.dumps({"locations": {"Marino": []}}))
        self.patch = patch.object(serve, "PREDICTIONS_FILE", str(self.predictions))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def test_both_modes_serve_identical_data(self):
        production = serve.app.test_client().get("/api/predictions")
        development = serve.create_app(dev=True).test_client().get("/api/predictions")
        self.assertEqual(production.status_code, 200)
        self.assertEqual(production.json, development.json)

    def test_production_sends_no_cors_headers(self):
        client = serve.app.test_client()
        response = client.get("/api/predictions", headers={"Origin": "https://other.example"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        preflight = client.options("/api/predictions", headers={
            "Origin": "https://other.example", "Access-Control-Request-Method": "GET"})
        self.assertNotIn("Access-Control-Allow-Origin", preflight.headers)

    def test_development_allows_the_local_frontend(self):
        response = serve.create_app(dev=True).test_client().get(
            "/api/predictions", headers={"Origin": "http://localhost:8765"})
        # flask-cors echoes the requesting origin; either form allows the request.
        self.assertIn(response.headers.get("Access-Control-Allow-Origin"),
                      ("*", "http://localhost:8765"))

    def test_refresh_exists_only_on_the_development_server(self):
        self.assertEqual(serve.app.test_client().post("/api/refresh").status_code, 404)
        dev = serve.create_app(dev=True).test_client()
        with patch.object(serve.subprocess, "run") as run:
            response = dev.post("/api/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c.args[0][1].rsplit(os.sep, 1)[-1] for c in run.call_args_list],
                         ["features.py", "predict.py"])
        failure = serve.subprocess.CalledProcessError(1, "predict.py")
        with patch.object(serve.subprocess, "run", side_effect=failure):
            response = dev.post("/api/refresh")
        self.assertEqual(response.status_code, 500)
        self.assertIn("features.py failed", response.json["error"])


if __name__ == "__main__":
    unittest.main()
