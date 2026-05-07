from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pyperclip

from .downloader import ffmpeg_available, human_bytes
from .history import connect_history
from .models import HistoryEntry
from .paths import config_path, history_path, state_dir


def render_history_entry(entry: HistoryEntry) -> list[str]:
    identity = f"{entry.extractor_key}:{entry.media_id}" if entry.extractor_key and entry.media_id else entry.normalized_url
    size = human_bytes(entry.file_size) if entry.file_size else "-"
    preset = entry.preset_name or "-"
    status = "present" if Path(entry.output_path).exists() else "missing"
    return [
        f"id         {entry.id}",
        f"when       {entry.downloaded_at}",
        f"platform   {entry.platform}",
        f"preset     {preset}",
        f"mode       {entry.mode}",
        f"quality    {entry.quality_key}",
        f"size       {size}",
        f"status     {status}",
        f"identity   {identity}",
        f"path       {entry.output_path}",
    ]


def dir_writable(path: Path) -> bool:
    target = path if path.exists() else path.parent
    return os.access(target, os.W_OK)


def collect_doctor_checks() -> list[tuple[str, bool, str]]:
    config_file = config_path()
    db_file = history_path()
    default_output = Path.home() / "Downloads"
    checks: list[tuple[str, bool, str]] = [
        ("python", True, sys.version.split()[0]),
    ]
    ffmpeg_ready = ffmpeg_available()
    checks.append(("ffmpeg", ffmpeg_ready, "available" if ffmpeg_ready else "not found; audio conversion and some muxing will be limited"))
    checks.append(("config", dir_writable(config_file), str(config_file)))
    checks.append(("state", dir_writable(state_dir()), str(state_dir())))
    try:
        with connect_history() as connection:
            connection.execute("SELECT 1").fetchone()
        checks.append(("history", True, str(db_file)))
    except sqlite3.Error as exc:
        checks.append(("history", False, f"{db_file} ({exc})"))
    try:
        pyperclip.paste()
        checks.append(("clipboard", True, "clipboard access available"))
    except pyperclip.PyperclipException as exc:
        checks.append(("clipboard", False, str(exc)))
    checks.append(("output", dir_writable(default_output), str(default_output)))
    try:
        import curses  # noqa: F401

        checks.append(("curses", True, "interactive mode available"))
    except ModuleNotFoundError:
        checks.append(("curses", False, "interactive mode unavailable in this Python environment"))
    return checks

