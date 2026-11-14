"""Paths shared by the Windows release and the existing source launcher."""
import os
from pathlib import Path
import sys


def data_root():
    override = os.getenv("EXAMAI_DATA_DIR")
    if override:
        path = Path(override)
    elif getattr(sys, "frozen", False):
        path = Path(os.environ["LOCALAPPDATA"]) / "EXAM AI"
    else:
        return Path.cwd()
    path.mkdir(parents=True, exist_ok=True)
    return path
