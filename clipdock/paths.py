from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_path, user_state_path

from .models import APP_NAME


def config_dir() -> Path:
    return Path(user_config_path(APP_NAME, appauthor=False))


def config_path() -> Path:
    return config_dir() / "config.toml"


def state_dir() -> Path:
    return Path(user_state_path(APP_NAME, appauthor=False))


def history_path() -> Path:
    return state_dir() / "history.sqlite3"


def watch_pid_path() -> Path:
    return state_dir() / "watch.pid"


def watch_log_path() -> Path:
    return state_dir() / "watch.log"


def watch_seen_path() -> Path:
    return state_dir() / "watch_seen.sqlite3"
