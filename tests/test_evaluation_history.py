"""Run with venv/bin/python tests/test_evaluation_history.py."""
import json
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


if __name__ == "__main__":
    unittest.main()
