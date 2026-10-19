"""Atomic artifact writes; run with venv/bin/python tests/test_fileio.py."""

import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import fileio  # noqa: E402
import train  # noqa: E402


def test_write_replaces_content_with_plain_open_permissions():
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "predictions.json"
        target.write_text("old")
        previous = os.umask(0o022)
        try:
            fileio.atomic_write_text(str(target), '{"new": true}')
        finally:
            os.umask(previous)
        assert target.read_text() == '{"new": true}'
        # mkstemp alone would leave 0600, which other service users cannot read.
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        assert os.listdir(directory) == ["predictions.json"], "temporary file left behind"


def test_failed_write_keeps_previous_file_and_cleans_up():
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "model.pkl"
        target.write_bytes(b"previous model")
        with patch.object(fileio.os, "replace", side_effect=OSError("disk full")):
            try:
                fileio.atomic_write_bytes(str(target), b"new model")
            except OSError:
                pass
            else:
                raise AssertionError("expected the simulated failure to propagate")
        assert target.read_bytes() == b"previous model"
        assert os.listdir(directory) == ["model.pkl"], "temporary file left behind"


def test_unpicklable_bundle_leaves_previous_model():
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "model.pkl"
        target.write_bytes(b"previous model")
        try:
            train.save_bundle({"model": lambda: None}, str(target))  # lambdas cannot pickle
        except Exception:
            pass
        assert target.read_bytes() == b"previous model"
        assert os.listdir(directory) == ["model.pkl"]


def test_readers_never_see_a_truncated_target():
    """Every byte goes to the temporary file; the target only ever changes by rename."""
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "predictions.json"
        target.write_text("complete old file")
        seen = []
        real_replace = os.replace

        def observe(source, destination):
            seen.append(Path(destination).read_text())  # state just before the swap
            real_replace(source, destination)

        with patch.object(fileio.os, "replace", side_effect=observe):
            fileio.atomic_write_text(str(target), "complete new file")
        assert seen == ["complete old file"]
        assert target.read_text() == "complete new file"


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
