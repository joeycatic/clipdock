from __future__ import annotations

import textwrap
import threading
import time
from typing import Any

import curses

from .downloader import (
    build_quality_options,
    download_media,
    extract_video_info,
    ffmpeg_available,
    format_duration,
    format_views,
    match_quality,
    normalize_upload_date,
    shorten,
)
from .models import APP_NAME, APP_SUBTITLE, AppState, DownloadProgress, DownloadSettings, MIN_HEIGHT, MIN_WIDTH, QualityOption
from .platforms import PLATFORMS, classify_platform_error, get_platform, infer_platform_from_url, youtube_url_has_playlist


def safe_addstr(window: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = window.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    max_len = max(0, width - x - 1)
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
    safe_addstr(window, top, left + 2, f"[ {title} ]", palette["heading"])


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


def build_command_items(info: dict[str, Any], settings: DownloadSettings, current_quality: QualityOption) -> list[tuple[str, str, str, str]]:
    mode_label = "video+audio" if settings.mode == "video" else "audio-only"
    playlist_label = "enabled" if settings.playlist else "single item"
    items = [
        ("platform", "platform", get_platform(settings.platform).option.label, "switch source platform and restart the URL step"),
        ("source", "source", settings.url or "unset", "edit the active URL"),
        ("playlist", "playlist", playlist_label, "toggle playlist downloads for playlist-capable sources"),
    ]
    if is_playlist_info(info):
        items.append(("queue", "queue", playlist_queue_label(info, settings), "review playlist entries and remove items from the queue"))
    items.extend(
        [
            ("mode", "mode", mode_label, "toggle video+audio vs audio-only"),
            ("quality", "quality", current_quality.label, current_quality.description),
            ("output", "output", settings.output_dir, "change the target folder"),
            ("template", "template", settings.filename_template, "change the yt-dlp naming template"),
            ("run", "run", "start download", "begin transfer with current settings"),
            ("quit", "quit", "exit", "leave the downloader"),
        ]
    )
    return items


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


def select_platform_ui(stdscr: curses.window, palette: dict[str, int], state: AppState, current_platform: str | None = None) -> str | None:
    selected = 0
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
        active = PLATFORMS[selected].option
        safe_addstr(stdscr, top + box_height - 4, left + 2, shorten(active.url_prompt, box_width - 4), palette["accent"])
        safe_addstr(stdscr, top + box_height - 3, left + 2, shorten("Supported: " + ", ".join(active.domains), box_width - 4), palette["muted"])
        safe_addstr(stdscr, top + box_height - 2, left + 2, "j/k or arrows move | Enter select | 1-6 jump | q cancel", palette["muted"])
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("q", "Q", "\x1b"):
            return None
        if key in (curses.KEY_UP, "k"):
            selected = (selected - 1) % len(PLATFORMS)
            continue
        if key in (curses.KEY_DOWN, "j"):
            selected = (selected + 1) % len(PLATFORMS)
            continue
        if isinstance(key, str) and key.isdigit() and "1" <= key <= str(len(PLATFORMS)):
            selected = int(key) - 1
            continue
        if key in ("\n", "\r", " "):
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
        safe_addstr(stdscr, top + 1 + idx, left + 2, shorten(entry, width - 4), palette["body"])


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
    safe_addstr(stdscr, 1, 13, APP_SUBTITLE, palette["muted"])
    safe_addstr(stdscr, 1, width - 30, f"ffmpeg={'ready' if has_ffmpeg else 'missing'}", palette["success"] if has_ffmpeg else palette["warning"])
    left_width = max(40, width // 2 - 2)
    right_width = width - left_width - 6
    top = 3
    main_height = 16
    draw_box(stdscr, top, 2, main_height, left_width, "source", palette)
    safe_addstr(stdscr, top + 1, 4, info.get("title") or "Unknown title", palette["heading"])
    for idx, line in enumerate(wrap_lines(info.get("webpage_url") or settings.url, left_width - 4, 2)):
        safe_addstr(stdscr, top + 2 + idx, 4, line, palette["muted"])
    info_rows = [
        ("platform", platform.label),
        ("creator", info.get("uploader") or info.get("channel") or info.get("uploader_id") or "Unknown creator"),
        ("duration", format_duration(info.get("duration"))),
        ("views", format_views(info.get("view_count"))),
        ("uploaded", normalize_upload_date(info.get("upload_date"))),
        (item_label, item_value),
        ("queue", playlist_queue_label(info, settings) if is_playlist else "n/a"),
        ("mode", mode_label),
        ("playlist", playlist_label),
        ("quality", current_quality.label),
    ]
    for idx, (label, value) in enumerate(info_rows):
        safe_addstr(stdscr, top + 5 + idx, 4, f"{label:<10} {shorten(value, left_width - 16)}", palette["body"])
    draw_box(stdscr, top, left_width + 4, main_height, right_width, "commands", palette)
    for idx, (_command_key, label, value, description) in enumerate(command_items):
        attr = palette["selected"] if idx == selected_index else palette["body"]
        safe_addstr(stdscr, top + 1 + idx, left_width + 6, f" {idx + 1}. {label:<8} {shorten(value, right_width - 16)}", attr)
        if idx == selected_index:
            safe_addstr(stdscr, top + main_height - 4, left_width + 6, shorten(description, right_width - 4), palette["accent"])
    logs_top = top + main_height + 1
    draw_logs(stdscr, logs_top, 2, height - logs_top - 4, width - 4, "activity", state.logs or ["[idle] waiting for input"], palette)
    safe_addstr(stdscr, height - 2, 2, shorten(state.status_line, width - 4), palette["accent"])
    jump_hint = "1-9 jump" if len(command_items) < 10 else "1-9,0 jump"
    safe_addstr(stdscr, height - 1, 2, f"j/k or arrows move | h/l switch | Enter edit/run | {jump_hint} | q quit", palette["muted"])
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
                selected_index = int(key) - 1
                continue
            if key == "0" and len(command_items) >= 10:
                selected_index = 9
                continue
            selected_command = command_items[selected_index][0]
            if selected_command == "mode" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, "h", "l", "\n", "\r", " "):
                settings.mode = "audio" if settings.mode == "video" else "video"
                settings.quality_index = 0
                state.status_line = f"Mode switched to {settings.mode}."
                state.log(f"Mode changed to {settings.mode}.")
                continue
            if selected_command == "quality" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, "h", "l", "\n", "\r"):
                delta = -1 if key in (curses.KEY_LEFT, "h") else 1
                settings.quality_index = cycle_index(settings.quality_index, delta, len(options))
                state.status_line = f"Quality set to {options[settings.quality_index].label}."
                state.log(f"Quality changed to {options[settings.quality_index].label}.")
                continue
            if key not in ("\n", "\r"):
                continue
            if selected_command == "platform":
                selected = select_platform_ui(stdscr, palette, state, settings.platform)
                if selected and selected != settings.platform:
                    settings.platform = selected
                    settings.url = ""
                    settings.playlist_items = None
                    settings.quality_index = 0
                    state.status_line = f"Platform switched to {get_platform(selected).option.label}. Paste a new URL."
                    break
                continue
            if selected_command == "source":
                updated = prompt_input(stdscr, palette, "source", get_platform(settings.platform).option.url_prompt, settings.url)
                if updated and updated != settings.url:
                    settings.url = updated
                    settings.playlist_items = None
                    settings.quality_index = 0
                    align_platform_with_url(settings, state)
                    state.status_line = "Loading metadata for the updated URL."
                    state.log("URL updated. Reloading metadata.")
                    break
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
                    break
                continue
            if selected_command == "queue":
                manage_playlist_queue_ui(stdscr, palette, info, settings, state)
                continue
            if selected_command == "mode":
                settings.mode = "audio" if settings.mode == "video" else "video"
                settings.quality_index = 0
                state.status_line = f"Mode switched to {settings.mode}."
                state.log(f"Mode changed to {settings.mode}.")
                continue
            if selected_command == "quality":
                settings.quality_index = cycle_index(settings.quality_index, 1, len(options))
                state.status_line = f"Quality set to {options[settings.quality_index].label}."
                state.log(f"Quality changed to {options[settings.quality_index].label}.")
                continue
            if selected_command == "output":
                updated = prompt_input(stdscr, palette, "output", "Folder for downloaded files", settings.output_dir)
                if updated:
                    settings.output_dir = updated
                    state.status_line = f"Output folder updated to {settings.output_dir}."
                    state.log(f"Output folder set to {settings.output_dir}.")
                continue
            if selected_command == "template":
                updated = prompt_input(stdscr, palette, "template", "yt-dlp filename template", settings.filename_template)
                if updated:
                    settings.filename_template = updated
                    state.status_line = "Filename template updated."
                    state.log(f"Filename template set to {settings.filename_template}.")
                continue
            if selected_command == "run":
                if is_playlist_info(info) and not settings.playlist_items:
                    state.status_line = "The playlist queue is empty. Restore at least one entry before running."
                    state.log(state.status_line)
                    continue
                try:
                    output_path, post_action = run_download_ui(stdscr, palette, info, settings, options[settings.quality_index], state)
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
