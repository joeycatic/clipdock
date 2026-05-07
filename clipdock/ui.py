from __future__ import annotations

import textwrap
import threading
import time
from typing import Any

import curses

from .config import get_preset, load_config, save_config
from .diagnostics import render_formats, render_simulation
from .downloader import (
    build_quality_options,
    download_media,
    extract_video_info,
    ffmpeg_available,
    format_duration,
    format_views,
    match_quality,
    normalize_upload_date,
    preview_output_path,
    shorten,
)
from .history import extract_media_identity, latest_duplicate, load_entries, record_download
from .inspector import collect_doctor_checks, render_history_entry
from .models import APP_NAME, APP_SUBTITLE, AppState, DownloadProgress, DownloadSettings, MIN_HEIGHT, MIN_WIDTH, PresetConfig, QualityOption, REMUX_CONTAINERS, ResolvedPlan
from .platforms import PLATFORMS, classify_platform_error, get_platform, infer_platform_from_url, normalize_url, platform_notes, youtube_url_has_playlist


def safe_addstr(window: curses.window, y: int, x: int, text: str, attr: int = 0, max_width: int | None = None) -> None:
    height, width = window.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    max_len = max(0, width - x - 1)
    if max_width is not None:
        max_len = min(max_len, max_width)
    if max_len <= 0:
        return
    try:
        window.addnstr(y, x, text, max_len, attr)
    except curses.error:
        pass


def init_colors() -> dict[str, int]:
    palette = {
        "title": curses.A_BOLD,
        "heading": curses.A_BOLD,
        "body": 0,
        "muted": curses.A_DIM,
        "accent": curses.A_BOLD,
        "selected": curses.A_REVERSE | curses.A_BOLD,
        "success": curses.A_BOLD,
        "error": curses.A_BOLD,
        "warning": curses.A_BOLD,
        "border": curses.A_DIM,
    }
    if not curses.has_colors():
        return palette
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(3, curses.COLOR_WHITE, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_RED, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)
    palette.update(
        {
            "title": curses.color_pair(1) | curses.A_BOLD,
            "heading": curses.color_pair(1) | curses.A_BOLD,
            "body": curses.color_pair(3),
            "muted": curses.A_DIM,
            "accent": curses.color_pair(1) | curses.A_BOLD,
            "selected": curses.color_pair(2) | curses.A_BOLD,
            "success": curses.color_pair(4) | curses.A_BOLD,
            "error": curses.color_pair(5) | curses.A_BOLD,
            "warning": curses.color_pair(6) | curses.A_BOLD,
            "border": curses.A_DIM,
        }
    )
    return palette


def draw_box(window: curses.window, top: int, left: int, height: int, width: int, title: str, palette: dict[str, int]) -> None:
    if height < 3 or width < 4:
        return
    for x in range(left + 1, left + width - 1):
        safe_addstr(window, top, x, "-", palette["border"])
        safe_addstr(window, top + height - 1, x, "-", palette["border"])
    for y in range(top + 1, top + height - 1):
        safe_addstr(window, y, left, "|", palette["border"])
        safe_addstr(window, y, left + width - 1, "|", palette["border"])
    for y, x in ((top, left), (top, left + width - 1), (top + height - 1, left), (top + height - 1, left + width - 1)):
        safe_addstr(window, y, x, "+", palette["border"])
    safe_addstr(window, top, left + 2, f"[ {title} ]", palette["heading"], max_width=max(0, width - 4))


def wrap_lines(value: str, width: int, max_lines: int) -> list[str]:
    if width <= 0:
        return []
    wrapped: list[str] = []
    for raw_line in value.splitlines() or [""]:
        wrapped.extend(textwrap.wrap(raw_line, width=width) or [""])
    visible = wrapped[:max_lines]
    if len(wrapped) > max_lines and visible:
        visible[-1] = shorten(visible[-1], width)
    return visible


def is_playlist_info(info: dict[str, Any]) -> bool:
    return info.get("_type") == "playlist" or isinstance(info.get("entries"), list)


def playlist_entries(info: dict[str, Any]) -> list[Any]:
    entries = info.get("entries") or []
    return entries if isinstance(entries, list) else []


def sync_playlist_queue(settings: DownloadSettings, info: dict[str, Any]) -> None:
    if not is_playlist_info(info):
        settings.playlist_items = None
        return
    entry_total = len(playlist_entries(info))
    if entry_total <= 0:
        settings.playlist_items = None
        return
    if not settings.playlist_items:
        settings.playlist_items = list(range(1, entry_total + 1))
        return
    kept = sorted({item for item in settings.playlist_items if 1 <= item <= entry_total})
    settings.playlist_items = kept or list(range(1, entry_total + 1))


def playlist_queue_label(info: dict[str, Any], settings: DownloadSettings) -> str:
    total = len(playlist_entries(info))
    if total <= 0:
        return "empty"
    kept = len(settings.playlist_items or [])
    return f"{kept}/{total} kept"


def extras_summary(settings: DownloadSettings) -> str:
    values: list[str] = []
    if settings.write_subs:
        values.append("subs")
    if settings.write_auto_subs:
        values.append("auto")
    if settings.embed_subs:
        values.append("embed-subs")
    if settings.write_thumbnail:
        values.append("thumb")
    if settings.embed_thumbnail:
        values.append("embed-thumb")
    if settings.write_info_json:
        values.append("info")
    if settings.embed_metadata:
        values.append("meta")
    if settings.split_chapters:
        values.append("chapters")
    if settings.remux_video:
        values.append(f"remux={settings.remux_video}")
    if settings.sub_lang:
        values.append(f"lang={settings.sub_lang}")
    return ", ".join(values) if values else "none"


def preset_label(settings: DownloadSettings) -> str:
    return settings.preset_name or "none"


def auth_label(settings: DownloadSettings) -> str:
    return settings.auth.describe()


def source_summary(settings: DownloadSettings) -> str:
    preset = preset_label(settings)
    auth = "auth" if settings.auth.cookies or settings.auth.cookies_from_browser else "no auth"
    return f"{get_platform(settings.platform).option.label} | {preset} | {auth}"


def download_summary(info: dict[str, Any], settings: DownloadSettings, current_quality: QualityOption) -> str:
    parts = [("video+audio" if settings.mode == "video" else "audio-only"), current_quality.label]
    if is_playlist_info(info):
        parts.append(playlist_queue_label(info, settings))
    return " | ".join(parts)


def output_summary(settings: DownloadSettings) -> str:
    if extras_summary(settings) == "none":
        return "folder, naming"
    return f"folder, naming, {extras_summary(settings)}"


def inspect_summary(info: dict[str, Any]) -> str:
    return f"{len(info.get('formats') or [])} formats"


def apply_preset_to_settings(settings: DownloadSettings, preset: PresetConfig) -> None:
    settings.preset_name = preset.name
    if preset.platform:
        settings.platform = preset.platform
    if preset.audio_only is not None:
        settings.mode = "audio" if preset.audio_only else "video"
    if preset.playlist is not None:
        settings.playlist = preset.playlist
    if preset.output_dir:
        settings.output_dir = preset.output_dir
    if preset.filename_template:
        settings.filename_template = preset.filename_template
    if preset.cookies is not None:
        settings.auth.cookies = preset.cookies
    if preset.cookies_from_browser is not None:
        browser, _, profile = preset.cookies_from_browser.partition(":")
        settings.auth.cookies_from_browser = (browser, profile or None) if browser else None
    for field_name in (
        "write_subs",
        "write_auto_subs",
        "embed_subs",
        "write_thumbnail",
        "embed_thumbnail",
        "write_info_json",
        "embed_metadata",
        "split_chapters",
    ):
        value = getattr(preset, field_name)
        if value is not None:
            setattr(settings, field_name, value)
    if preset.sub_lang is not None:
        settings.sub_lang = preset.sub_lang
    if preset.remux_video is not None:
        settings.remux_video = preset.remux_video


def build_command_items(info: dict[str, Any], settings: DownloadSettings, current_quality: QualityOption) -> list[tuple[str, str, str, str]]:
    return [
        ("source_group", "source", source_summary(settings), "platform, preset, url, and auth settings"),
        ("download_group", "download", download_summary(info, settings, current_quality), "mode, quality, playlist, and queue"),
        ("output_group", "output", output_summary(settings), "output folder, filename template, and extras"),
        ("inspect_group", "inspect", inspect_summary(info), "formats and simulation tools"),
        ("run", "run", "start download", "begin transfer with current settings"),
        ("quit", "quit", "exit", "leave the downloader"),
    ]


def prompt_input(stdscr: curses.window, palette: dict[str, int], title: str, prompt: str, initial_value: str = "") -> str | None:
    curses.curs_set(1)
    value = list(initial_value)
    cursor = len(value)
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(46, min(width - 6, 96))
        box_height = 9
        top = max(2, (height - box_height) // 2)
        left = max(2, (width - box_width) // 2)
        input_width = box_width - 6
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, title, palette)
        safe_addstr(stdscr, top + 2, left + 2, shorten(prompt, box_width - 4), palette["body"])
        safe_addstr(stdscr, top + 4, left + 2, ">" + " " * max(0, input_width), palette["muted"])
        visible_text = "".join(value)
        start = max(0, cursor - input_width + 1) if cursor > input_width - 1 else 0
        display = visible_text[start : start + input_width]
        safe_addstr(stdscr, top + 4, left + 3, display, palette["body"])
        safe_addstr(stdscr, top + 6, left + 2, "Enter accept | Esc cancel | Left/Right move", palette["muted"])
        stdscr.move(top + 4, left + 3 + min(cursor - start, max(0, input_width - 1)))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("\n", "\r"):
            curses.curs_set(0)
            return "".join(value).strip()
        if key == "\x1b":
            curses.curs_set(0)
            return None
        if key in ("\x7f", "\b", "\x08") or key == curses.KEY_BACKSPACE:
            if cursor > 0:
                value.pop(cursor - 1)
                cursor -= 1
            continue
        if key == curses.KEY_DC:
            if cursor < len(value):
                value.pop(cursor)
            continue
        if key == curses.KEY_LEFT:
            cursor = max(0, cursor - 1)
            continue
        if key == curses.KEY_RIGHT:
            cursor = min(len(value), cursor + 1)
            continue
        if key == curses.KEY_HOME:
            cursor = 0
            continue
        if key == curses.KEY_END:
            cursor = len(value)
            continue
        if isinstance(key, str) and key.isprintable():
            value.insert(cursor, key)
            cursor += 1


def prompt_confirm(stdscr: curses.window, palette: dict[str, int], title: str, lines: list[str], confirm_label: str) -> bool:
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(56, min(width - 6, 104))
        box_height = max(9, min(height - 4, 12 + len(lines)))
        top = max(2, (height - box_height) // 2)
        left = max(2, (width - box_width) // 2)
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, title, palette)
        for idx, line in enumerate(lines[: box_height - 4]):
            safe_addstr(stdscr, top + 2 + idx, left + 2, shorten(line, box_width - 4), palette["body"])
        safe_addstr(stdscr, top + box_height - 2, left + 2, f"{confirm_label} [y/N] | q cancel", palette["muted"])
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("y", "Y"):
            return True
        if key in ("n", "N", "q", "Q", "\x1b"):
            return False


def manage_playlist_queue_ui(
    stdscr: curses.window,
    palette: dict[str, int],
    info: dict[str, Any],
    settings: DownloadSettings,
    state: AppState,
) -> None:
    entries = playlist_entries(info)
    if not entries:
        state.status_line = "No playlist entries were available to manage."
        return
    sync_playlist_queue(settings, info)
    kept = set(settings.playlist_items or [])
    selected = 0
    offset = 0
    total = len(entries)
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(56, min(width - 6, 108))
        box_height = max(14, min(height - 4, 24))
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        visible_rows = max(1, box_height - 7)
        if selected < offset:
            offset = selected
        if selected >= offset + visible_rows:
            offset = selected - visible_rows + 1

        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "playlist queue", palette)
        safe_addstr(stdscr, top + 1, left + 2, shorten(info.get("title") or "Playlist", box_width - 4), palette["heading"])
        safe_addstr(stdscr, top + 2, left + 2, shorten(f"kept {len(kept)}/{total} entries", box_width - 4), palette["accent"])
        for row in range(visible_rows):
            entry_index = offset + row
            if entry_index >= total:
                break
            entry = entries[entry_index]
            in_queue = entry_index + 1 in kept
            marker = "[keep]" if in_queue else "[skip]"
            title = entry.get("title") if isinstance(entry, dict) else None
            if not title and isinstance(entry, dict):
                title = entry.get("url")
            if not title:
                title = f"Playlist item {entry_index + 1}"
            line = f"{entry_index + 1:>3}. {marker} {title}"
            attr = palette["selected"] if entry_index == selected else palette["body"]
            safe_addstr(stdscr, top + 4 + row, left + 2, shorten(line, box_width - 4), attr)
        safe_addstr(stdscr, top + box_height - 3, left + 2, "Enter/Space toggle | d/Delete remove | r restore | a restore all", palette["muted"])
        safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k or arrows move | PgUp/PgDn scroll | q close", palette["muted"])
        stdscr.refresh()

        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            settings.playlist_items = sorted(kept)
            state.status_line = f"Playlist queue updated: {len(kept)}/{total} entries kept."
            state.log(state.status_line)
            return
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(total - 1, selected + 1)
            continue
        if key == curses.KEY_PPAGE:
            selected = max(0, selected - visible_rows)
            continue
        if key == curses.KEY_NPAGE:
            selected = min(total - 1, selected + visible_rows)
            continue
        item_number = selected + 1
        if key in ("a", "A"):
            kept = set(range(1, total + 1))
            continue
        if key in ("r", "R"):
            kept.add(item_number)
            continue
        if key in ("d", "D", curses.KEY_DC, "\n", "\r", " "):
            if item_number in kept:
                if len(kept) == 1:
                    state.status_line = "The queue must keep at least one playlist entry."
                    state.log(state.status_line)
                    continue
                kept.remove(item_number)
            else:
                kept.add(item_number)


def show_text_modal(
    stdscr: curses.window,
    palette: dict[str, int],
    title: str,
    lines: list[str],
    footer: str = "j/k or arrows move | PgUp/PgDn scroll | q close",
) -> None:
    scroll = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(64, min(width - 6, 120))
        box_height = max(12, min(height - 4, 28))
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        visible_rows = max(1, box_height - 4)
        max_scroll = max(0, len(lines) - visible_rows)
        scroll = min(scroll, max_scroll)

        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, title, palette)
        for row in range(visible_rows):
            line_index = scroll + row
            if line_index >= len(lines):
                break
            safe_addstr(
                stdscr,
                top + 1 + row,
                left + 2,
                shorten(lines[line_index], box_width - 4),
                palette["body"],
                max_width=max(0, box_width - 4),
            )
        safe_addstr(
            stdscr,
            top + box_height - 2,
            left + 2,
            shorten(footer, box_width - 4),
            palette["muted"],
            max_width=max(0, box_width - 4),
        )
        stdscr.refresh()

        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b", "\n", "\r"):
            return
        if key in (curses.KEY_UP, "k"):
            scroll = max(0, scroll - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            scroll = min(max_scroll, scroll + 1)
            continue
        if key == curses.KEY_PPAGE:
            scroll = max(0, scroll - visible_rows)
            continue
        if key == curses.KEY_NPAGE:
            scroll = min(max_scroll, scroll + visible_rows)
            continue


def show_history_ui(stdscr: curses.window, palette: dict[str, int], state: AppState) -> None:
    entries = load_entries(limit=20)
    lines: list[str] = []
    if not entries:
        lines.append("No history entries found.")
    else:
        for idx, entry in enumerate(entries):
            if idx:
                lines.append("")
            lines.extend(render_history_entry(entry))
    show_text_modal(stdscr, palette, "history", lines)
    state.status_line = "Closed history view."
    state.log(state.status_line)


def show_doctor_ui(stdscr: curses.window, palette: dict[str, int], state: AppState) -> None:
    lines = ["doctor", "------"]
    for label, ok, detail in collect_doctor_checks():
        status = "ok" if ok else "warn"
        lines.append(f"{label:<10} {status:<4} {detail}")
    show_text_modal(stdscr, palette, "doctor", lines)
    state.status_line = "Closed doctor view."
    state.log(state.status_line)


def show_preset_picker_ui(stdscr: curses.window, palette: dict[str, int], state: AppState, settings: DownloadSettings) -> str | None:
    config = load_config()
    if not config.presets:
        state.status_line = "No presets are configured."
        state.log(state.status_line)
        return None
    items = sorted(config.presets)
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(52, min(width - 6, 84))
        box_height = max(12, min(height - 4, 18))
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "presets", palette)
        safe_addstr(stdscr, top + 1, left + 2, "Select a preset to apply its defaults.", palette["body"], max_width=max(0, box_width - 4))
        for idx, name in enumerate(items[: box_height - 5]):
            preset = config.presets[name]
            label = f" {idx + 1}. {name:<16} {shorten((preset.quality or 'default') + (' audio' if preset.audio_only else ''), box_width - 24)}"
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(stdscr, top + 3 + idx, left + 2, label, attr, max_width=max(0, box_width - 4))
        safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k or arrows move | Enter apply | q cancel", palette["muted"], max_width=max(0, box_width - 4))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return None
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(len(items) - 1, selected + 1)
            continue
        if key in ("\n", "\r", " "):
            chosen = items[selected]
            apply_preset_to_settings(settings, config.presets[chosen])
            state.status_line = f'Applied preset "{chosen}".'
            state.log(state.status_line)
            return config.presets[chosen].quality


def edit_auth_ui(stdscr: curses.window, palette: dict[str, int], state: AppState, settings: DownloadSettings) -> None:
    items = [
        ("cookies", "cookie file", settings.auth.cookies or "none"),
        ("browser", "browser cookies", settings.auth.describe() if settings.auth.cookies_from_browser else "none"),
        ("clear", "clear auth", "remove all auth sources"),
        ("back", "back", "return to session"),
    ]
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(52, min(width - 6, 86))
        box_height = 11
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "auth", palette)
        for idx, (_key, label, value) in enumerate(items):
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(stdscr, top + 2 + idx, left + 2, f" {idx + 1}. {label:<16} {shorten(value, box_width - 24)}", attr, max_width=max(0, box_width - 4))
        safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k or arrows move | Enter edit | q back", palette["muted"], max_width=max(0, box_width - 4))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(len(items) - 1, selected + 1)
            continue
        if key not in ("\n", "\r", " "):
            continue
        choice = items[selected][0]
        if choice == "cookies":
            updated = prompt_input(stdscr, palette, "cookie file", "Path to Netscape-format cookie file (leave blank to clear)", settings.auth.cookies or "")
            settings.auth.cookies = updated or None
            if updated:
                settings.auth.cookies_from_browser = None
            state.status_line = "Updated cookie-file auth setting."
            state.log(state.status_line)
            items[0] = ("cookies", "cookie file", settings.auth.cookies or "none")
            items[1] = ("browser", "browser cookies", settings.auth.describe() if settings.auth.cookies_from_browser else "none")
            continue
        if choice == "browser":
            updated = prompt_input(stdscr, palette, "browser cookies", "Browser cookie source such as chrome or chrome:Profile 1", "")
            if updated:
                browser, _, profile = updated.partition(":")
                settings.auth.cookies_from_browser = (browser.strip(), profile.strip() or None)
                settings.auth.cookies = None
            else:
                settings.auth.cookies_from_browser = None
            state.status_line = "Updated browser-cookie auth setting."
            state.log(state.status_line)
            items[0] = ("cookies", "cookie file", settings.auth.cookies or "none")
            items[1] = ("browser", "browser cookies", settings.auth.describe() if settings.auth.cookies_from_browser else "none")
            continue
        if choice == "clear":
            settings.auth.cookies = None
            settings.auth.cookies_from_browser = None
            state.status_line = "Cleared auth settings."
            state.log(state.status_line)
            items[0] = ("cookies", "cookie file", "none")
            items[1] = ("browser", "browser cookies", "none")
            continue
        return


def manage_extras_ui(stdscr: curses.window, palette: dict[str, int], state: AppState, settings: DownloadSettings) -> None:
    items = [
        ("write_subs", "write subtitles"),
        ("write_auto_subs", "write auto-subs"),
        ("embed_subs", "embed subtitles"),
        ("write_thumbnail", "write thumbnail"),
        ("embed_thumbnail", "embed thumbnail"),
        ("write_info_json", "write info json"),
        ("embed_metadata", "embed metadata"),
        ("split_chapters", "split chapters"),
        ("sub_lang", "subtitle lang"),
        ("remux_video", "remux video"),
        ("back", "back"),
    ]
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(56, min(width - 6, 92))
        box_height = max(14, min(height - 4, 18))
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "extras", palette)
        for idx, (key_name, label) in enumerate(items):
            if key_name == "back":
                value = "return"
            elif key_name in {"sub_lang", "remux_video"}:
                value = getattr(settings, key_name) or "none"
            else:
                value = "on" if getattr(settings, key_name) else "off"
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(stdscr, top + 2 + idx, left + 2, f" {idx + 1}. {label:<18} {value}", attr, max_width=max(0, box_width - 4))
        safe_addstr(stdscr, top + box_height - 2, left + 2, "Enter toggle/edit | j/k move | q back", palette["muted"], max_width=max(0, box_width - 4))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(len(items) - 1, selected + 1)
            continue
        if key not in ("\n", "\r", " "):
            continue
        choice = items[selected][0]
        if choice == "back":
            return
        if choice == "sub_lang":
            updated = prompt_input(stdscr, palette, "subtitle lang", "Comma-separated subtitle languages", settings.sub_lang or "")
            settings.sub_lang = updated or None
            state.status_line = "Updated subtitle language selection."
            state.log(state.status_line)
            continue
        if choice == "remux_video":
            updated = prompt_input(stdscr, palette, "remux video", f"Container ({', '.join(REMUX_CONTAINERS)}) or blank", settings.remux_video or "")
            normalized = (updated or "").strip().lower()
            if normalized and normalized not in REMUX_CONTAINERS:
                state.status_line = f"Unsupported remux container: {normalized}"
                state.log(state.status_line)
                continue
            settings.remux_video = normalized or None
            state.status_line = "Updated remux container."
            state.log(state.status_line)
            continue
        selected_label = dict(items).get(choice, choice)
        setattr(settings, choice, not getattr(settings, choice))
        state.status_line = f"{selected_label} {'enabled' if getattr(settings, choice) else 'disabled'}."
        state.log(state.status_line)


def build_ui_plan(info: dict[str, Any], settings: DownloadSettings, quality: QualityOption, has_ffmpeg: bool) -> ResolvedPlan:
    return ResolvedPlan(
        platform_key=settings.platform,
        platform_label=get_platform(settings.platform).option.label,
        requested_url=settings.url,
        normalized_url=settings.url,
        mode=settings.mode,
        playlist=settings.playlist,
        quality=quality,
        output_path=preview_output_path(settings, info, quality),
        has_ffmpeg=has_ffmpeg,
        auth_description=settings.auth.describe(),
        policy_notes=platform_notes(settings.platform),
        option_preview={},
        detected_platform=infer_platform_from_url(settings.url),
    )


def show_formats_ui(stdscr: curses.window, palette: dict[str, int], info: dict[str, Any], state: AppState) -> None:
    show_text_modal(stdscr, palette, "formats", render_formats(info).splitlines())
    state.status_line = "Closed formats view."
    state.log(state.status_line)


def show_simulation_ui(
    stdscr: curses.window,
    palette: dict[str, int],
    info: dict[str, Any],
    settings: DownloadSettings,
    quality: QualityOption,
    has_ffmpeg: bool,
    state: AppState,
) -> None:
    plan = build_ui_plan(info, settings, quality, has_ffmpeg)
    show_text_modal(stdscr, palette, "simulation", render_simulation(plan, info).splitlines())
    state.status_line = "Closed simulation view."
    state.log(state.status_line)


def manage_watch_settings_ui(stdscr: curses.window, palette: dict[str, int], state: AppState) -> None:
    config = load_config()
    interval_text = f"{config.watch.interval:.2f}"
    auto = config.watch.auto
    preset = config.watch.preset or ""
    items = ["interval", "auto", "preset", "save", "back"]
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(52, min(width - 6, 86))
        box_height = 11
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        rows = [
            ("interval", interval_text),
            ("auto", "on" if auto else "off"),
            ("preset", preset or "none"),
            ("save", "write to config"),
            ("back", "return"),
        ]
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "watch settings", palette)
        for idx, (label, value) in enumerate(rows):
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(stdscr, top + 2 + idx, left + 2, f" {idx + 1}. {label:<12} {value}", attr, max_width=max(0, box_width - 4))
        safe_addstr(stdscr, top + box_height - 2, left + 2, "Enter edit/toggle | j/k move | q back", palette["muted"], max_width=max(0, box_width - 4))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(len(items) - 1, selected + 1)
            continue
        if key not in ("\n", "\r", " "):
            continue
        choice = items[selected]
        if choice == "interval":
            updated = prompt_input(stdscr, palette, "watch interval", "Polling interval in seconds", interval_text)
            if updated:
                try:
                    if float(updated) <= 0:
                        raise ValueError
                    interval_text = updated
                    state.status_line = "Updated watch polling interval."
                    state.log(state.status_line)
                except ValueError:
                    state.status_line = "Watch interval must be a positive number."
                    state.log(state.status_line)
            continue
        if choice == "auto":
            auto = not auto
            state.status_line = f"Watch auto-download {'enabled' if auto else 'disabled'}."
            state.log(state.status_line)
            continue
        if choice == "preset":
            updated = prompt_input(stdscr, palette, "watch preset", "Default preset for watch run/start (blank clears)", preset)
            preset = (updated or "").strip()
            state.status_line = "Updated watch preset."
            state.log(state.status_line)
            continue
        if choice == "save":
            save_config(
                config.__class__(
                    presets=config.presets,
                    watch=config.watch.__class__(interval=float(interval_text), auto=auto, preset=preset or None),
                    duplicates=config.duplicates,
                )
            )
            state.status_line = "Saved watch settings to config."
            state.log(state.status_line)
            return
        return


def manage_misc_ui(stdscr: curses.window, palette: dict[str, int], state: AppState) -> None:
    items = [
        ("history", "history", "recent downloads"),
        ("doctor", "doctor", "system checks"),
        ("watch", "watch", "watch defaults"),
        ("back", "back", "return to main menu"),
    ]
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(46, min(width - 6, 72))
        box_height = 10
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)

        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "misc", palette)
        safe_addstr(stdscr, top + 1, left + 2, "Open utility tools from inside the interactive UI.", palette["body"], max_width=max(0, box_width - 4))
        for idx, (_key, label, description) in enumerate(items):
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(
                stdscr,
                top + 3 + idx,
                left + 2,
                f" {idx + 1}. {label:<8} {shorten(description, box_width - 18)}",
                attr,
                max_width=max(0, box_width - 4),
            )
        safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k or arrows move | Enter select | q back", palette["muted"], max_width=max(0, box_width - 4))
        stdscr.refresh()

        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            state.status_line = "Closed misc menu."
            state.log(state.status_line)
            return
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(len(items) - 1, selected + 1)
            continue
        if isinstance(key, str) and key.isdigit() and "1" <= key <= str(len(items)):
            selected = int(key) - 1
            continue
        if key not in ("\n", "\r", " "):
            continue
        choice = items[selected][0]
        if choice == "history":
            show_history_ui(stdscr, palette, state)
            continue
        if choice == "doctor":
            show_doctor_ui(stdscr, palette, state)
            continue
        if choice == "watch":
            manage_watch_settings_ui(stdscr, palette, state)
            continue
        state.status_line = "Returned to main menu."
        state.log(state.status_line)
        return


def manage_source_group_ui(
    stdscr: curses.window,
    palette: dict[str, int],
    state: AppState,
    settings: DownloadSettings,
) -> tuple[str | None, str | None]:
    items = [
        ("platform", "platform", get_platform(settings.platform).option.label),
        ("preset", "preset", preset_label(settings)),
        ("source", "source", shorten(settings.url or "unset", 32)),
        ("auth", "auth", auth_label(settings)),
        ("back", "back", "return"),
    ]
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(56, min(width - 6, 90))
        box_height = 11
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "source", palette)
        for idx, (_key, label, value) in enumerate(items):
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(stdscr, top + 2 + idx, left + 2, f" {idx + 1}. {label:<10} {shorten(value, box_width - 20)}", attr, max_width=max(0, box_width - 4))
        safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k or arrows move | Enter select | q back", palette["muted"], max_width=max(0, box_width - 4))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return None, None
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(len(items) - 1, selected + 1)
            continue
        if key not in ("\n", "\r", " "):
            continue
        choice = items[selected][0]
        if choice == "platform":
            picked = select_platform_ui(stdscr, palette, state, settings.platform)
            if picked and picked != settings.platform:
                settings.platform = picked
                settings.url = ""
                settings.playlist_items = None
                settings.quality_index = 0
                state.status_line = f"Platform switched to {get_platform(picked).option.label}. Paste a new URL."
                return "reload", None
            continue
        if choice == "preset":
            selected_quality = show_preset_picker_ui(stdscr, palette, state, settings)
            if selected_quality is not None:
                settings.url = ""
                settings.playlist_items = None
                settings.quality_index = 0
                state.status_line = f'Applied preset "{settings.preset_name}". Paste a URL to continue.'
                return "reload", selected_quality
            continue
        if choice == "source":
            updated = prompt_input(stdscr, palette, "source", get_platform(settings.platform).option.url_prompt, settings.url)
            if updated and updated != settings.url:
                settings.url = updated
                settings.playlist_items = None
                settings.quality_index = 0
                align_platform_with_url(settings, state)
                state.status_line = "Loading metadata for the updated URL."
                state.log("URL updated. Reloading metadata.")
                return "reload", None
            continue
        if choice == "auth":
            previous_auth = auth_label(settings)
            edit_auth_ui(stdscr, palette, state, settings)
            if auth_label(settings) != previous_auth:
                state.status_line = "Auth changed. Reloading metadata."
                state.log(state.status_line)
                return "reload", None
            continue
        return None, None


def manage_download_group_ui(
    stdscr: curses.window,
    palette: dict[str, int],
    state: AppState,
    settings: DownloadSettings,
    info: dict[str, Any],
    options: list[QualityOption],
) -> str | None:
    while True:
        items = [
            ("playlist", "playlist", "enabled" if settings.playlist else "single item"),
            ("mode", "mode", "video+audio" if settings.mode == "video" else "audio-only"),
            ("quality", "quality", options[settings.quality_index].label),
        ]
        if is_playlist_info(info):
            items.insert(1, ("queue", "queue", playlist_queue_label(info, settings)))
        items.append(("back", "back", "return"))
        selected = 0
        while True:
            height, width = stdscr.getmaxyx()
            box_width = max(56, min(width - 6, 88))
            box_height = 10 if len(items) <= 4 else 11
            top = max(2, (height - box_height) // 2)
            left = max(3, (width - box_width) // 2)
            stdscr.erase()
            draw_box(stdscr, top, left, box_height, box_width, "download", palette)
            for idx, (_key, label, value) in enumerate(items):
                attr = palette["selected"] if idx == selected else palette["body"]
                safe_addstr(stdscr, top + 2 + idx, left + 2, f" {idx + 1}. {label:<10} {shorten(value, box_width - 20)}", attr, max_width=max(0, box_width - 4))
            safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k move | h/l cycle quality or mode | Enter select | q back", palette["muted"], max_width=max(0, box_width - 4))
            stdscr.refresh()
            key = stdscr.get_wch()
            if key in ("q", "Q", "\x1b"):
                return None
            if key in (curses.KEY_UP, "k"):
                selected = max(0, selected - 1)
                continue
            if key in (curses.KEY_DOWN, "j"):
                selected = min(len(items) - 1, selected + 1)
                continue
            selected_command = items[selected][0]
            if selected_command == "mode" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, "h", "l"):
                settings.mode = "audio" if settings.mode == "video" else "video"
                settings.quality_index = 0
                state.status_line = f"Mode switched to {settings.mode}."
                state.log(state.status_line)
                return None
            if selected_command == "quality" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, "h", "l"):
                delta = -1 if key in (curses.KEY_LEFT, "h") else 1
                settings.quality_index = cycle_index(settings.quality_index, delta, len(options))
                state.status_line = f"Quality set to {options[settings.quality_index].label}."
                state.log(state.status_line)
                return None
            if key not in ("\n", "\r", " "):
                continue
            if selected_command == "playlist":
                settings.playlist = not settings.playlist
                if not settings.playlist:
                    settings.playlist_items = None
                state.status_line = "Playlist downloads enabled." if settings.playlist else "Playlist downloads disabled."
                state.log(f"Playlist mode changed to {'enabled' if settings.playlist else 'single item'}.")
                if is_playlist_info(info) != settings.playlist:
                    state.status_line = "Playlist mode changed. Reloading metadata."
                    state.log("Reloading metadata to apply the new playlist mode.")
                    return "reload"
                return None
            if selected_command == "queue":
                manage_playlist_queue_ui(stdscr, palette, info, settings, state)
                return None
            if selected_command == "mode":
                settings.mode = "audio" if settings.mode == "video" else "video"
                settings.quality_index = 0
                state.status_line = f"Mode switched to {settings.mode}."
                state.log(state.status_line)
                return None
            if selected_command == "quality":
                settings.quality_index = cycle_index(settings.quality_index, 1, len(options))
                state.status_line = f"Quality set to {options[settings.quality_index].label}."
                state.log(state.status_line)
                return None
            return None


def manage_output_group_ui(stdscr: curses.window, palette: dict[str, int], state: AppState, settings: DownloadSettings) -> None:
    items = [
        ("output", "output", settings.output_dir),
        ("template", "template", settings.filename_template),
        ("extras", "extras", extras_summary(settings)),
        ("back", "back", "return"),
    ]
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(58, min(width - 6, 94))
        box_height = 10
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "output", palette)
        items[0] = ("output", "output", settings.output_dir)
        items[1] = ("template", "template", settings.filename_template)
        items[2] = ("extras", "extras", extras_summary(settings))
        for idx, (_key, label, value) in enumerate(items):
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(stdscr, top + 2 + idx, left + 2, f" {idx + 1}. {label:<10} {shorten(value, box_width - 20)}", attr, max_width=max(0, box_width - 4))
        safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k or arrows move | Enter select | q back", palette["muted"], max_width=max(0, box_width - 4))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(len(items) - 1, selected + 1)
            continue
        if key not in ("\n", "\r", " "):
            continue
        choice = items[selected][0]
        if choice == "output":
            updated = prompt_input(stdscr, palette, "output", "Folder for downloaded files", settings.output_dir)
            if updated:
                settings.output_dir = updated
                state.status_line = f"Output folder updated to {settings.output_dir}."
                state.log(state.status_line)
            continue
        if choice == "template":
            updated = prompt_input(stdscr, palette, "template", "yt-dlp filename template", settings.filename_template)
            if updated:
                settings.filename_template = updated
                state.status_line = "Filename template updated."
                state.log(f"Filename template set to {settings.filename_template}.")
            continue
        if choice == "extras":
            manage_extras_ui(stdscr, palette, state, settings)
            continue
        return


def manage_inspect_group_ui(
    stdscr: curses.window,
    palette: dict[str, int],
    state: AppState,
    info: dict[str, Any],
    settings: DownloadSettings,
    quality: QualityOption,
    has_ffmpeg: bool,
) -> None:
    items = [
        ("formats", "formats", str(len(info.get("formats") or []))),
        ("simulate", "simulate", "preview plan"),
        ("back", "back", "return"),
    ]
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        box_width = max(48, min(width - 6, 78))
        box_height = 9
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        stdscr.erase()
        draw_box(stdscr, top, left, box_height, box_width, "inspect", palette)
        for idx, (_key, label, value) in enumerate(items):
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(stdscr, top + 2 + idx, left + 2, f" {idx + 1}. {label:<10} {shorten(value, box_width - 20)}", attr, max_width=max(0, box_width - 4))
        safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k or arrows move | Enter select | q back", palette["muted"], max_width=max(0, box_width - 4))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return
        if key in (curses.KEY_UP, "k"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = min(len(items) - 1, selected + 1)
            continue
        if key not in ("\n", "\r", " "):
            continue
        choice = items[selected][0]
        if choice == "formats":
            show_formats_ui(stdscr, palette, info, state)
            continue
        if choice == "simulate":
            show_simulation_ui(stdscr, palette, info, settings, quality, has_ffmpeg, state)
            continue
        return


def select_platform_ui(stdscr: curses.window, palette: dict[str, int], state: AppState, current_platform: str | None = None) -> str | None:
    selected = 0
    picker_size = len(PLATFORMS) + 1
    if current_platform:
        for idx, policy in enumerate(PLATFORMS):
            if policy.option.key == current_platform:
                selected = idx
                break
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        box_width = min(width - 6, 92)
        box_height = min(height - 4, 18)
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)
        draw_box(stdscr, top, left, box_height, box_width, "platform", palette)
        safe_addstr(stdscr, top + 2, left + 2, "Select a source platform before loading the URL.", palette["body"])
        for idx, policy in enumerate(PLATFORMS):
            platform = policy.option
            attr = palette["selected"] if idx == selected else palette["body"]
            safe_addstr(stdscr, top + 4 + idx, left + 2, f" {idx + 1}. {platform.label:<10} {shorten(platform.description, box_width - 20)}", attr)
        misc_attr = palette["selected"] if selected == len(PLATFORMS) else palette["body"]
        safe_addstr(stdscr, top + 4 + len(PLATFORMS), left + 2, f" {len(PLATFORMS) + 1}. {'Misc':<10} history, doctor, watch", misc_attr)
        if selected < len(PLATFORMS):
            active = PLATFORMS[selected].option
            safe_addstr(stdscr, top + box_height - 4, left + 2, shorten(active.url_prompt, box_width - 4), palette["accent"])
            safe_addstr(stdscr, top + box_height - 3, left + 2, shorten("Supported: " + ", ".join(active.domains), box_width - 4), palette["muted"])
        else:
            safe_addstr(stdscr, top + box_height - 4, left + 2, shorten("Open interactive utility tools before choosing a platform.", box_width - 4), palette["accent"])
            safe_addstr(stdscr, top + box_height - 3, left + 2, shorten("Includes history, doctor, watch defaults, and a return path back here.", box_width - 4), palette["muted"])
        safe_addstr(stdscr, top + box_height - 2, left + 2, f"j/k or arrows move | Enter select | 1-{picker_size} jump | q cancel", palette["muted"])
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return None
        if key in (curses.KEY_UP, "k"):
            selected = (selected - 1) % picker_size
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = (selected + 1) % picker_size
            continue
        if isinstance(key, str) and key.isdigit() and "1" <= key <= str(picker_size):
            selected = int(key) - 1
            continue
        if key in ("\n", "\r", " "):
            if selected == len(PLATFORMS):
                manage_misc_ui(stdscr, palette, state)
                continue
            choice = PLATFORMS[selected].option.key
            state.log(f"Platform selected: {get_platform(choice).option.label}.")
            return choice


def fetch_info_with_ui(stdscr: curses.window, palette: dict[str, int], settings: DownloadSettings, state: AppState) -> dict[str, Any]:
    result: dict[str, Any] = {}
    error: list[Exception] = []
    spinner = ["[|]", "[/]", "[-]", "[\\]"]
    platform = get_platform(settings.platform).option

    def worker() -> None:
        try:
            result["info"] = extract_video_info(settings.url, settings.platform, settings.auth, playlist=settings.playlist)
        except Exception as exc:  # noqa: BLE001
            error.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    tick = 0
    state.log(f"Inspecting remote metadata for {platform.label}.")
    while thread.is_alive():
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        panel_width = min(width - 8, 84)
        panel_height = 12
        top = max(2, (height - panel_height) // 2)
        left = max(4, (width - panel_width) // 2)
        draw_box(stdscr, top, left, panel_height, panel_width, "fetch", palette)
        safe_addstr(stdscr, top + 2, left + 2, APP_NAME, palette["title"])
        safe_addstr(stdscr, top + 3, left + 2, APP_SUBTITLE, palette["muted"])
        safe_addstr(stdscr, top + 5, left + 2, f"{spinner[tick % len(spinner)]} resolving {platform.label} media via yt-dlp", palette["accent"])
        for idx, line in enumerate(wrap_lines(settings.url, panel_width - 4, 2)):
            safe_addstr(stdscr, top + 7 + idx, left + 2, line, palette["body"])
        safe_addstr(stdscr, top + 10, left + 2, "q is disabled while metadata is loading", palette["muted"])
        stdscr.refresh()
        time.sleep(0.08)
        tick += 1
    thread.join()
    if error:
        raise error[0]
    return result["info"]


def draw_progress_bar(stdscr: curses.window, y: int, x: int, width: int, percent: float, palette: dict[str, int]) -> None:
    filled = min(max(10, width), int((percent / 100.0) * max(10, width)))
    bar = "#" * filled + "." * max(0, max(10, width) - filled)
    safe_addstr(stdscr, y, x, bar, palette["accent"])


def draw_logs(stdscr: curses.window, top: int, left: int, height: int, width: int, title: str, logs: list[str], palette: dict[str, int]) -> None:
    draw_box(stdscr, top, left, height, width, title, palette)
    for idx, entry in enumerate(logs[-max(0, height - 2) :]):
        safe_addstr(stdscr, top + 1 + idx, left + 2, shorten(entry, width - 4), palette["body"], max_width=max(0, width - 4))


def draw_main_screen(stdscr: curses.window, palette: dict[str, int], state: AppState, info: dict[str, Any], settings: DownloadSettings, options: list[QualityOption], selected_index: int, has_ffmpeg: bool) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < MIN_HEIGHT or width < MIN_WIDTH:
        safe_addstr(stdscr, 1, 2, "Terminal is too small for the interactive interface.", palette["error"])
        safe_addstr(stdscr, 3, 2, f"Need at least {MIN_WIDTH}x{MIN_HEIGHT}. Resize or use --non-interactive.", palette["body"])
        stdscr.refresh()
        return
    platform = get_platform(settings.platform).option
    current_quality = options[settings.quality_index]
    mode_label = "video+audio" if settings.mode == "video" else "audio-only"
    playlist_label = "enabled" if settings.playlist else "single item"
    is_playlist = is_playlist_info(info)
    item_label = "entries" if is_playlist else "formats"
    item_value = str(info.get("playlist_count") or len(playlist_entries(info))) if is_playlist else str(len(info.get("formats") or []))
    command_items = build_command_items(info, settings, current_quality)
    safe_addstr(stdscr, 1, 2, APP_NAME, palette["title"])
    safe_addstr(stdscr, 1, 13, shorten(APP_SUBTITLE, max(0, width - 46)), palette["muted"], max_width=max(0, width - 46))
    safe_addstr(stdscr, 1, width - 30, f"ffmpeg={'ready' if has_ffmpeg else 'missing'}", palette["success"] if has_ffmpeg else palette["warning"])
    left_width = max(40, width // 2 - 2)
    right_width = width - left_width - 6
    top = 3
    left_content_width = max(0, left_width - 4)
    right_content_width = max(0, right_width - 4)
    title_lines = wrap_lines(info.get("title") or "Unknown title", left_content_width, 2)
    url_lines = wrap_lines(info.get("webpage_url") or settings.url, left_content_width, max(1, 3 - len(title_lines)))
    for idx, line in enumerate(title_lines):
        safe_addstr(stdscr, top + 1 + idx, 4, line, palette["heading"], max_width=left_content_width)
    url_top = top + 1 + len(title_lines)
    for idx, line in enumerate(url_lines):
        safe_addstr(stdscr, url_top + idx, 4, line, palette["muted"], max_width=left_content_width)
    info_rows = [
        ("platform", platform.label),
        ("preset", preset_label(settings)),
        ("creator", info.get("uploader") or info.get("channel") or info.get("uploader_id") or "Unknown creator"),
        ("duration", format_duration(info.get("duration"))),
        ("views", format_views(info.get("view_count"))),
        ("uploaded", normalize_upload_date(info.get("upload_date"))),
        (item_label, item_value),
        ("queue", playlist_queue_label(info, settings) if is_playlist else "n/a"),
        ("mode", mode_label),
        ("playlist", playlist_label),
        ("quality", current_quality.label),
        ("auth", auth_label(settings)),
        ("extras", extras_summary(settings)),
    ]
    info_required_height = 3 + len(title_lines) + len(url_lines) + len(info_rows)
    commands_required_height = len(command_items) + 5
    main_height = max(16, info_required_height, commands_required_height)
    draw_box(stdscr, top, 2, main_height, left_width, "source", palette)
    info_start = top + 1 + len(title_lines) + len(url_lines) + 1
    for idx, (label, value) in enumerate(info_rows):
        safe_addstr(
            stdscr,
            info_start + idx,
            4,
            f"{label:<10} {shorten(value, left_width - 16)}",
            palette["body"],
            max_width=left_content_width,
        )
    draw_box(stdscr, top, left_width + 4, main_height, right_width, "commands", palette)
    for idx, (_command_key, label, value, description) in enumerate(command_items):
        attr = palette["selected"] if idx == selected_index else palette["body"]
        safe_addstr(
            stdscr,
            top + 1 + idx,
            left_width + 6,
            f" {idx + 1}. {label:<8} {shorten(value, right_width - 16)}",
            attr,
            max_width=right_content_width,
        )
        if idx == selected_index:
            safe_addstr(
                stdscr,
                top + main_height - 4,
                left_width + 6,
                shorten(description, right_width - 4),
                palette["accent"],
                max_width=right_content_width,
            )
    logs_top = top + main_height + 1
    draw_logs(stdscr, logs_top, 2, height - logs_top - 4, width - 4, "activity", state.logs or ["[idle] waiting for input"], palette)
    safe_addstr(stdscr, height - 2, 2, shorten(state.status_line, width - 4), palette["accent"])
    safe_addstr(stdscr, height - 1, 2, "j/k or arrows move | Enter open | 1-6 jump | q quit", palette["muted"])
    stdscr.refresh()


def draw_download_screen(stdscr: curses.window, palette: dict[str, int], info: dict[str, Any], progress: DownloadProgress, state: AppState, settings: DownloadSettings) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    safe_addstr(stdscr, 1, 2, APP_NAME, palette["title"])
    safe_addstr(stdscr, 1, 13, "active transfer", palette["muted"])
    draw_box(stdscr, 3, 2, 12, width - 4, "download", palette)
    for idx, line in enumerate(wrap_lines(info.get("title") or "Unknown title", width - 8, 2)):
        safe_addstr(stdscr, 4 + idx, 4, line, palette["heading"])
    safe_addstr(stdscr, 6, 4, f"platform {get_platform(settings.platform).option.label}", palette["muted"])
    rows = [("stage", progress.stage), ("file", progress.file_name or "Preparing file"), ("written", f"{progress.downloaded} / {progress.total}"), ("speed", progress.speed), ("eta", progress.eta)]
    for idx, (label, value) in enumerate(rows):
        safe_addstr(stdscr, 7 + idx, 4, f"{label:<8} {shorten(value, width - 16)}", palette["body"])
    bar_width = min(width - 10, 72)
    draw_progress_bar(stdscr, 13, 4, bar_width, progress.percent, palette)
    safe_addstr(stdscr, 13, 6 + bar_width, f"{progress.percent:5.1f}%", palette["accent"])
    logs_top = 16
    draw_logs(stdscr, logs_top, 2, height - logs_top - 4, width - 4, "activity", state.logs, palette)
    if progress.complete:
        message = f"Completed: {progress.output_path}" if progress.success else f"Failed: {progress.error}"
        safe_addstr(stdscr, height - 2, 2, shorten(message, width - 4), palette["success"] if progress.success else palette["error"])
        footer = "Press N for another video or any other key to return." if progress.success else "Press any key to return to the main session."
        safe_addstr(stdscr, height - 1, 2, footer, palette["muted"])
    else:
        safe_addstr(stdscr, height - 2, 2, "yt-dlp is running. Use Ctrl-C to abort the process.", palette["warning"])
        safe_addstr(stdscr, height - 1, 2, "Status updates stream into the activity panel.", palette["muted"])
    stdscr.refresh()


def cycle_index(index: int, delta: int, size: int) -> int:
    return 0 if size <= 0 else (index + delta) % size


def align_platform_with_url(settings: DownloadSettings, state: AppState) -> None:
    detected = infer_platform_from_url(settings.url)
    if detected and detected != settings.platform:
        previous = get_platform(settings.platform).option.label if settings.platform else "Unknown"
        settings.platform = detected
        state.log(f"URL matched {get_platform(detected).option.label}. Switched platform from {previous}.")
        state.status_line = f"Detected {get_platform(detected).option.label} from the URL and updated the platform."
    if settings.platform == "youtube" and youtube_url_has_playlist(settings.url) and not settings.playlist:
        settings.playlist = True
        state.log("Detected a YouTube playlist URL. Enabled playlist mode automatically.")
        state.status_line = "Detected a YouTube playlist URL and enabled playlist mode."


def run_download_ui(stdscr: curses.window, palette: dict[str, int], info: dict[str, Any], settings: DownloadSettings, quality: QualityOption, state: AppState) -> tuple[str, str]:
    progress = DownloadProgress(stage="Booting")
    result: dict[str, str] = {}
    error: list[Exception] = []
    seen: set[str] = set()
    percent_bucket = -1

    def on_update(snapshot: DownloadProgress) -> None:
        nonlocal percent_bucket
        if snapshot.stage and snapshot.stage not in seen:
            state.log(f"stage={snapshot.stage.lower()}")
            seen.add(snapshot.stage)
        bucket = int(snapshot.percent // 10)
        if bucket > percent_bucket and bucket < 10:
            percent_bucket = bucket
            state.log(f"progress={snapshot.percent:0.0f}% speed={snapshot.speed} eta={snapshot.eta}")

    def worker() -> None:
        try:
            state.log(f"Starting {get_platform(settings.platform).option.label} transfer with profile {quality.label}.")
            result["path"] = download_media(settings, quality, progress=progress, quiet=True, on_update=on_update)
            progress.output_path = result["path"]
            progress.success = True
            state.log(f"Saved file to {result['path']}.")
        except Exception as exc:  # noqa: BLE001
            progress.error = str(exc)
            error.append(exc)
            state.log(f"Transfer failed: {exc}.")
            help_text = classify_platform_error(settings.platform, exc)
            if help_text is not None:
                state.log(help_text.summary)
                for hint in help_text.hints[:2]:
                    state.log(f"hint: {hint}")
        finally:
            progress.complete = True

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    stdscr.nodelay(True)
    while thread.is_alive():
        draw_download_screen(stdscr, palette, info, progress, state, settings)
        try:
            if stdscr.get_wch() == "\x03":
                raise KeyboardInterrupt
        except curses.error:
            pass
        time.sleep(0.08)
    thread.join()
    stdscr.nodelay(False)
    draw_download_screen(stdscr, palette, info, progress, state, settings)
    key = stdscr.get_wch()
    if error:
        raise error[0]
    if progress.success and key in ("n", "N"):
        return result["path"], "new"
    return result["path"], "return"


def interactive_app(stdscr: curses.window, args: Any) -> int:
    palette = init_colors()
    curses.curs_set(0)
    stdscr.keypad(True)
    state = AppState()
    has_ffmpeg = ffmpeg_available()
    state.log("Session booted.")
    state.log(f"ffmpeg={'ready' if has_ffmpeg else 'missing'}")
    initial_platform = args.platform if args.platform != "auto" else (infer_platform_from_url(args.url or "") or "")
    settings = DownloadSettings(
        platform=initial_platform,
        url=args.url or "",
        mode="audio" if args.audio_only else "video",
        playlist=args.playlist,
        output_dir=args.output_dir,
        filename_template=args.filename_template,
        auth=args.auth,
        preset_name=getattr(args, "preset", None),
        force=bool(getattr(args, "force", False)),
        write_subs=bool(getattr(args, "write_subs", False)),
        write_auto_subs=bool(getattr(args, "write_auto_subs", False)),
        sub_lang=getattr(args, "sub_lang", None),
        embed_subs=bool(getattr(args, "embed_subs", False)),
        write_thumbnail=bool(getattr(args, "write_thumbnail", False)),
        embed_thumbnail=bool(getattr(args, "embed_thumbnail", False)),
        write_info_json=bool(getattr(args, "write_info_json", False)),
        embed_metadata=bool(getattr(args, "embed_metadata", False)),
        split_chapters=bool(getattr(args, "split_chapters", False)),
        remux_video=getattr(args, "remux_video", None),
    )
    pending_quality = args.quality
    selected_index = 0
    while True:
        if not settings.platform:
            selected = select_platform_ui(stdscr, palette, state)
            if not selected:
                return 0
            settings.platform = selected
            settings.url = ""
            settings.quality_index = 0
            state.status_line = f"{get_platform(selected).option.label} selected. Paste a URL to continue."
        if not settings.url:
            entered = prompt_input(stdscr, palette, APP_NAME, get_platform(settings.platform).option.url_prompt, settings.url)
            if not entered:
                settings.platform = ""
                continue
            settings.url = entered
            align_platform_with_url(settings, state)
            state.log("Accepted URL input.")
        try:
            info = fetch_info_with_ui(stdscr, palette, settings, state)
            sync_playlist_queue(settings, info)
            state.status_line = "Metadata resolved. Review the stream plan before running."
            state.log(f"Loaded metadata for {info.get('title') or settings.url}.")
            if is_playlist_info(info):
                entry_count = info.get("playlist_count") or len(playlist_entries(info))
                state.log(f"Playlist detected with {entry_count} entries. Unavailable items will be skipped.")
        except Exception as exc:  # noqa: BLE001
            state.status_line = f"Could not resolve that URL: {exc}"
            state.log(f"Metadata fetch failed: {exc}.")
            help_text = classify_platform_error(settings.platform, exc)
            if help_text is not None:
                state.log(help_text.summary)
                for hint in help_text.hints[:2]:
                    state.log(f"hint: {hint}")
            settings.url = ""
            continue
        video_options, audio_options = build_quality_options(info, has_ffmpeg, settings.platform)
        options = video_options if settings.mode == "video" else audio_options
        settings.quality_index = match_quality(options, pending_quality) if pending_quality else min(settings.quality_index, len(options) - 1)
        pending_quality = ""
        while True:
            options = video_options if settings.mode == "video" else audio_options
            settings.quality_index = min(settings.quality_index, len(options) - 1)
            command_items = build_command_items(info, settings, options[settings.quality_index])
            selected_index = min(selected_index, len(command_items) - 1)
            draw_main_screen(stdscr, palette, state, info, settings, options, selected_index, has_ffmpeg)
            key = stdscr.get_wch()
            if key in ("q", "Q"):
                return 0
            if key in (curses.KEY_UP, "k"):
                selected_index = cycle_index(selected_index, -1, len(command_items))
                continue
            if key in (curses.KEY_DOWN, "j"):
                selected_index = cycle_index(selected_index, 1, len(command_items))
                continue
            if isinstance(key, str) and key.isdigit() and "1" <= key <= "9":
                selected_index = min(int(key) - 1, len(command_items) - 1)
                continue
            selected_command = command_items[selected_index][0]
            if key not in ("\n", "\r"):
                continue
            if selected_command == "source_group":
                action, selected_quality = manage_source_group_ui(stdscr, palette, state, settings)
                if selected_quality:
                    pending_quality = selected_quality
                if action == "reload":
                    break
                continue
            if selected_command == "download_group":
                action = manage_download_group_ui(stdscr, palette, state, settings, info, options)
                if action == "reload":
                    break
                continue
            if selected_command == "output_group":
                manage_output_group_ui(stdscr, palette, state, settings)
                continue
            if selected_command == "inspect_group":
                manage_inspect_group_ui(stdscr, palette, state, info, settings, options[settings.quality_index], has_ffmpeg)
                continue
            if selected_command == "run":
                if is_playlist_info(info) and not settings.playlist_items:
                    state.status_line = "The playlist queue is empty. Restore at least one entry before running."
                    state.log(state.status_line)
                    continue
                duplicate = None
                if not settings.force:
                    extractor_key, media_id = extract_media_identity(info)
                    duplicate = latest_duplicate(normalize_url(settings.platform, settings.url), extractor_key=extractor_key, media_id=media_id)
                if duplicate is not None:
                    confirmed = prompt_confirm(
                        stdscr,
                        palette,
                        "duplicate detected",
                        [
                            f"Downloaded before: {duplicate.downloaded_at}",
                            duplicate.output_path,
                        ],
                        "Download again?",
                    )
                    if not confirmed:
                        state.status_line = "Duplicate download cancelled."
                        state.log(state.status_line)
                        continue
                try:
                    output_path, post_action = run_download_ui(stdscr, palette, info, settings, options[settings.quality_index], state)
                    record_download(settings, options[settings.quality_index], settings.url, output_path, info=info)
                    state.status_line = f"Saved to {output_path}"
                    if post_action == "new":
                        settings.url = ""
                        settings.quality_index = 0
                        state.status_line = "Paste another URL to start the next download."
                        state.log("Ready for another download.")
                        break
                except Exception as exc:  # noqa: BLE001
                    state.status_line = f"Download failed: {exc}"
                continue
            if selected_command == "quit":
                return 0
