from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

import pyperclip

from .platforms import infer_platform_from_url, normalize_url


URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")


@dataclass(frozen=True)
class ClipboardMatch:
    raw_text: str
    url: str
    platform: str
    normalized_url: str


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

