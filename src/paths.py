"""
paths.py

Default locations of GymCast's inputs and artifacts, anchored to the
repository rather than the working directory. With cwd-relative defaults like
"../data", the scripts worked only when launched from src/; anchored defaults
resolve to the same files from anywhere. Paths passed on the command line keep
their usual meaning.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
OUTPUTS_DIR = ROOT / "outputs"

RAW_COUNTS = DATA_DIR / "marino_counts.csv"
POLL_ERRORS = DATA_DIR / "poll_errors.csv"
FEATURES = DATA_DIR / "features.csv"
ACADEMIC_CALENDAR = DATA_DIR / "academic_calendar.csv"
FACILITY_HOURS = DATA_DIR / "facility_hours.json"
FACILITY_OVERRIDES = DATA_DIR / "facility_overrides.json"

MODEL = MODELS_DIR / "model.pkl"

PREDICTIONS = OUTPUTS_DIR / "predictions.json"
EVALUATION_PREDICTIONS = OUTPUTS_DIR / "evaluation_predictions.json"
