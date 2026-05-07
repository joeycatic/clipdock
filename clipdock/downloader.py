from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from .models import AuthSettings, DownloadProgress, DownloadSettings, QualityOption
from .platforms import platform_overrides


class NullLogger:
    def debug(self, msg: str) -> None:
        return

    def warning(self, msg: str) -> None:
        return

    def error(self, msg: str) -> None:
        return


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def build_video_selector(has_ffmpeg: bool, max_height: int | None = None, platform_key: str = "generic") -> str:
    if platform_key == "reddit":
        if has_ffmpeg:
            if max_height is None:
                return "bv*+ba/b/bv*"
            return f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b/bv*"
        if max_height is None:
            return "b/bv*"
        return f"b[height<={max_height}]/bv*[height<={max_height}]/b/bv*"

    if has_ffmpeg:
        if max_height is None:
            return "bestvideo+bestaudio/best"
        return f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"
    if max_height is None:
        return "best/bestvideo"
    return f"best[height<={max_height}]/bestvideo[height<={max_height}]/best"


def extract_video_info(url: str, platform_key: str, auth: AuthSettings, debug: bool = False, playlist: bool = False) -> dict[str, Any]:
    opts = {
        "quiet": not debug,
        "no_warnings": not debug,
        "skip_download": True,
        "noplaylist": not playlist,
    }
    if playlist:
        opts["ignoreerrors"] = True
        opts["extract_flat"] = "in_playlist"
    opts.update(platform_overrides(platform_key, auth))
    if not debug:
        opts["logger"] = NullLogger()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)


def encode_playlist_items(items: list[int] | None) -> str | None:
    if not items:
        return None
    normalized = sorted({int(item) for item in items if int(item) > 0})
    if not normalized:
        return None
    return ",".join(str(item) for item in normalized)


def parse_subtitle_languages(value: str | None) -> list[str] | None:
    if value is None:
        return None
    languages = [item.strip() for item in value.split(",") if item.strip()]
    return languages or None


def build_quality_options(info: dict[str, Any], has_ffmpeg: bool, platform_key: str) -> tuple[list[QualityOption], list[QualityOption]]:
    formats = info.get("formats") or []
    heights: dict[int, dict[str, Any]] = {}
    for fmt in formats:
        if fmt.get("vcodec") == "none":
            continue
        height = fmt.get("height")
        if not height:
            continue
        bucket = heights.setdefault(int(height), {"exts": set(), "fps": 0})
        ext = fmt.get("ext")
        if ext:
            bucket["exts"].add(ext)
        bucket["fps"] = max(bucket["fps"], int(fmt.get("fps") or 0))

    video_options = [
        QualityOption(
            key="best",
            label="Best available",
            description="Top quality stream plan offered by the source",
            format_selector=build_video_selector(has_ffmpeg, platform_key=platform_key),
        )
    ]
    discovered_heights = sorted(heights.keys(), reverse=True)[:8]
    fallback_heights = (2160, 1440, 1080, 720, 480, 360)
    for height in discovered_heights or fallback_heights:
        details = heights.get(height, {"exts": set(), "fps": 0})
        container_hint = "/".join(sorted(details["exts"])) if details.get("exts") else "auto"
        fps_hint = f" @ {details['fps']}fps" if details.get("fps") else ""
        description = f"Cap output at {height}p{fps_hint} | {container_hint}"
        if height not in heights:
            description = f"Try to cap output at {height}p when the source provides it"
        video_options.append(
            QualityOption(
                key=f"{height}p",
                label=f"{height}p",
                description=description,
                format_selector=build_video_selector(has_ffmpeg, max_height=height, platform_key=platform_key),
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
                    postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}],
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


def assemble_ydl_options(
    settings: DownloadSettings,
    quality: QualityOption | None = None,
    *,
    quiet: bool = True,
    debug: bool = False,
    progress: DownloadProgress | None = None,
    on_update: Callable[[DownloadProgress], None] | None = None,
    skip_download: bool = False,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "outtmpl": str(Path(settings.output_dir).expanduser() / settings.filename_template),
        "noplaylist": not settings.playlist,
        "quiet": quiet and not debug,
        "no_warnings": quiet and not debug,
    }
    if quality is not None:
        options["format"] = quality.format_selector
        if quality.postprocessors:
            options["postprocessors"] = quality.postprocessors
    if skip_download:
        options["skip_download"] = True
    if settings.playlist:
        options["ignoreerrors"] = True
        playlist_items = encode_playlist_items(settings.playlist_items)
        if playlist_items:
            options["playlist_items"] = playlist_items
    subtitle_languages = parse_subtitle_languages(settings.sub_lang)
    if settings.write_subs:
        options["writesubtitles"] = True
    if settings.write_auto_subs:
        options["writeautomaticsub"] = True
    if subtitle_languages:
        options["subtitleslangs"] = subtitle_languages
    if settings.embed_subs:
        options["embedsubtitles"] = True
    if settings.write_thumbnail:
        options["writethumbnail"] = True
    if settings.embed_thumbnail:
        options["embedthumbnail"] = True
    if settings.write_info_json:
        options["writeinfojson"] = True
    if settings.embed_metadata:
        options["embedmetadata"] = True
    if settings.split_chapters:
        options["split_chapters"] = True
    if settings.remux_video:
        options["remuxvideo"] = settings.remux_video
    if quiet and not debug:
        options["logger"] = NullLogger()
    if progress is not None:
        options["progress_hooks"] = [progress_hook_factory(progress, on_update=on_update)]
        options["postprocessor_hooks"] = [postprocessor_hook_factory(progress, on_update=on_update)]
    options.update(platform_overrides(settings.platform, settings.auth))
    return options


def resolve_output_path(ydl: yt_dlp.YoutubeDL, info: dict[str, Any], quality: QualityOption) -> str:
    if info.get("_type") == "playlist" or isinstance(info.get("entries"), list):
        entries = info.get("entries") or []
        for entry in entries:
            if isinstance(entry, dict):
                return str(Path(ydl.prepare_filename(entry)).parent)
        outtmpl = ydl.params.get("outtmpl")
        if isinstance(outtmpl, dict):
            outtmpl = outtmpl.get("default", "")
        return str(Path(str(outtmpl)).parent)
    prepared = Path(ydl.prepare_filename(info))
    if settings_remux_video := ydl.params.get("remuxvideo"):
        return str(prepared.with_suffix(f".{settings_remux_video}"))
    if quality.final_extension:
        return str(prepared.with_suffix(f".{quality.final_extension}"))
    return str(prepared)


def preview_output_path(settings: DownloadSettings, info: dict[str, Any], quality: QualityOption) -> str:
    options = assemble_ydl_options(settings, quality, quiet=True, skip_download=True)
    with yt_dlp.YoutubeDL(options) as ydl:
        return resolve_output_path(ydl, info, quality)


def download_media(
    settings: DownloadSettings,
    quality: QualityOption,
    progress: DownloadProgress | None = None,
    quiet: bool = True,
    debug: bool = False,
    on_update: Callable[[DownloadProgress], None] | None = None,
) -> str:
    output_dir = Path(settings.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    options = assemble_ydl_options(
        settings,
        quality,
        quiet=quiet,
        debug=debug,
        progress=progress,
        on_update=on_update,
    )
    with yt_dlp.YoutubeDL(options) as ydl:
        result = ydl.extract_info(settings.url, download=True)
        return resolve_output_path(ydl, result, quality)


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


def match_quality(options: list[QualityOption], requested: str) -> int:
    wanted = requested.strip().lower()
    for idx, option in enumerate(options):
        if option.key == wanted or option.label.lower() == wanted:
            return idx
    return 0
