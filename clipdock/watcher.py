from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pyperclip

from .paths import watch_seen_path
from .platforms import infer_platform_from_url, normalize_url


URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")
SEEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_urls (
    normalized_url TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ClipboardMatch:
    raw_text: str
    url: str
    platform: str
    normalized_url: str


def connect_seen(path: Path | None = None) -> sqlite3.Connection:
    target = path or watch_seen_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.executescript(SEEN_SCHEMA)
    return connection


def has_seen_url(normalized_url: str, *, db_path: Path | None = None) -> bool:
    with connect_seen(db_path) as connection:
        row = connection.execute("SELECT 1 FROM seen_urls WHERE normalized_url = ?", (normalized_url,)).fetchone()
    return row is not None


def mark_seen_url(normalized_url: str, *, db_path: Path | None = None) -> None:
    seen_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with connect_seen(db_path) as connection:
        connection.execute(
            """
            INSERT INTO seen_urls (normalized_url, seen_at)
            VALUES (?, ?)
            ON CONFLICT(normalized_url) DO UPDATE SET seen_at = excluded.seen_at
            """,
            (normalized_url, seen_at),
        )


def extract_supported_url(text: str) -> ClipboardMatch | None:
    for match in URL_PATTERN.findall(text):
        platform = infer_platform_from_url(match)
        if platform is None:
            continue
        return ClipboardMatch(
            raw_text=text,
            url=match,
            platform=platform,
            normalized_url=normalize_url(platform, match),
        )
    return None


def watch_clipboard(
    *,
    interval: float,
    on_match: Callable[[ClipboardMatch], bool],
    emit: Callable[[str], None],
) -> int:
    previous_text: str | None = None
    while True:
        try:
            clipboard_text = pyperclip.paste()
        except pyperclip.PyperclipException as exc:
            emit(f"watch      clipboard unavailable: {exc}")
            time.sleep(interval)
            continue
        if clipboard_text == previous_text:
            time.sleep(interval)
            continue
        previous_text = clipboard_text
        match = extract_supported_url(clipboard_text or "")
        if match is None:
            time.sleep(interval)
            continue
        if not on_match(match):
            return 0
        time.sleep(interval)
