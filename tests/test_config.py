from __future__ import annotations

from clipdock import cli
from clipdock.config import delete_preset, load_config, save_config, upsert_preset
from clipdock.models import AppConfig, PresetConfig


def test_save_and_load_config_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = tmp_path / "config.toml"
    config = AppConfig(
        presets={
            "music": PresetConfig(
                name="music",
                audio_only=True,
                quality="mp3",
                output_dir="~/Music/Clips",
                filename_template="%(uploader)s - %(title)s.%(ext)s",
                write_subs=True,
                sub_lang="en",
                remux_video="mkv",
            )
        }
    )

    save_config(config, path=config_file)
    loaded = load_config(config_file)

    assert loaded.presets["music"].audio_only is True
    assert loaded.presets["music"].quality == "mp3"
    assert loaded.presets["music"].output_dir == "~/Music/Clips"
    assert loaded.presets["music"].write_subs is True
    assert loaded.presets["music"].sub_lang == "en"
    assert loaded.presets["music"].remux_video == "mkv"


def test_resolve_download_namespace_applies_preset_then_cli_override() -> None:
    config = AppConfig(
        presets={
            "music": PresetConfig(
                name="music",
                audio_only=True,
                quality="mp3",
                output_dir="~/Music/Clips",
                write_thumbnail=True,
            )
        }
    )
    raw = cli.create_download_parser(raw=True).parse_args(["--preset", "music", "--quality", "m4a", "--write-info-json", "https://youtu.be/demo"])

    resolved = cli.resolve_download_namespace(raw, config)

    assert resolved.audio_only is True
    assert resolved.quality == "m4a"
    assert resolved.output_dir == "~/Music/Clips"
    assert resolved.preset == "music"
    assert resolved.write_thumbnail is True
    assert resolved.write_info_json is True


def test_upsert_and_delete_preset_update_config() -> None:
    config = AppConfig()
    updated = upsert_preset(config, PresetConfig(name="music", quality="mp3"))
    assert "music" in updated.presets

    deleted = delete_preset(updated, "music")
    assert "music" not in deleted.presets
