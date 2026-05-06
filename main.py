from __future__ import annotations

import argparse
import curses
import shutil
import sys
import textwrap
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yt_dlp


APP_NAME = "clipdock"
APP_SUBTITLE = "terminal media downloader"
DEFAULT_TEMPLATE = "%(title)s.%(ext)s"
DEFAULT_OUTPUT_DIR = str(Path.home() / "Downloads")
ARGPARSE_TEMPLATE_HELP = DEFAULT_TEMPLATE.replace("%", "%%")
MIN_HEIGHT = 28
MIN_WIDTH = 96


@dataclass(frozen=True)
class PlatformOption:
    key: str
    label: str
    description: str
    url_prompt: str
    domains: tuple[str, ...]


PLATFORMS = [
    PlatformOption(
        key="youtube",
        label="YouTube",
        description="Videos, shorts, music, public posts",
        url_prompt="Paste a YouTube URL",
        domains=("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"),
    ),
    PlatformOption(
        key="tiktok",
        label="TikTok",
        description="Videos and share links",
        url_prompt="Paste a TikTok URL",
        domains=("tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com"),
    ),
    PlatformOption(
        key="reddit",
        label="Reddit",
        description="Posts, hosted videos, v.redd.it",
        url_prompt="Paste a Reddit post URL",
        domains=("reddit.com", "www.reddit.com", "old.reddit.com", "v.redd.it"),
    ),
    PlatformOption(
        key="instagram",
        label="Instagram",
        description="Posts, reels, stories, highlights",
        url_prompt="Paste an Instagram post, reel, or story URL",
        domains=("instagram.com", "www.instagram.com"),
    ),
    PlatformOption(
        key="x",
        label="X",
        description="x.com and twitter.com posts",
        url_prompt="Paste an X or Twitter post URL",
        domains=("x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"),
    ),
    PlatformOption(
        key="pinterest",
        label="Pinterest",
        description="Pins and video pins",
        url_prompt="Paste a Pinterest pin URL",
        domains=("pinterest.com", "www.pinterest.com", "pin.it"),
    ),
]
PLATFORM_MAP = {platform.key: platform for platform in PLATFORMS}


@dataclass
class QualityOption:
    key: str
    label: str
    description: str
    format_selector: str
    postprocessors: list[dict[str, Any]] = field(default_factory=list)
    final_extension: str | None = None


@dataclass
class DownloadSettings:
    platform: str = ""
    url: str = ""
    mode: str = "video"
    output_dir: str = DEFAULT_OUTPUT_DIR
    filename_template: str = DEFAULT_TEMPLATE
    quality_index: int = 0


@dataclass
class DownloadProgress:
    stage: str = "Queued"
    percent: float = 0.0
    speed: str = "--"
    eta: str = "--"
    downloaded: str = "0 B"
    total: str = "?"
    file_name: str = ""
    complete: bool = False
    success: bool = False
    error: str | None = None
    output_path: str | None = None


@dataclass
class AppState:
    status_line: str = "Select a platform, paste a URL, review the stream plan, then run the download."
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        self.logs = self.logs[-10:]


class NullLogger:
    def debug(self, msg: str) -> None:
        return

    def warning(self, msg: str) -> None:
        return

    def error(self, msg: str) -> None:
        return


def get_platform(platform_key: str) -> PlatformOption:
    return PLATFORM_MAP[platform_key]


def infer_platform_from_url(url: str) -> str | None:
    lowered = url.strip().lower()
    if not lowered:
        return None
    for platform in PLATFORMS:
        if any(domain in lowered for domain in platform.domains):
            return platform.key
    return None


def human_bytes(value: float | int | None) -> str:
    if not value:
        return "0 B"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} PB"


def human_seconds(value: float | int | None) -> str:
    if value is None:
        return "--"
    seconds = max(0, int(value))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def format_duration(value: int | None) -> str:
    return human_seconds(value) if value else "Unknown"


def format_views(value: int | None) -> str:
    if not value:
        return "Unknown"
    return f"{value:,}"


def normalize_upload_date(value: str | None) -> str:
    if not value or len(value) != 8 or not value.isdigit():
        return "Unknown"
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def shorten(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


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
    horizontal = "-" * max(0, width - 2)
    safe_addstr(window, top, left, "+" + horizontal + "+", palette["border"])
    for row in range(top + 1, top + height - 1):
        safe_addstr(window, row, left, "|", palette["border"])
        safe_addstr(window, row, left + width - 1, "|", palette["border"])
    safe_addstr(window, top + height - 1, left, "+" + horizontal + "+", palette["border"])
    if title:
        safe_addstr(window, top, left + 2, shorten(f" {title} ", width - 4), palette["heading"])


def wrap_lines(value: str, width: int, max_lines: int) -> list[str]:
    if width <= 0 or max_lines <= 0:
        return []
    wrapped = textwrap.wrap(value, width=width) or [""]
    if len(wrapped) <= max_lines:
        return wrapped
    visible = wrapped[:max_lines]
    visible[-1] = shorten(visible[-1], width)
    return visible


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_video_info(url: str) -> dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "logger": NullLogger(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)


def build_quality_options(info: dict[str, Any], has_ffmpeg: bool) -> tuple[list[QualityOption], list[QualityOption]]:
    formats = info.get("formats") or []
    heights: dict[int, dict[str, Any]] = {}
    for fmt in formats:
        if fmt.get("vcodec") == "none":
            continue
        height = fmt.get("height")
        if not height:
            continue
        bucket = heights.setdefault(height, {"exts": set(), "fps": 0})
        ext = fmt.get("ext")
        if ext:
            bucket["exts"].add(ext)
        bucket["fps"] = max(bucket["fps"], int(fmt.get("fps") or 0))

    video_options = [
        QualityOption(
            key="best",
            label="Best available",
            description="Top quality stream plan offered by the source",
            format_selector="bestvideo+bestaudio/best" if has_ffmpeg else "best",
        )
    ]
    for height in sorted(heights.keys(), reverse=True)[:8]:
        details = heights[height]
        container_hint = "/".join(sorted(details["exts"])) if details["exts"] else "auto"
        fps_hint = f" @ {details['fps']}fps" if details["fps"] else ""
        selector = (
            f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
            if has_ffmpeg
            else f"best[height<={height}]/best"
        )
        video_options.append(
            QualityOption(
                key=f"{height}p",
                label=f"{height}p",
                description=f"Cap output at {height}p{fps_hint} | {container_hint}",
                format_selector=selector,
            )
        )

    audio_options = [
        QualityOption(
            key="best-audio",
            label="Best audio",
            description="Keep the best source audio without transcoding",
            format_selector="bestaudio/best",
        )
    ]
    if has_ffmpeg:
        audio_options.extend(
            [
                QualityOption(
                    key="mp3",
                    label="MP3 320k",
                    description="Transcode the best source audio to MP3",
                    format_selector="bestaudio/best",
                    postprocessors=[
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "320",
                        }
                    ],
                    final_extension="mp3",
                ),
                QualityOption(
                    key="m4a",
                    label="M4A",
                    description="Transcode the best source audio to M4A",
                    format_selector="bestaudio/best",
                    postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
                    final_extension="m4a",
                ),
            ]
        )
    return video_options, audio_options


def progress_hook_factory(
    progress: DownloadProgress,
    on_update: Callable[[DownloadProgress], None] | None = None,
) -> Callable[[dict[str, Any]], None]:
    def hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        filename = data.get("filename")
        if filename:
            progress.file_name = Path(filename).name

        if status == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            if total:
                progress.percent = min(100.0, (downloaded / total) * 100)
                progress.total = human_bytes(total)
            progress.downloaded = human_bytes(downloaded)
            progress.speed = f"{human_bytes(data.get('speed'))}/s" if data.get("speed") else "--"
            progress.eta = human_seconds(data.get("eta"))
            progress.stage = "Downloading"
        elif status == "finished":
            progress.percent = 100.0
            progress.eta = "0:00"
            progress.stage = "Finalizing"

        if on_update is not None:
            on_update(progress)

    return hook


def postprocessor_hook_factory(
    progress: DownloadProgress,
    on_update: Callable[[DownloadProgress], None] | None = None,
) -> Callable[[dict[str, Any]], None]:
    def hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        postprocessor = data.get("postprocessor", "processing")
        if status == "started":
            progress.stage = f"{postprocessor}..."
        elif status == "finished":
            progress.stage = "Wrapping up"

        if on_update is not None:
            on_update(progress)

    return hook


def resolve_output_path(ydl: yt_dlp.YoutubeDL, info: dict[str, Any], quality: QualityOption) -> str:
    prepared = Path(ydl.prepare_filename(info))
    if quality.final_extension:
        return str(prepared.with_suffix(f".{quality.final_extension}"))
    return str(prepared)


def download_media(
    settings: DownloadSettings,
    quality: QualityOption,
    progress: DownloadProgress | None = None,
    quiet: bool = True,
    on_update: Callable[[DownloadProgress], None] | None = None,
) -> str:
    output_dir = Path(settings.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "outtmpl": str(output_dir / settings.filename_template),
        "format": quality.format_selector,
        "noplaylist": True,
        "quiet": quiet,
        "no_warnings": quiet,
    }
    if quiet:
        options["logger"] = NullLogger()
    if quality.postprocessors:
        options["postprocessors"] = quality.postprocessors
    if progress is not None:
        options["progress_hooks"] = [progress_hook_factory(progress, on_update=on_update)]
        options["postprocessor_hooks"] = [postprocessor_hook_factory(progress, on_update=on_update)]

    with yt_dlp.YoutubeDL(options) as ydl:
        result = ydl.extract_info(settings.url, download=True)
        return resolve_output_path(ydl, result, quality)


def prompt_input(
    stdscr: curses.window,
    palette: dict[str, int],
    title: str,
    prompt: str,
    initial_value: str = "",
) -> str | None:
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

        cursor_x = left + 3 + min(cursor - start, max(0, input_width - 1))
        stdscr.move(top + 4, cursor_x)
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


def select_platform_ui(
    stdscr: curses.window,
    palette: dict[str, int],
    state: AppState,
    current_platform: str | None = None,
) -> str | None:
    selected = 0
    if current_platform in PLATFORM_MAP:
        selected = next(idx for idx, platform in enumerate(PLATFORMS) if platform.key == current_platform)

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        box_width = min(width - 6, 92)
        box_height = min(height - 4, 18)
        top = max(2, (height - box_height) // 2)
        left = max(3, (width - box_width) // 2)

        draw_box(stdscr, top, left, box_height, box_width, "platform", palette)
        safe_addstr(stdscr, top + 2, left + 2, "Select a source platform before loading the URL.", palette["body"])

        for idx, platform in enumerate(PLATFORMS):
            attr = palette["selected"] if idx == selected else palette["body"]
            line = f" {idx + 1}. {platform.label:<10} {shorten(platform.description, box_width - 20)}"
            safe_addstr(stdscr, top + 4 + idx, left + 2, line, attr)

        active = PLATFORMS[selected]
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
            choice = PLATFORMS[selected].key
            state.log(f"Platform selected: {get_platform(choice).label}.")
            return choice


def fetch_info_with_ui(stdscr: curses.window, palette: dict[str, int], url: str, state: AppState, platform_key: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    error: list[Exception] = []
    spinner = ["[|]", "[/]", "[-]", "[\\]"]
    platform = get_platform(platform_key)

    def worker() -> None:
        try:
            result["info"] = extract_video_info(url)
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
        for idx, line in enumerate(wrap_lines(url, panel_width - 4, 2)):
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
    inner_width = max(10, width)
    filled = min(inner_width, int((percent / 100.0) * inner_width))
    bar = "#" * filled + "." * max(0, inner_width - filled)
    safe_addstr(stdscr, y, x, bar, palette["accent"])


def draw_logs(
    stdscr: curses.window,
    top: int,
    left: int,
    height: int,
    width: int,
    title: str,
    logs: list[str],
    palette: dict[str, int],
) -> None:
    draw_box(stdscr, top, left, height, width, title, palette)
    usable_lines = max(0, height - 2)
    visible = logs[-usable_lines:]
    for idx, entry in enumerate(visible):
        safe_addstr(stdscr, top + 1 + idx, left + 2, shorten(entry, width - 4), palette["body"])


def draw_main_screen(
    stdscr: curses.window,
    palette: dict[str, int],
    state: AppState,
    info: dict[str, Any],
    settings: DownloadSettings,
    options: list[QualityOption],
    selected_index: int,
    has_ffmpeg: bool,
) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < MIN_HEIGHT or width < MIN_WIDTH:
        safe_addstr(stdscr, 1, 2, "Terminal is too small for the interactive interface.", palette["error"])
        safe_addstr(stdscr, 3, 2, f"Need at least {MIN_WIDTH}x{MIN_HEIGHT}. Resize or use --non-interactive.", palette["body"])
        stdscr.refresh()
        return

    platform = get_platform(settings.platform)
    title = info.get("title") or "Unknown title"
    uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id") or "Unknown creator"
    duration = format_duration(info.get("duration"))
    views = format_views(info.get("view_count"))
    upload_date = normalize_upload_date(info.get("upload_date"))
    current_quality = options[settings.quality_index]
    mode_label = "video+audio" if settings.mode == "video" else "audio-only"
    format_count = len(info.get("formats") or [])

    safe_addstr(stdscr, 1, 2, APP_NAME, palette["title"])
    safe_addstr(stdscr, 1, 13, APP_SUBTITLE, palette["muted"])
    safe_addstr(stdscr, 1, width - 30, f"ffmpeg={'ready' if has_ffmpeg else 'missing'}", palette["success"] if has_ffmpeg else palette["warning"])

    left_width = max(40, width // 2 - 2)
    right_width = width - left_width - 6
    top = 3
    main_height = 16

    draw_box(stdscr, top, 2, main_height, left_width, "source", palette)
    safe_addstr(stdscr, top + 1, 4, title, palette["heading"])
    for idx, line in enumerate(wrap_lines(info.get("webpage_url") or settings.url, left_width - 4, 2)):
        safe_addstr(stdscr, top + 2 + idx, 4, line, palette["muted"])

    info_rows = [
        ("platform", platform.label),
        ("creator", uploader),
        ("duration", duration),
        ("views", views),
        ("uploaded", upload_date),
        ("formats", str(format_count)),
        ("mode", mode_label),
        ("quality", current_quality.label),
    ]
    for idx, (label, value) in enumerate(info_rows):
        safe_addstr(stdscr, top + 5 + idx, 4, f"{label:<10} {shorten(value, left_width - 16)}", palette["body"])

    draw_box(stdscr, top, left_width + 4, main_height, right_width, "commands", palette)
    items = [
        ("platform", platform.label, "switch source platform and restart the URL step"),
        ("source", settings.url or "unset", "edit the active URL"),
        ("mode", mode_label, "toggle video+audio vs audio-only"),
        ("quality", current_quality.label, current_quality.description),
        ("output", settings.output_dir, "change the target folder"),
        ("template", settings.filename_template, "change the yt-dlp naming template"),
        ("run", "start download", "begin transfer with current settings"),
        ("quit", "exit", "leave the downloader"),
    ]
    for idx, (label, value, description) in enumerate(items):
        attr = palette["selected"] if idx == selected_index else palette["body"]
        safe_addstr(stdscr, top + 1 + idx, left_width + 6, f" {idx + 1}. {label:<8} {shorten(value, right_width - 16)}", attr)
        if idx == selected_index:
            safe_addstr(stdscr, top + 11, left_width + 6, shorten(description, right_width - 4), palette["accent"])

    logs_top = top + main_height + 1
    logs_height = height - logs_top - 4
    draw_logs(stdscr, logs_top, 2, logs_height, width - 4, "activity", state.logs or ["[idle] waiting for input"], palette)

    safe_addstr(stdscr, height - 2, 2, shorten(state.status_line, width - 4), palette["accent"])
    safe_addstr(stdscr, height - 1, 2, "j/k or arrows move | h/l switch | Enter edit/run | 1-8 jump | q quit", palette["muted"])
    stdscr.refresh()


def draw_download_screen(
    stdscr: curses.window,
    palette: dict[str, int],
    info: dict[str, Any],
    progress: DownloadProgress,
    state: AppState,
    settings: DownloadSettings,
) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    title = info.get("title") or "Unknown title"
    platform = get_platform(settings.platform)

    safe_addstr(stdscr, 1, 2, APP_NAME, palette["title"])
    safe_addstr(stdscr, 1, 13, "active transfer", palette["muted"])

    draw_box(stdscr, 3, 2, 12, width - 4, "download", palette)
    for idx, line in enumerate(wrap_lines(title, width - 8, 2)):
        safe_addstr(stdscr, 4 + idx, 4, line, palette["heading"])
    safe_addstr(stdscr, 6, 4, f"platform {platform.label}", palette["muted"])

    rows = [
        ("stage", progress.stage),
        ("file", progress.file_name or "Preparing file"),
        ("written", f"{progress.downloaded} / {progress.total}"),
        ("speed", progress.speed),
        ("eta", progress.eta),
    ]
    for idx, (label, value) in enumerate(rows):
        safe_addstr(stdscr, 7 + idx, 4, f"{label:<8} {shorten(value, width - 16)}", palette["body"])

    bar_width = min(width - 10, 72)
    draw_progress_bar(stdscr, 13, 4, bar_width, progress.percent, palette)
    safe_addstr(stdscr, 13, 6 + bar_width, f"{progress.percent:5.1f}%", palette["accent"])

    logs_top = 16
    logs_height = height - logs_top - 4
    draw_logs(stdscr, logs_top, 2, logs_height, width - 4, "activity", state.logs, palette)

    if progress.complete:
        if progress.success:
            safe_addstr(stdscr, height - 2, 2, shorten(f"Completed: {progress.output_path}", width - 4), palette["success"])
        else:
            safe_addstr(stdscr, height - 2, 2, shorten(f"Failed: {progress.error}", width - 4), palette["error"])
        safe_addstr(stdscr, height - 1, 2, "Press any key to return to the main session.", palette["muted"])
    else:
        safe_addstr(stdscr, height - 2, 2, "yt-dlp is running. Use Ctrl-C to abort the process.", palette["warning"])
        safe_addstr(stdscr, height - 1, 2, "Status updates stream into the activity panel.", palette["muted"])
    stdscr.refresh()


def cycle_index(index: int, delta: int, size: int) -> int:
    if size <= 0:
        return 0
    return (index + delta) % size


def match_quality(options: list[QualityOption], requested: str) -> int:
    wanted = requested.strip().lower()
    for idx, option in enumerate(options):
        if option.key == wanted or option.label.lower() == wanted:
            return idx
    return 0


def align_platform_with_url(settings: DownloadSettings, state: AppState) -> None:
    detected = infer_platform_from_url(settings.url)
    if detected and detected != settings.platform:
        previous = get_platform(settings.platform).label if settings.platform else "Unknown"
        settings.platform = detected
        state.log(f"URL matched {get_platform(detected).label}. Switched platform from {previous}.")
        state.status_line = f"Detected {get_platform(detected).label} from the URL and updated the platform."


def run_download_ui(
    stdscr: curses.window,
    palette: dict[str, int],
    info: dict[str, Any],
    settings: DownloadSettings,
    quality: QualityOption,
    state: AppState,
) -> str:
    progress = DownloadProgress(stage="Booting")
    result: dict[str, str] = {}
    error: list[Exception] = []
    stage_markers: set[str] = set()
    percent_bucket = -1

    def on_update(snapshot: DownloadProgress) -> None:
        nonlocal percent_bucket
        if snapshot.stage and snapshot.stage not in stage_markers:
            state.log(f"stage={snapshot.stage.lower()}")
            stage_markers.add(snapshot.stage)
        bucket = int(snapshot.percent // 10)
        if bucket > percent_bucket and bucket < 10:
            percent_bucket = bucket
            state.log(f"progress={snapshot.percent:0.0f}% speed={snapshot.speed} eta={snapshot.eta}")

    def worker() -> None:
        try:
            state.log(f"Starting {get_platform(settings.platform).label} transfer with profile {quality.label}.")
            result["path"] = download_media(settings, quality, progress=progress, quiet=True, on_update=on_update)
            progress.output_path = result["path"]
            progress.success = True
            state.log(f"Saved file to {result['path']}.")
        except Exception as exc:  # noqa: BLE001
            progress.error = str(exc)
            error.append(exc)
            state.log(f"Transfer failed: {exc}.")
        finally:
            progress.complete = True

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    stdscr.nodelay(True)
    while thread.is_alive():
        draw_download_screen(stdscr, palette, info, progress, state, settings)
        try:
            key = stdscr.get_wch()
            if key == "\x03":
                raise KeyboardInterrupt
        except curses.error:
            pass
        time.sleep(0.08)
    thread.join()
    stdscr.nodelay(False)
    draw_download_screen(stdscr, palette, info, progress, state, settings)
    stdscr.get_wch()
    if error:
        raise error[0]
    return result["path"]


def interactive_app(stdscr: curses.window, args: argparse.Namespace) -> int:
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
        output_dir=args.output_dir,
        filename_template=args.filename_template,
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
            state.status_line = f"{get_platform(selected).label} selected. Paste a URL to continue."

        if not settings.url:
            prompt = get_platform(settings.platform).url_prompt
            entered = prompt_input(stdscr, palette, APP_NAME, prompt, settings.url)
            if not entered:
                settings.platform = ""
                continue
            settings.url = entered
            align_platform_with_url(settings, state)
            state.log("Accepted URL input.")

        try:
            info = fetch_info_with_ui(stdscr, palette, settings.url, state, settings.platform)
            state.status_line = "Metadata resolved. Review the stream plan before running."
            state.log(f"Loaded metadata for {info.get('title') or settings.url}.")
        except Exception as exc:  # noqa: BLE001
            state.status_line = f"Could not resolve that URL: {exc}"
            state.log(f"Metadata fetch failed: {exc}.")
            settings.url = ""
            continue

        video_options, audio_options = build_quality_options(info, has_ffmpeg)
        options = video_options if settings.mode == "video" else audio_options
        settings.quality_index = match_quality(options, pending_quality) if pending_quality else min(settings.quality_index, len(options) - 1)
        pending_quality = ""

        while True:
            options = video_options if settings.mode == "video" else audio_options
            settings.quality_index = min(settings.quality_index, len(options) - 1)
            draw_main_screen(stdscr, palette, state, info, settings, options, selected_index, has_ffmpeg)
            key = stdscr.get_wch()

            if key in ("q", "Q"):
                return 0
            if key in (curses.KEY_UP, "k"):
                selected_index = cycle_index(selected_index, -1, 8)
                continue
            if key in (curses.KEY_DOWN, "j"):
                selected_index = cycle_index(selected_index, 1, 8)
                continue
            if isinstance(key, str) and key.isdigit() and "1" <= key <= "8":
                selected_index = int(key) - 1
                continue

            if selected_index == 2 and key in (curses.KEY_LEFT, curses.KEY_RIGHT, "h", "l", "\n", "\r", " "):
                settings.mode = "audio" if settings.mode == "video" else "video"
                settings.quality_index = 0
                state.status_line = f"Mode switched to {settings.mode}."
                state.log(f"Mode changed to {settings.mode}.")
                continue

            if selected_index == 3 and key in (curses.KEY_LEFT, curses.KEY_RIGHT, "h", "l", "\n", "\r"):
                delta = -1 if key in (curses.KEY_LEFT, "h") else 1
                settings.quality_index = cycle_index(settings.quality_index, delta, len(options))
                state.status_line = f"Quality set to {options[settings.quality_index].label}."
                state.log(f"Quality changed to {options[settings.quality_index].label}.")
                continue

            if key not in ("\n", "\r"):
                continue

            if selected_index == 0:
                selected = select_platform_ui(stdscr, palette, state, settings.platform)
                if selected and selected != settings.platform:
                    settings.platform = selected
                    settings.url = ""
                    settings.quality_index = 0
                    state.status_line = f"Platform switched to {get_platform(selected).label}. Paste a new URL."
                    break
                continue

            if selected_index == 1:
                updated = prompt_input(stdscr, palette, "source", get_platform(settings.platform).url_prompt, settings.url)
                if updated and updated != settings.url:
                    settings.url = updated
                    settings.quality_index = 0
                    align_platform_with_url(settings, state)
                    state.status_line = "Loading metadata for the updated URL."
                    state.log("URL updated. Reloading metadata.")
                    break
                continue

            if selected_index == 2:
                settings.mode = "audio" if settings.mode == "video" else "video"
                settings.quality_index = 0
                state.status_line = f"Mode switched to {settings.mode}."
                state.log(f"Mode changed to {settings.mode}.")
                continue

            if selected_index == 3:
                settings.quality_index = cycle_index(settings.quality_index, 1, len(options))
                state.status_line = f"Quality set to {options[settings.quality_index].label}."
                state.log(f"Quality changed to {options[settings.quality_index].label}.")
                continue

            if selected_index == 4:
                updated = prompt_input(stdscr, palette, "output", "Folder for downloaded files", settings.output_dir)
                if updated:
                    settings.output_dir = updated
                    state.status_line = f"Output folder updated to {settings.output_dir}."
                    state.log(f"Output folder set to {settings.output_dir}.")
                continue

            if selected_index == 5:
                updated = prompt_input(stdscr, palette, "template", "yt-dlp filename template", settings.filename_template)
                if updated:
                    settings.filename_template = updated
                    state.status_line = "Filename template updated."
                    state.log(f"Filename template set to {settings.filename_template}.")
                continue

            if selected_index == 6:
                try:
                    output_path = run_download_ui(stdscr, palette, info, settings, options[settings.quality_index], state)
                    state.status_line = f"Saved to {output_path}"
                except Exception as exc:  # noqa: BLE001
                    state.status_line = f"Download failed: {exc}"
                continue

            if selected_index == 7:
                return 0


def build_non_interactive_quality(args: argparse.Namespace, has_ffmpeg: bool) -> QualityOption:
    wanted = args.quality.strip().lower()
    if args.audio_only:
        if wanted in ("best", "best-audio", "native"):
            return QualityOption("best-audio", "Best audio", "Keep the highest quality audio stream", "bestaudio/best")
        if wanted == "mp3" and has_ffmpeg:
            return QualityOption(
                key="mp3",
                label="MP3 320k",
                description="Best audio converted to MP3",
                format_selector="bestaudio/best",
                postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}],
                final_extension="mp3",
            )
        if wanted == "m4a" and has_ffmpeg:
            return QualityOption(
                key="m4a",
                label="M4A",
                description="Best audio converted to M4A",
                format_selector="bestaudio/best",
                postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
                final_extension="m4a",
            )
        raise SystemExit("Unsupported audio quality. Use best, mp3, or m4a.")

    if wanted == "best":
        return QualityOption("best", "Best available", "Top quality the media offers", "bestvideo+bestaudio/best" if has_ffmpeg else "best")
    if wanted.endswith("p") and wanted[:-1].isdigit():
        height = int(wanted[:-1])
        return QualityOption(
            key=wanted,
            label=wanted,
            description=f"Video capped at {height}p",
            format_selector=f"bestvideo[height<={height}]+bestaudio/best[height<={height}]" if has_ffmpeg else f"best[height<={height}]/best",
        )
    raise SystemExit("Unsupported video quality. Use best or a value like 1080p.")


class CliReporter:
    def __init__(self) -> None:
        self.stage_seen: set[str] = set()
        self.percent_bucket = -1

    def section(self, label: str, value: str) -> None:
        print(f"{label:<10} {value}")

    def progress_update(self, progress: DownloadProgress) -> None:
        if progress.stage and progress.stage not in self.stage_seen:
            self.stage_seen.add(progress.stage)
            print(f"stage      {progress.stage}")
        bucket = int(progress.percent // 10)
        if bucket > self.percent_bucket and bucket <= 10:
            self.percent_bucket = bucket
            print(
                f"progress   {progress.percent:5.1f}%  "
                f"written={progress.downloaded}/{progress.total}  speed={progress.speed}  eta={progress.eta}"
            )


def non_interactive_app(args: argparse.Namespace) -> int:
    prompt_label = f"{get_platform(args.platform).label} URL" if args.platform != "auto" else "Media URL"
    url = args.url or input(f"{prompt_label}: ").strip()
    if not url:
        raise SystemExit("A URL is required.")

    detected_platform = infer_platform_from_url(url)
    platform_key = args.platform if args.platform != "auto" else (detected_platform or "youtube")

    has_ffmpeg = ffmpeg_available()
    quality = build_non_interactive_quality(args, has_ffmpeg)
    settings = DownloadSettings(
        platform=platform_key,
        url=url,
        mode="audio" if args.audio_only else "video",
        output_dir=args.output_dir,
        filename_template=args.filename_template,
    )
    reporter = CliReporter()

    print(APP_NAME)
    print("-" * len(APP_NAME))
    reporter.section("platform", get_platform(settings.platform).label)
    reporter.section("mode", settings.mode)
    reporter.section("quality", quality.label)
    reporter.section("output", str(Path(settings.output_dir).expanduser()))
    reporter.section("ffmpeg", "ready" if has_ffmpeg else "missing")
    if detected_platform and detected_platform != settings.platform:
        reporter.section("warning", f"url looks like {get_platform(detected_platform).label}, continuing anyway")

    try:
        info = extract_video_info(url)
        reporter.section("title", info.get("title") or url)
        reporter.section("creator", info.get("uploader") or info.get("channel") or info.get("uploader_id") or "Unknown creator")
    except Exception as exc:  # noqa: BLE001
        print(f"warning    metadata lookup failed: {exc}")

    progress = DownloadProgress(stage="Queued")
    try:
        output_path = download_media(settings, quality, progress=progress, quiet=True, on_update=reporter.progress_update)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"error      {exc}") from None
    print(f"saved      {output_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive multi-platform downloader powered by yt-dlp.")
    parser.add_argument("url", nargs="?", help="Media URL to load.")
    parser.add_argument(
        "--platform",
        default="auto",
        choices=["auto"] + [platform.key for platform in PLATFORMS],
        help="Source platform. Default: auto",
    )
    parser.add_argument("--audio-only", action="store_true", help="Download audio only.")
    parser.add_argument(
        "--quality",
        default="best",
        help="Interactive default or non-interactive target quality. Examples: best, 1080p, mp3, m4a",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination folder. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--filename-template",
        default=DEFAULT_TEMPLATE,
        help=f"yt-dlp output template. Default: {ARGPARSE_TEMPLATE_HELP}",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable the curses UI and run in plain CLI mode.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Force the curses UI even if stdin is not a TTY.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    wants_interactive = args.interactive or (sys.stdin.isatty() and not args.non_interactive)
    if wants_interactive:
        try:
            return curses.wrapper(lambda stdscr: interactive_app(stdscr, args))
        except curses.error:
            print("Interactive mode needs a larger terminal. Falling back to plain CLI.")
    return non_interactive_app(args)


if __name__ == "__main__":
    raise SystemExit(main())
