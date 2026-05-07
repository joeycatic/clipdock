from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import tomli_w

from .models import AppConfig, DuplicateConfig, PresetConfig, WatchConfig
from .paths import config_path


def _load_preset(name: str, data: dict[str, Any]) -> PresetConfig:
    return PresetConfig(
        name=name,
        platform=_as_string(data.get("platform")),
        audio_only=_as_bool(data.get("audio_only")),
        playlist=_as_bool(data.get("playlist")),
        quality=_as_string(data.get("quality")),
        output_dir=_as_string(data.get("output_dir")),
        filename_template=_as_string(data.get("filename_template")),
        cookies=_as_string(data.get("cookies")),
        cookies_from_browser=_as_string(data.get("cookies_from_browser")),
    )


def _as_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, (float, int)) and float(value) > 0:
        return float(value)
    return default


def load_config(path: Path | None = None) -> AppConfig:
    target = path or config_path()
    if not target.exists():
        return AppConfig()
    with target.open("rb") as handle:
        raw = tomllib.load(handle)

    presets_block = raw.get("presets") if isinstance(raw, dict) else None
    presets: dict[str, PresetConfig] = {}
    if isinstance(presets_block, dict):
        for name, data in presets_block.items():
            if isinstance(name, str) and isinstance(data, dict):
                presets[name] = _load_preset(name, data)

    watch_block = raw.get("watch") if isinstance(raw.get("watch"), dict) else {}
    duplicates_block = raw.get("duplicates") if isinstance(raw.get("duplicates"), dict) else {}
    return AppConfig(
        presets=presets,
        watch=WatchConfig(
            interval=_as_float(watch_block.get("interval"), 0.75),
            auto=bool(watch_block.get("auto", False)),
            preset=_as_string(watch_block.get("preset")),
        ),
        duplicates=DuplicateConfig(default_hash=bool(duplicates_block.get("default_hash", False))),
    )


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "presets": {
            name: {
                key: value
                for key, value in {
                    "platform": preset.platform,
                    "audio_only": preset.audio_only,
                    "playlist": preset.playlist,
                    "quality": preset.quality,
                    "output_dir": preset.output_dir,
                    "filename_template": preset.filename_template,
                    "cookies": preset.cookies,
                    "cookies_from_browser": preset.cookies_from_browser,
                }.items()
                if value is not None
            }
            for name, preset in sorted(config.presets.items())
        },
        "watch": {
            key: value
            for key, value in {
                "interval": config.watch.interval,
                "auto": config.watch.auto,
                "preset": config.watch.preset,
            }.items()
            if value is not None
        },
        "duplicates": {
            "default_hash": config.duplicates.default_hash,
        },
    }
    with target.open("wb") as handle:
        handle.write(tomli_w.dumps(payload).encode("utf-8"))
    return target


def get_preset(config: AppConfig, name: str) -> PresetConfig | None:
    return config.presets.get(name)


def upsert_preset(config: AppConfig, preset: PresetConfig) -> AppConfig:
    presets = dict(config.presets)
    presets[preset.name] = preset
    return replace(config, presets=presets)


def delete_preset(config: AppConfig, name: str) -> AppConfig:
    presets = dict(config.presets)
    presets.pop(name, None)
    return replace(config, presets=presets)
