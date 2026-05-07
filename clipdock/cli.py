from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

from .config import delete_preset, get_preset, load_config, save_config, upsert_preset
from .diagnostics import render_debug_report, render_failure, render_formats, render_simulation
from .downloader import (
    assemble_ydl_options,
    build_video_selector,
    download_media,
    extract_video_info,
    ffmpeg_available,
    preview_output_path,
)
from .history import (
    cleanup_actions,
    delete_entries,
    duplicate_groups,
    extract_media_identity,
    latest_duplicate,
    load_entries,
    record_download,
    remove_file,
)
from .inspector import collect_doctor_checks, render_history_entry
from .models import (
    APP_NAME,
    ARGPARSE_TEMPLATE_HELP,
    AppConfig,
    AuthSettings,
    CleanupAction,
    DiagnosticsSettings,
    DownloadContext,
    DownloadProgress,
    DownloadSettings,
    PresetConfig,
    QualityOption,
    ResolvedPlan,
)
from .platforms import classify_platform_error, get_platform, infer_platform_from_url, normalize_url, platform_notes, youtube_url_has_playlist
from .watcher import ClipboardMatch, watch_clipboard


def parse_browser_spec(value: str) -> tuple[str, str | None]:
    browser, _, profile = value.partition(":")
    browser = browser.strip()
    profile = profile.strip() or None
    if not browser:
        raise argparse.ArgumentTypeError("Browser name is required. Example: chrome or chrome:Default")
    return browser, profile


def build_auth_settings(args: argparse.Namespace) -> AuthSettings:
    cookies_from_browser_value = getattr(args, "cookies_from_browser", None)
    cookies_from_browser = parse_browser_spec(cookies_from_browser_value) if cookies_from_browser_value else None
    return AuthSettings(cookies=getattr(args, "cookies", None), cookies_from_browser=cookies_from_browser)


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


def _bool_action_kwargs(raw: bool, help_text: str, default: bool = False) -> dict[str, Any]:
    if raw:
        return {
            "action": argparse.BooleanOptionalAction,
            "default": argparse.SUPPRESS,
            "help": help_text,
        }
    return {
        "action": argparse.BooleanOptionalAction,
        "default": default,
        "help": help_text,
    }


def add_download_arguments(
    parser: argparse.ArgumentParser,
    *,
    raw: bool = False,
    include_url: bool = True,
    include_ui: bool = True,
    include_diagnostics: bool = True,
    include_preset: bool = True,
    include_force: bool = True,
) -> None:
    if include_url:
        parser.add_argument("url", nargs="?", help="Media URL to load.")
    parser.add_argument(
        "--platform",
        default=argparse.SUPPRESS if raw else "auto",
        choices=["auto", "youtube", "tiktok", "reddit", "instagram", "x", "pinterest"],
        help="Source platform. Default: auto",
    )
    parser.add_argument("--audio-only", **_bool_action_kwargs(raw, "Download audio only.", False))
    parser.add_argument("--playlist", **_bool_action_kwargs(raw, "Allow playlist downloads when the source URL points to a playlist.", False))
    parser.add_argument(
        "--quality",
        default=argparse.SUPPRESS if raw else "best",
        help="Interactive default or non-interactive target quality. Examples: best, 1080p, mp3, m4a",
    )
    parser.add_argument(
        "--output-dir",
        default=argparse.SUPPRESS if raw else str(Path.home() / "Downloads"),
        help=f"Destination folder. Default: {Path.home() / 'Downloads'}",
    )
    parser.add_argument(
        "--filename-template",
        default=argparse.SUPPRESS if raw else "%(title)s.%(ext)s",
        help=f"yt-dlp output template. Default: {ARGPARSE_TEMPLATE_HELP}",
    )
    if include_preset:
        parser.add_argument("--preset", default=argparse.SUPPRESS if raw else None, help="Preset name to apply before CLI overrides.")
    if include_force:
        parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS if raw else False, help="Download even when a duplicate URL is already recorded.")
    if include_ui:
        parser.add_argument("--non-interactive", action="store_true", default=argparse.SUPPRESS if raw else False, help="Disable the curses UI and run in plain CLI mode.")
        parser.add_argument("--interactive", action="store_true", default=argparse.SUPPRESS if raw else False, help="Force curses UI when supported.")
    if include_diagnostics:
        parser.add_argument("--list-formats", action="store_true", default=argparse.SUPPRESS if raw else False, help="Inspect formats without downloading.")
        parser.add_argument("--simulate", action="store_true", default=argparse.SUPPRESS if raw else False, help="Resolve metadata, quality, and output path without downloading.")
        parser.add_argument("--debug", action="store_true", default=argparse.SUPPRESS if raw else False, help="Show detailed yt-dlp and policy diagnostics.")
    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument("--cookies", default=argparse.SUPPRESS if raw else None, help="Path to a Netscape-format cookie file.")
    auth_group.add_argument("--cookies-from-browser", default=argparse.SUPPRESS if raw else None, help="Browser cookie source such as chrome or chrome:Default.")


def create_download_parser(*, raw: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interactive multi-platform downloader powered by yt-dlp.",
        epilog="Additional commands: watch, preset, use, history, duplicates, clean-duplicates, doctor",
    )
    add_download_arguments(parser, raw=raw)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = create_download_parser(raw=False)
    args = parser.parse_args(argv)
    args.preset = getattr(args, "preset", None)
    args.force = bool(getattr(args, "force", False))
    args.auth = build_auth_settings(args)
    args.diagnostics = DiagnosticsSettings(
        list_formats=bool(getattr(args, "list_formats", False)),
        simulate=bool(getattr(args, "simulate", False)),
        debug=bool(getattr(args, "debug", False)),
    )
    return args


def resolve_download_namespace(raw_args: argparse.Namespace, config: AppConfig, *, preset_name: str | None = None) -> argparse.Namespace:
    raw = vars(raw_args)
    requested_preset = preset_name if preset_name is not None else raw.get("preset")
    preset = get_preset(config, requested_preset) if requested_preset else None
    if requested_preset and preset is None:
        raise SystemExit(f'Preset "{requested_preset}" was not found.')

    def pick(name: str, default: Any, preset_value: Any = None) -> Any:
        if name in raw:
            return raw[name]
        if preset_value is not None:
            return preset_value
        return default

    namespace = argparse.Namespace(
        url=raw.get("url"),
        platform=pick("platform", "auto", preset.platform if preset else None),
        audio_only=pick("audio_only", False, preset.audio_only if preset else None),
        playlist=pick("playlist", False, preset.playlist if preset else None),
        quality=pick("quality", "best", preset.quality if preset else None),
        output_dir=pick("output_dir", str(Path.home() / "Downloads"), preset.output_dir if preset else None),
        filename_template=pick("filename_template", "%(title)s.%(ext)s", preset.filename_template if preset else None),
        non_interactive=bool(raw.get("non_interactive", False)),
        interactive=bool(raw.get("interactive", False)),
        list_formats=bool(raw.get("list_formats", False)),
        simulate=bool(raw.get("simulate", False)),
        debug=bool(raw.get("debug", False)),
        cookies=pick("cookies", None, preset.cookies if preset else None),
        cookies_from_browser=pick("cookies_from_browser", None, preset.cookies_from_browser if preset else None),
        preset=requested_preset,
        force=bool(raw.get("force", False)),
    )
    namespace.auth = build_auth_settings(namespace)
    namespace.diagnostics = DiagnosticsSettings(
        list_formats=namespace.list_formats,
        simulate=namespace.simulate,
        debug=namespace.debug,
    )
    return namespace


def build_settings(args: argparse.Namespace, url: str, platform_key: str) -> DownloadSettings:
    playlist_enabled = bool(args.playlist) or (platform_key == "youtube" and youtube_url_has_playlist(url))
    return DownloadSettings(
        platform=platform_key,
        url=normalize_url(platform_key, url),
        mode="audio" if bool(args.audio_only) else "video",
        playlist=playlist_enabled,
        output_dir=args.output_dir,
        filename_template=args.filename_template,
        auth=args.auth,
        preset_name=getattr(args, "preset", None),
        force=bool(getattr(args, "force", False)),
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


def build_download_context(args: argparse.Namespace, original_url: str) -> DownloadContext:
    detected_platform = infer_platform_from_url(original_url)
    platform_key = args.platform if args.platform != "auto" else (detected_platform or "youtube")
    has_ffmpeg = ffmpeg_available()
    settings = build_settings(args, original_url, platform_key)
    quality = build_non_interactive_quality(args, has_ffmpeg, settings.platform)
    info = extract_video_info(settings.url, settings.platform, settings.auth, debug=args.diagnostics.debug, playlist=settings.playlist)
    plan = build_resolved_plan(settings, quality, info, has_ffmpeg, detected_platform, original_url, args.diagnostics.debug)
    return DownloadContext(
        original_url=original_url,
        detected_platform=detected_platform,
        settings=settings,
        quality=quality,
        info=info,
        plan=plan,
        has_ffmpeg=has_ffmpeg,
    )


def interactive_unavailable_message() -> str:
    if sys.platform.startswith("win"):
        return "Interactive mode is unavailable in this Python environment. Install the Windows curses runtime and retry; falling back to plain CLI."
    return "Interactive mode is unavailable in this Python environment. Falling back to plain CLI."


def run_interactive(args: argparse.Namespace) -> int:
    try:
        import curses
        ui = importlib.import_module("clipdock.ui")
    except ModuleNotFoundError:
        emit_text(interactive_unavailable_message())
        return run_cli(args)

    try:
        return curses.wrapper(lambda stdscr: ui.interactive_app(stdscr, args))
    except curses.error:
        emit_text("Interactive mode needs a larger terminal. Falling back to plain CLI.")
        return run_cli(args)


def _prompt_yes_no(question: str, *, default: bool = False) -> bool:
    try:
        answer = input(question).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in {"y", "yes"}


def confirm_duplicate_download(path: str, downloaded_at: str) -> bool:
    emit_text(f"duplicate  This URL was already downloaded on {downloaded_at}:")
    emit_text(f"path       {path}")
    return _prompt_yes_no("Download again? [y/N] ", default=False)


def confirm_preset_overwrite(name: str) -> bool:
    return _prompt_yes_no(f'Preset "{name}" already exists. Overwrite it? [y/N] ', default=False)


def prompt_watch_action(preset_name: str | None) -> str:
    label = preset_name or "default"
    try:
        answer = input(f'Download with preset "{label}"? [Y/n/q] ').strip().lower()
    except EOFError:
        return "n"
    if answer in {"", "y", "yes"}:
        return "y"
    if answer in {"q", "quit"}:
        return "q"
    return "n"


def describe_download_context(context: DownloadContext, reporter: CliReporter) -> None:
    settings = context.settings
    reporter.section("platform", context.plan.platform_label)
    reporter.section("mode", settings.mode)
    reporter.section("playlist", "enabled" if settings.playlist else "single item")
    reporter.section("quality", context.quality.label)
    reporter.section("output", str(Path(settings.output_dir).expanduser()))
    reporter.section("ffmpeg", "ready" if context.has_ffmpeg else "missing")
    reporter.section("auth", settings.auth.describe())
    if context.plan.normalized_url != context.original_url:
        reporter.section("url", context.plan.normalized_url)
    if context.detected_platform and context.detected_platform != settings.platform:
        reporter.section("warning", f"url looks like {get_platform(context.detected_platform).option.label}, continuing anyway")
    reporter.section("title", context.info.get("title") or settings.url)
    reporter.section("creator", context.info.get("uploader") or context.info.get("channel") or context.info.get("uploader_id") or "Unknown creator")


def run_download_from_context(
    args: argparse.Namespace,
    context: DownloadContext,
    *,
    reporter: CliReporter | None = None,
    duplicate_entry: Any = None,
    confirm_duplicate: bool = True,
) -> str | None:
    duplicate = duplicate_entry
    extractor_key, media_id = extract_media_identity(context.info)
    if duplicate is None and not context.settings.force:
        duplicate = latest_duplicate(context.plan.normalized_url, extractor_key=extractor_key, media_id=media_id)
    if duplicate is not None and not context.settings.force and confirm_duplicate:
        if not confirm_duplicate_download(duplicate.output_path, duplicate.downloaded_at):
            emit_text("status     skipped duplicate download")
            return None

    if reporter is None:
        reporter = CliReporter()
    progress = DownloadProgress(stage="Queued")
    output_path = download_media(
        context.settings,
        context.quality,
        progress=progress,
        quiet=not args.diagnostics.debug,
        debug=args.diagnostics.debug,
        on_update=reporter.progress_update,
    )
    record_download(context.settings, context.quality, context.original_url, output_path, info=context.info)
    return output_path


def run_cli(args: argparse.Namespace, original_url: str | None = None) -> int:
    prompt_label = f"{get_platform(args.platform).option.label} URL" if args.platform != "auto" else "Media URL"
    selected_url = original_url or args.url or input(f"{prompt_label}: ").strip()
    if not selected_url:
        raise SystemExit("A URL is required.")

    reporter = CliReporter()
    emit_text(APP_NAME)
    emit_text("-" * len(APP_NAME))
    try:
        context = build_download_context(args, selected_url)
    except Exception as exc:  # noqa: BLE001
        detected_platform = infer_platform_from_url(selected_url) or (args.platform if args.platform != "auto" else "youtube")
        emit_text(render_failure(exc, classify_platform_error(detected_platform, exc), debug=args.diagnostics.debug))
        return 1

    describe_download_context(context, reporter)
    if args.diagnostics.list_formats:
        emit_text(render_formats(context.info))
        if args.diagnostics.debug:
            emit_text(render_debug_report(context.plan, context.info))
        return 0

    if args.diagnostics.simulate:
        emit_text(render_simulation(context.plan, context.info))
        if args.diagnostics.debug:
            emit_text(render_debug_report(context.plan, context.info))
        return 0

    try:
        output_path = run_download_from_context(args, context, reporter=reporter)
    except Exception as exc:  # noqa: BLE001
        emit_text(render_failure(exc, classify_platform_error(context.settings.platform, exc), debug=args.diagnostics.debug))
        return 1
    if output_path is None:
        return 0
    if args.diagnostics.debug:
        emit_text(render_debug_report(context.plan, context.info))
    emit_text(f"saved      {output_path}")
    return 0


def create_preset_from_args(name: str, raw_args: argparse.Namespace) -> PresetConfig:
    data = vars(raw_args)
    return PresetConfig(
        name=name,
        platform=data.get("platform"),
        audio_only=data.get("audio_only"),
        playlist=data.get("playlist"),
        quality=data.get("quality"),
        output_dir=data.get("output_dir"),
        filename_template=data.get("filename_template"),
        cookies=data.get("cookies"),
        cookies_from_browser=data.get("cookies_from_browser"),
    )


def format_preset(preset: PresetConfig) -> list[str]:
    rows = [f"preset     {preset.name}"]
    fields = {
        "platform": preset.platform,
        "audio_only": preset.audio_only,
        "playlist": preset.playlist,
        "quality": preset.quality,
        "output_dir": preset.output_dir,
        "filename_template": preset.filename_template,
        "cookies": preset.cookies,
        "cookies_from_browser": preset.cookies_from_browser,
    }
    for key, value in fields.items():
        if value is not None:
            rows.append(f"{key:<10} {value}")
    return rows


def run_preset_command(argv: list[str], config: AppConfig) -> int:
    parser = argparse.ArgumentParser(prog="clipdock preset", description="Manage clipdock presets.")
    subparsers = parser.add_subparsers(dest="preset_command", required=True)

    create_parser = subparsers.add_parser("create", help="Create or update a preset.")
    create_parser.add_argument("name")
    create_parser.add_argument("download_args", nargs=argparse.REMAINDER)
    subparsers.add_parser("list", help="List preset names.")
    show_parser = subparsers.add_parser("show", help="Show a preset.")
    show_parser.add_argument("name")
    delete_parser = subparsers.add_parser("delete", help="Delete a preset.")
    delete_parser.add_argument("name")

    args = parser.parse_args(argv)
    if args.preset_command == "list":
        if not config.presets:
            emit_text("status     no presets configured")
            return 0
        for name in sorted(config.presets):
            emit_text(f"preset     {name}")
        return 0

    if args.preset_command == "show":
        preset = get_preset(config, args.name)
        if preset is None:
            emit_text(f'error      Preset "{args.name}" was not found.')
            return 1
        for line in format_preset(preset):
            emit_text(line)
        return 0

    if args.preset_command == "delete":
        if get_preset(config, args.name) is None:
            emit_text(f'error      Preset "{args.name}" was not found.')
            return 1
        updated = delete_preset(config, args.name)
        save_config(updated)
        emit_text(f'preset     deleted "{args.name}"')
        return 0

    raw_args = create_download_parser(raw=True).parse_args(args.download_args)
    preset = create_preset_from_args(args.name, raw_args)
    if args.name in config.presets and not confirm_preset_overwrite(args.name):
        emit_text("status     preset overwrite cancelled")
        return 1
    updated = upsert_preset(config, preset)
    save_config(updated)
    emit_text(f'preset     saved "{args.name}"')
    return 0


def run_use_command(argv: list[str], config: AppConfig) -> int:
    parser = argparse.ArgumentParser(prog="clipdock use", description="Download using a named preset.")
    parser.add_argument("preset_name")
    parser.add_argument("download_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    raw_download_args = create_download_parser(raw=True).parse_args(args.download_args)
    resolved = resolve_download_namespace(raw_download_args, config, preset_name=args.preset_name)
    return run_download_command(resolved)


def _format_duplicate_group_header(use_hash: bool, key: str) -> str:
    label = "sha256" if use_hash else "identity"
    return f"{label:<10} {key}"


def run_history_command(argv: list[str], _config: AppConfig) -> int:
    parser = argparse.ArgumentParser(prog="clipdock history", description="Show recent download history.")
    parser.add_argument("--platform", choices=["youtube", "tiktok", "reddit", "instagram", "x", "pinterest"], help="Filter by platform.")
    parser.add_argument("--preset", help="Filter by preset name.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of rows to show.")
    args = parser.parse_args(argv)
    entries = load_entries(platform=args.platform, preset_name=args.preset, limit=max(1, args.limit))
    if not entries:
        emit_text("status     no history entries found")
        return 0
    for idx, entry in enumerate(entries):
        if idx:
            emit_text("")
        for line in render_history_entry(entry):
            emit_text(line)
    return 0


def run_duplicates_command(argv: list[str], config: AppConfig) -> int:
    parser = argparse.ArgumentParser(prog="clipdock duplicates", description="List duplicate downloads.")
    parser.add_argument("--hash", action="store_true", help="Group duplicates by file hash instead of URL.")
    args = parser.parse_args(argv)
    use_hash = args.hash or config.duplicates.default_hash
    groups = duplicate_groups(use_hash=use_hash)
    if not groups:
        emit_text("status     no duplicate groups found")
        return 0
    for group in groups:
        emit_text(_format_duplicate_group_header(use_hash, group.key))
        for entry in group.entries:
            status = "missing" if not Path(entry.output_path).exists() else "present"
            emit_text(f"entry      {entry.downloaded_at}  {status:<7}  {entry.output_path}")
    return 0


def _confirm_cleanup(action: CleanupAction, *, dry_run: bool) -> bool:
    emit_text(f"keep       {action.keep.output_path}")
    for entry in action.remove:
        emit_text(f"remove     {entry.output_path}")
    if dry_run:
        return False
    return _prompt_yes_no("Delete the duplicate files listed above? [y/N] ", default=False)


def run_clean_duplicates_command(argv: list[str], config: AppConfig) -> int:
    parser = argparse.ArgumentParser(prog="clipdock clean-duplicates", description="Delete duplicate files after confirmation.")
    parser.add_argument("--hash", action="store_true", help="Group duplicates by file hash instead of URL.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without removing files.")
    args = parser.parse_args(argv)
    use_hash = args.hash or config.duplicates.default_hash
    actions = cleanup_actions(use_hash=use_hash)
    if not actions:
        emit_text("status     no duplicate files to clean")
        return 0
    removed_count = 0
    for action in actions:
        if not _confirm_cleanup(action, dry_run=args.dry_run):
            if args.dry_run:
                continue
            emit_text("status     skipped cleanup group")
            continue
        for entry in action.remove:
            remove_file(entry)
            removed_count += 1
        delete_entries(action.remove)
    if args.dry_run:
        emit_text(f"status     dry-run complete for {len(actions)} duplicate groups")
        return 0
    emit_text(f"status     removed {removed_count} duplicate files")
    return 0


def _doctor_check(label: str, ok: bool, detail: str) -> None:
    status = "ok" if ok else "warn"
    emit_text(f"{label:<10} {status:<4} {detail}")


def run_doctor_command(argv: list[str], _config: AppConfig) -> int:
    parser = argparse.ArgumentParser(prog="clipdock doctor", description="Check local clipdock dependencies and paths.")
    parser.parse_args(argv)

    emit_text("doctor")
    emit_text("------")
    for label, ok, detail in collect_doctor_checks():
        _doctor_check(label, ok, detail)
    return 0


def create_watch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clipdock watch", description="Watch the clipboard for supported URLs.")
    parser.add_argument("--interval", type=float, default=argparse.SUPPRESS, help="Clipboard polling interval in seconds.")
    parser.add_argument("--auto", action=argparse.BooleanOptionalAction, default=argparse.SUPPRESS, help="Download matching clipboard URLs without prompting.")
    add_download_arguments(
        parser,
        raw=True,
        include_url=False,
        include_ui=False,
        include_diagnostics=False,
        include_preset=True,
        include_force=True,
    )
    return parser


def resolve_watch_namespace(raw_args: argparse.Namespace, config: AppConfig) -> argparse.Namespace:
    resolved = resolve_download_namespace(raw_args, config)
    raw = vars(raw_args)
    resolved.interval = float(raw.get("interval", config.watch.interval))
    resolved.auto = bool(raw.get("auto", config.watch.auto))
    if resolved.preset is None and config.watch.preset:
        if get_preset(config, config.watch.preset) is None:
            raise SystemExit(f'Preset "{config.watch.preset}" from watch config was not found.')
        resolved = resolve_download_namespace(raw_args, config, preset_name=config.watch.preset)
        resolved.interval = float(raw.get("interval", config.watch.interval))
        resolved.auto = bool(raw.get("auto", config.watch.auto))
    return resolved


def handle_watch_match(match: ClipboardMatch, args: argparse.Namespace, seen_urls: set[str]) -> bool:
    if match.normalized_url in seen_urls:
        emit_text(f"watch      already handled this session: {match.normalized_url}")
        return True
    seen_urls.add(match.normalized_url)

    try:
        context = build_download_context(args, match.url)
    except Exception as exc:  # noqa: BLE001
        emit_text(render_failure(exc, classify_platform_error(match.platform, exc), debug=False))
        return True
    extractor_key, media_id = extract_media_identity(context.info)
    duplicate = None if args.force else latest_duplicate(match.normalized_url, extractor_key=extractor_key, media_id=media_id)
    if args.auto and duplicate is not None and not args.force:
        emit_text(f"watch      duplicate skipped: {duplicate.output_path}")
        return True

    preset_label = args.preset or "default"
    emit_text(f"watch      detected {context.plan.platform_label} URL in clipboard")
    emit_text(f'title      "{context.info.get("title") or context.plan.normalized_url}"')
    if duplicate is not None:
        emit_text(f"duplicate  already downloaded on {duplicate.downloaded_at}")
        emit_text(f"path       {duplicate.output_path}")

    if not args.auto:
        action = prompt_watch_action(args.preset)
        if action == "q":
            emit_text("watch      exiting")
            return False
        if action != "y":
            emit_text(f"watch      skipped preset {preset_label}")
            return True

    try:
        output_path = run_download_from_context(args, context, duplicate_entry=duplicate, confirm_duplicate=False)
    except Exception as exc:  # noqa: BLE001
        emit_text(render_failure(exc, classify_platform_error(context.settings.platform, exc), debug=False))
        return True
    if output_path:
        emit_text(f"saved      {output_path}")
    return True


def run_watch_command(argv: list[str], config: AppConfig) -> int:
    args = resolve_watch_namespace(create_watch_parser().parse_args(argv), config)
    emit_text("watch      monitoring clipboard for supported URLs")
    emit_text(f'watch      preset={args.preset or "default"} interval={args.interval:.2f}s auto={str(args.auto).lower()}')
    seen_urls: set[str] = set()
    return watch_clipboard(
        interval=args.interval,
        emit=emit_text,
        on_match=lambda match: handle_watch_match(match, args, seen_urls),
    )


def run_download_command(args: argparse.Namespace) -> int:
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


def dispatch(argv: list[str], config: AppConfig) -> int:
    if argv:
        command = argv[0]
        if command == "preset":
            return run_preset_command(argv[1:], config)
        if command == "use":
            return run_use_command(argv[1:], config)
        if command == "history":
            return run_history_command(argv[1:], config)
        if command == "duplicates":
            return run_duplicates_command(argv[1:], config)
        if command == "clean-duplicates":
            return run_clean_duplicates_command(argv[1:], config)
        if command == "watch":
            return run_watch_command(argv[1:], config)
        if command == "doctor":
            return run_doctor_command(argv[1:], config)
    raw_args = create_download_parser(raw=True).parse_args(argv)
    return run_download_command(resolve_download_namespace(raw_args, config))


def main(argv: list[str] | None = None) -> int:
    active_argv = list(sys.argv[1:] if argv is None else argv)
    config = load_config()
    return dispatch(active_argv, config)
