from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


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


@dataclass
class QualityOption:
    key: str
    label: str
    description: str
    format_selector: str
    postprocessors: list[dict[str, Any]] = field(default_factory=list)
    final_extension: str | None = None


@dataclass
class AuthSettings:
    cookies: str | None = None
    cookies_from_browser: tuple[str, str | None] | None = None

    def describe(self) -> str:
        if self.cookies:
            return f"cookie file: {self.cookies}"
        if self.cookies_from_browser:
            browser, profile = self.cookies_from_browser
            return f"browser cookies: {browser}{f' ({profile})' if profile else ''}"
        return "none"


@dataclass
class DiagnosticsSettings:
    list_formats: bool = False
    simulate: bool = False
    debug: bool = False


@dataclass
class DownloadSettings:
    platform: str = ""
    url: str = ""
    mode: str = "video"
    playlist: bool = False
    playlist_items: list[int] | None = None
    output_dir: str = DEFAULT_OUTPUT_DIR
    filename_template: str = DEFAULT_TEMPLATE
    quality_index: int = 0
    auth: AuthSettings = field(default_factory=AuthSettings)
    preset_name: str | None = None
    force: bool = False


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


@dataclass(frozen=True)
class FailureHelp:
    summary: str
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlatformPolicy:
    option: PlatformOption
    normalize: Callable[[str], str]
    option_overrides: Callable[[AuthSettings], dict[str, Any]]
    classify_error: Callable[[str], FailureHelp | None]
    diagnostics_notes: tuple[str, ...] = ()


@dataclass
class ResolvedPlan:
    platform_key: str
    platform_label: str
    requested_url: str
    normalized_url: str
    mode: str
    playlist: bool
    quality: QualityOption
    output_path: str
    has_ffmpeg: bool
    auth_description: str
    policy_notes: list[str]
    option_preview: dict[str, Any]
    detected_platform: str | None = None


@dataclass(frozen=True)
class PresetConfig:
    name: str
    platform: str | None = None
    audio_only: bool | None = None
    playlist: bool | None = None
    quality: str | None = None
    output_dir: str | None = None
    filename_template: str | None = None
    cookies: str | None = None
    cookies_from_browser: str | None = None


@dataclass(frozen=True)
class WatchConfig:
    interval: float = 0.75
    auto: bool = False
    preset: str | None = None


@dataclass(frozen=True)
class DuplicateConfig:
    default_hash: bool = False


@dataclass(frozen=True)
class AppConfig:
    presets: dict[str, PresetConfig] = field(default_factory=dict)
    watch: WatchConfig = field(default_factory=WatchConfig)
    duplicates: DuplicateConfig = field(default_factory=DuplicateConfig)


@dataclass(frozen=True)
class DownloadContext:
    original_url: str
    detected_platform: str | None
    settings: DownloadSettings
    quality: QualityOption
    info: dict[str, Any]
    plan: ResolvedPlan
    has_ffmpeg: bool


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    original_url: str
    normalized_url: str
    extractor_key: str | None
    media_id: str | None
    platform: str
    output_path: str
    mode: str
    quality_key: str
    preset_name: str | None
    downloaded_at: str
    file_size: int | None
    sha256: str | None

    @property
    def exists(self) -> bool:
        return Path(self.output_path).exists()

    @property
    def is_file(self) -> bool:
        return Path(self.output_path).is_file()


@dataclass(frozen=True)
class DuplicateGroup:
    key: str
    entries: list[HistoryEntry]


@dataclass(frozen=True)
class CleanupAction:
    keep: HistoryEntry
    remove: list[HistoryEntry]
