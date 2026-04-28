from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "mabiDB"


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


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


APP_DIR = app_dir()
RESOURCE_DIR = resource_dir()
USER_DATA_DIR = user_data_dir()
DATA_DIR = (USER_DATA_DIR if is_frozen() else APP_DIR) / "data"
CONFIG_DIR = (USER_DATA_DIR if is_frozen() else APP_DIR) / "config"
