"""
fileio.py

Crash-safe writes for every artifact the pipeline publishes (features.csv,
model.pkl, predictions.json, evaluation exports).

Writing a file in place truncates it first, so for as long as the write takes,
a reader sees a partial file. That matters here: Gunicorn serves
predictions.json while the refresh timer rewrites it, and the nightly train job
reads features.csv while the same timer may be rebuilding it.
"""

import os
import tempfile


def atomic_write_bytes(path: str, data: bytes) -> None:
    """
    Replace `path` with `data`; readers see the old file or the new one, never part of one.

    The data goes to a temporary file in the same directory, which is then
    renamed over the target. A rename within one filesystem is atomic, and a
    reader that already opened the old file keeps reading it intact.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp creates owner-only (0600) files, and the rename keeps that mode.
        # Give the result the permissions a plain open() would have.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(temp_path, 0o666 & ~umask)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def atomic_write_text(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))
