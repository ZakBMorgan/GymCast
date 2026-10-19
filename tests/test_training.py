"""
Backend selection and model publishing; run with venv/bin/python tests/test_training.py.

Two production guarantees: the backend is never substituted silently, and a
failed or invalid retrain never replaces the last model that served forecasts.
"""

import contextlib
import io
import os
import pickle
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
sys.path.insert(0, str(TESTS_DIR))

import train  # noqa: E402
from features import TARGET_COL, build_supervised_frame  # noqa: E402
from test_pipeline import synthetic_panel  # noqa: E402

PANEL = synthetic_panel(weeks=3)
FRAME = build_supervised_frame(PANEL, [1, 24]).dropna(subset=[TARGET_COL])


def expect(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__}")


@contextlib.contextmanager
def lightgbm_missing():
    with patch.object(train, "lgb", None), \
         patch.object(train, "LGB_IMPORT_ERROR", OSError("libomp.dylib not found")):
        yield


@contextlib.contextmanager
def published_model(directory):
    """A features file plus an existing 'last known-good' model to protect."""
    features = Path(directory) / "features.csv"
    model = Path(directory) / "model.pkl"
    PANEL.to_csv(features, index=False)
    model.write_bytes(b"last known-good model")
    yield str(features), str(model)


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def test_default_backend_is_lightgbm_and_recorded():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GYMCAST_BACKEND", None)
        bundle = train.fit_model(FRAME)
    assert bundle["backend"] == "lightgbm" and bundle["used_lgb"] is True


def test_sklearn_is_available_when_chosen_explicitly():
    for chosen in ({"backend": "sklearn"}, {}):
        env = {} if chosen else {"GYMCAST_BACKEND": "sklearn"}
        with patch.dict(os.environ, env):
            bundle = train.fit_model(FRAME, **chosen)
        assert bundle["backend"] == "sklearn" and bundle["used_lgb"] is False
        preds = train.predict_with(bundle, FRAME.head(200))
        assert np.isfinite(preds).all()


def test_missing_lightgbm_is_an_error_not_a_silent_fallback():
    with lightgbm_missing(), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GYMCAST_BACKEND", None)
        exc = expect(train.BackendUnavailable, train.fit_model, FRAME)
        assert "libomp" in str(exc) and "--backend sklearn" in str(exc)
        # The alternative still works when asked for by name.
        assert train.fit_model(FRAME, backend="sklearn")["backend"] == "sklearn"
    expect(ValueError, train.resolve_backend, "xgboost")


def test_lightgbm_fit_failure_keeps_last_known_good_model():
    with tempfile.TemporaryDirectory() as directory, published_model(directory) as (features, model):
        with patch.object(train.lgb.LGBMRegressor, "fit", side_effect=RuntimeError("boom")):
            expect(RuntimeError, quiet, train.train_model, features, model, [1, 24])
        assert Path(model).read_bytes() == b"last known-good model"
        assert not Path(train.previous_model_path(model)).exists()
        assert sorted(os.listdir(directory)) == ["features.csv", "model.pkl"]


def test_invalid_new_model_is_not_published():
    with tempfile.TemporaryDirectory() as directory, published_model(directory) as (features, model):
        bad = lambda bundle, frame: np.full(len(frame), np.nan)  # noqa: E731
        with patch.object(train, "predict_with", side_effect=bad):
            exc = expect(train.ModelValidationError, quiet, train.train_model,
                         features, model, [1, 24])
        assert "non-finite" in str(exc)
        assert Path(model).read_bytes() == b"last known-good model"


def test_successful_retrain_keeps_replaced_model_for_rollback():
    with tempfile.TemporaryDirectory() as directory, published_model(directory) as (features, model):
        quiet(train.train_model, features, model, [1, 24])
        assert Path(train.previous_model_path(model)).read_bytes() == b"last known-good model"
        assert train.load_bundle(model)["backend"] == "lightgbm"


def test_cli_failures_exit_nonzero_and_say_what_was_kept():
    with tempfile.TemporaryDirectory() as directory, published_model(directory) as (features, model):
        argv = ["--input", features, "--model-out", model, "--max-horizon", "2"]
        with lightgbm_missing():
            exc = expect(SystemExit, quiet, train.main, argv)
        assert exc.code != 0 and "left unchanged" in str(exc.code)

        stderr = io.StringIO()
        with patch.object(train, "train_model", side_effect=RuntimeError("segfault-ish")), \
             contextlib.redirect_stderr(stderr):
            expect(RuntimeError, train.main, argv)  # traceback reaches the journal
        assert "left unchanged" in stderr.getvalue()
        assert Path(model).read_bytes() == b"last known-good model"


def test_bundles_from_before_explicit_backends_still_load():
    bundle = train.fit_model(FRAME)
    del bundle["backend"]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "old.pkl"
        path.write_bytes(pickle.dumps(bundle))
        assert train.load_bundle(str(path))["backend"] == "lightgbm"


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
