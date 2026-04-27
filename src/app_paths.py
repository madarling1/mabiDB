from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir())).resolve()
    return app_dir()


APP_DIR = app_dir()
RESOURCE_DIR = resource_dir()
DATA_DIR = APP_DIR / "data"
CONFIG_DIR = APP_DIR / "config"
