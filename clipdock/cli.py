from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .diagnostics import render_debug_report, render_failure, render_formats, render_simulation
from .downloader import (
    assemble_ydl_options,
    build_video_selector,
    download_media,
    extract_video_info,
    ffmpeg_available,
    preview_output_path,
)
from .models import (
    APP_NAME,
    ARGPARSE_TEMPLATE_HELP,
    AuthSettings,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TEMPLATE,
    DiagnosticsSettings,
    DownloadProgress,
    DownloadSettings,
    QualityOption,
    ResolvedPlan,
)
from .platforms import classify_platform_error, get_platform, infer_platform_from_url, normalize_url, platform_notes, youtube_url_has_playlist


def parse_browser_spec(value: str) -> tuple[str, str | None]:
    browser, _, profile = value.partition(":")
    browser = browser.strip()
    profile = profile.strip() or None
    if not browser:
        raise argparse.ArgumentTypeError("Browser name is required. Example: chrome or chrome:Default")
    return browser, profile


def build_auth_settings(args: argparse.Namespace) -> AuthSettings:
    cookies_from_browser = parse_browser_spec(args.cookies_from_browser) if args.cookies_from_browser else None
    return AuthSettings(cookies=args.cookies, cookies_from_browser=cookies_from_browser)


def build_non_interactive_quality(args: argparse.Namespace, has_ffmpeg: bool, platform_key: str) -> QualityOption:
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
        return QualityOption("best", "Best available", "Top quality the media offers", build_video_selector(has_ffmpeg, platform_key=platform_key))
    if wanted.endswith("p") and wanted[:-1].isdigit():
        height = int(wanted[:-1])
        return QualityOption(
            key=wanted,
            label=wanted,
            description=f"Video capped at {height}p",
            format_selector=build_video_selector(has_ffmpeg, max_height=height, platform_key=platform_key),
        )
    raise SystemExit("Unsupported video quality. Use best or a value like 1080p.")


class CliReporter:
    def __init__(self) -> None:
        self.stage_seen: set[str] = set()
        self.percent_bucket = -1

    @staticmethod
    def emit(text: str) -> None:
        encoding = sys.stdout.encoding or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))

    def section(self, label: str, value: str) -> None:
        self.emit(f"{label:<10} {value}")

    def progress_update(self, progress: DownloadProgress) -> None:
        if progress.stage and progress.stage not in self.stage_seen:
            self.stage_seen.add(progress.stage)
            self.emit(f"stage      {progress.stage}")
        bucket = int(progress.percent // 10)
        if bucket > self.percent_bucket and bucket <= 10:
            self.percent_bucket = bucket
            self.emit(
                f"progress   {progress.percent:5.1f}%  "
                f"written={progress.downloaded}/{progress.total}  speed={progress.speed}  eta={progress.eta}"
            )


def emit_text(text: str) -> None:
    CliReporter.emit(text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive multi-platform downloader powered by yt-dlp.")
    parser.add_argument("url", nargs="?", help="Media URL to load.")
    parser.add_argument(
        "--platform",
        default="auto",
        choices=["auto", "youtube", "tiktok", "reddit", "instagram", "x", "pinterest"],
        help="Source platform. Default: auto",
    )
    parser.add_argument("--audio-only", action="store_true", help="Download audio only.")
    parser.add_argument("--playlist", action="store_true", help="Allow playlist downloads when the source URL points to a playlist.")
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
    parser.add_argument("--non-interactive", action="store_true", help="Disable the curses UI and run in plain CLI mode.")
    parser.add_argument("--interactive", action="store_true", help="Force curses UI when supported.")
    parser.add_argument("--list-formats", action="store_true", help="Inspect formats without downloading.")
    parser.add_argument("--simulate", action="store_true", help="Resolve metadata, quality, and output path without downloading.")
    parser.add_argument("--debug", action="store_true", help="Show detailed yt-dlp and policy diagnostics.")
    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument("--cookies", help="Path to a Netscape-format cookie file.")
    auth_group.add_argument("--cookies-from-browser", help="Browser cookie source such as chrome or chrome:Default.")
    args = parser.parse_args(argv)
    args.auth = build_auth_settings(args)
    args.diagnostics = DiagnosticsSettings(list_formats=args.list_formats, simulate=args.simulate, debug=args.debug)
    return args


def build_settings(args: argparse.Namespace, url: str, platform_key: str) -> DownloadSettings:
    playlist_enabled = args.playlist or (platform_key == "youtube" and youtube_url_has_playlist(url))
    return DownloadSettings(
        platform=platform_key,
        url=normalize_url(platform_key, url),
        mode="audio" if args.audio_only else "video",
        playlist=playlist_enabled,
        output_dir=args.output_dir,
        filename_template=args.filename_template,
        auth=args.auth,
    )


def build_resolved_plan(
    settings: DownloadSettings,
    quality: QualityOption,
    info: dict[str, Any],
    has_ffmpeg: bool,
    detected_platform: str | None,
    original_url: str,
    debug: bool,
) -> ResolvedPlan:
    option_preview = assemble_ydl_options(settings, quality, quiet=not debug, debug=debug, skip_download=True)
    stable_preview = {key: value for key, value in option_preview.items() if key not in {"logger", "progress_hooks", "postprocessor_hooks"}}
    return ResolvedPlan(
        platform_key=settings.platform,
        platform_label=get_platform(settings.platform).option.label,
        requested_url=original_url,
        normalized_url=settings.url,
        mode=settings.mode,
        playlist=settings.playlist,
        quality=quality,
        output_path=preview_output_path(settings, info, quality),
        has_ffmpeg=has_ffmpeg,
        auth_description=settings.auth.describe(),
        policy_notes=platform_notes(settings.platform),
        option_preview=stable_preview,
        detected_platform=detected_platform,
    )


def interactive_unavailable_message() -> str:
    if sys.platform.startswith("win"):
        return "Interactive mode is unavailable in this Python environment. Install the Windows curses runtime and retry; falling back to plain CLI."
    return "Interactive mode is unavailable in this Python environment. Falling back to plain CLI."


def run_interactive(args: argparse.Namespace) -> int:
    try:
        import curses
        from . import ui
    except ModuleNotFoundError:
        emit_text(interactive_unavailable_message())
        return run_cli(args)

    try:
        return curses.wrapper(lambda stdscr: ui.interactive_app(stdscr, args))
    except curses.error:
        emit_text("Interactive mode needs a larger terminal. Falling back to plain CLI.")
        return run_cli(args)


def run_cli(args: argparse.Namespace) -> int:
    prompt_label = f"{get_platform(args.platform).option.label} URL" if args.platform != "auto" else "Media URL"
    original_url = args.url or input(f"{prompt_label}: ").strip()
    if not original_url:
        raise SystemExit("A URL is required.")

    detected_platform = infer_platform_from_url(original_url)
    platform_key = args.platform if args.platform != "auto" else (detected_platform or "youtube")
    has_ffmpeg = ffmpeg_available()
    settings = build_settings(args, original_url, platform_key)
    quality = build_non_interactive_quality(args, has_ffmpeg, settings.platform)
    reporter = CliReporter()

    emit_text(APP_NAME)
    emit_text("-" * len(APP_NAME))
    reporter.section("platform", get_platform(settings.platform).option.label)
    reporter.section("mode", settings.mode)
    reporter.section("playlist", "enabled" if settings.playlist else "single item")
    reporter.section("quality", quality.label)
    reporter.section("output", str(Path(settings.output_dir).expanduser()))
    reporter.section("ffmpeg", "ready" if has_ffmpeg else "missing")
    reporter.section("auth", settings.auth.describe())
    if settings.url != original_url:
        reporter.section("url", settings.url)
    if detected_platform and detected_platform != settings.platform:
        reporter.section("warning", f"url looks like {get_platform(detected_platform).option.label}, continuing anyway")

    try:
        info = extract_video_info(settings.url, settings.platform, settings.auth, debug=args.diagnostics.debug, playlist=settings.playlist)
    except Exception as exc:  # noqa: BLE001
        emit_text(render_failure(exc, classify_platform_error(settings.platform, exc), debug=args.diagnostics.debug))
        return 1

    reporter.section("title", info.get("title") or settings.url)
    reporter.section("creator", info.get("uploader") or info.get("channel") or info.get("uploader_id") or "Unknown creator")
    plan = build_resolved_plan(settings, quality, info, has_ffmpeg, detected_platform, original_url, args.diagnostics.debug)

    if args.diagnostics.list_formats:
        emit_text(render_formats(info))
        if args.diagnostics.debug:
            emit_text(render_debug_report(plan, info))
        return 0

    if args.diagnostics.simulate:
        emit_text(render_simulation(plan, info))
        if args.diagnostics.debug:
            emit_text(render_debug_report(plan, info))
        return 0

    progress = DownloadProgress(stage="Queued")
    try:
        output_path = download_media(
            settings,
            quality,
            progress=progress,
            quiet=not args.diagnostics.debug,
            debug=args.diagnostics.debug,
            on_update=reporter.progress_update,
        )
    except Exception as exc:  # noqa: BLE001
        emit_text(render_failure(exc, classify_platform_error(settings.platform, exc), debug=args.diagnostics.debug))
        return 1

    if args.diagnostics.debug:
        emit_text(render_debug_report(plan, info))
    emit_text(f"saved      {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    wants_interactive = (
        not args.non_interactive
        and not args.diagnostics.list_formats
        and not args.diagnostics.simulate
        and not args.diagnostics.debug
        and (args.interactive or sys.stdin.isatty())
    )
    if wants_interactive:
        return run_interactive(args)
    return run_cli(args)
