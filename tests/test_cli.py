from __future__ import annotations

import builtins
import sys
import types
from pathlib import Path

import pytest

from clipdock import cli
from clipdock import ui
from clipdock.models import AppConfig, HistoryEntry


INFO = {
    "title": "Demo title",
    "uploader": "Demo creator",
    "formats": [
        {"format_id": "18", "ext": "mp4", "height": 360, "width": 640, "acodec": "aac", "vcodec": "h264"},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "aac"},
    ],
}


def test_parse_args_parses_browser_cookie_spec() -> None:
    args = cli.parse_args(["--non-interactive", "--cookies-from-browser", "chrome:Profile 1", "https://example.com"])
    assert args.auth.cookies is None
    assert args.auth.cookies_from_browser == ("chrome", "Profile 1")


def test_parse_args_parses_playlist_flag() -> None:
    args = cli.parse_args(["--non-interactive", "--playlist", "https://www.youtube.com/playlist?list=demo"])
    assert args.playlist is True


def test_parse_args_parses_output_extras() -> None:
    args = cli.parse_args(
        [
            "--non-interactive",
            "--write-subs",
            "--write-auto-subs",
            "--sub-lang",
            "en,en-US",
            "--embed-metadata",
            "--remux-video",
            "mkv",
            "https://example.com",
        ]
    )

    assert args.write_subs is True
    assert args.write_auto_subs is True
    assert args.sub_lang == "en,en-US"
    assert args.embed_metadata is True
    assert args.remux_video == "mkv"


def test_build_settings_auto_enables_playlist_for_youtube_list_url() -> None:
    args = cli.parse_args(["--non-interactive", "https://www.youtube.com/watch?v=abc123&list=PLdemo"])
    settings = cli.build_settings(args, args.url, "youtube")
    assert settings.playlist is True


def test_run_cli_simulate_does_not_download(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.parse_args(["--non-interactive", "--simulate", "--playlist", "--platform", "reddit", "https://www.reddit.com/video/demo"])
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(cli, "extract_video_info", lambda *args, **kwargs: INFO)
    monkeypatch.setattr(cli, "preview_output_path", lambda *args, **kwargs: "C:/tmp/Demo title.mp4")
    monkeypatch.setattr(cli, "download_media", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download should not run")))

    assert cli.run_cli(args) == 0
    output = capsys.readouterr().out
    assert "simulation" in output
    assert "playlist   enabled" in output
    assert "selector" in output
    assert "C:/tmp/Demo title.mp4" in output


def test_run_cli_list_formats_renders_formats(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.parse_args(["--non-interactive", "--list-formats", "--platform", "youtube", "https://youtu.be/demo"])
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "extract_video_info", lambda *args, **kwargs: INFO)
    monkeypatch.setattr(cli, "preview_output_path", lambda *args, **kwargs: "C:/tmp/Demo title.mp4")

    assert cli.run_cli(args) == 0
    output = capsys.readouterr().out
    assert "formats" in output
    assert "18" in output


def test_run_cli_debug_failure_prints_raw_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.parse_args(["--non-interactive", "--debug", "--platform", "instagram", "https://instagram.com/reel/demo"])
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise Exception("login required")

    monkeypatch.setattr(cli, "extract_video_info", fail)

    assert cli.run_cli(args) == 1
    output = capsys.readouterr().out
    assert "reason" in output
    assert "raw" in output


def test_run_interactive_uses_curses_wrapper_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_curses = types.SimpleNamespace(error=RuntimeError, wrapper=lambda fn: fn("screen"))
    fake_ui = types.SimpleNamespace(interactive_app=lambda stdscr, args: 7 if stdscr == "screen" else 0)
    monkeypatch.setitem(sys.modules, "curses", fake_curses)
    monkeypatch.setitem(sys.modules, "clipdock.ui", fake_ui)
    args = cli.parse_args(["--interactive", "https://youtu.be/demo"])

    assert cli.run_interactive(args) == 7


def test_run_interactive_falls_back_when_curses_missing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    original_import = builtins.__import__

    def fake_import(name: str, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "curses":
            raise ModuleNotFoundError("No module named '_curses'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(cli, "run_cli", lambda _args: 3)
    args = cli.parse_args(["--interactive", "https://youtu.be/demo"])

    assert cli.run_interactive(args) == 3
    assert "falling back to plain cli" in capsys.readouterr().out.lower()


def test_run_preset_command_create_list_show_delete(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    saved: dict[str, AppConfig] = {"config": AppConfig()}

    def fake_save(config: AppConfig) -> None:
        saved["config"] = config

    monkeypatch.setattr(cli, "save_config", fake_save)

    assert cli.run_preset_command(["create", "music", "--audio-only", "--quality", "mp3"], saved["config"]) == 0
    config = saved["config"]
    assert "music" in config.presets

    assert cli.run_preset_command(["list"], config) == 0
    assert "preset     music" in capsys.readouterr().out

    assert cli.run_preset_command(["show", "music"], config) == 0
    show_output = capsys.readouterr().out
    assert "audio_only True" in show_output
    assert "quality    mp3" in show_output

    assert cli.run_preset_command(["delete", "music"], config) == 0
    assert "deleted" in capsys.readouterr().out


def test_dispatch_use_subcommand_applies_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_download_command(args: object) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli, "run_download_command", fake_run_download_command)
    config = AppConfig()
    config = AppConfig(presets={"music": cli.PresetConfig(name="music", quality="mp3", audio_only=True)})

    assert cli.dispatch(["use", "music", "https://youtu.be/demo"], config) == 0
    args = captured["args"]
    assert args is not None
    assert getattr(args, "quality") == "mp3"
    assert getattr(args, "audio_only") is True


def test_build_command_items_uses_grouped_main_menu() -> None:
    settings = cli.DownloadSettings(platform="youtube", url="https://youtu.be/demo")
    quality = cli.QualityOption(key="best", label="Best available", description="best", format_selector="best")

    commands = ui.build_command_items({"title": "Demo", "formats": []}, settings, quality)
    keys = [key for key, *_rest in commands]

    assert keys == ["source_group", "download_group", "output_group", "inspect_group", "run", "quit"]


def test_run_history_command_renders_entries(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    entry = HistoryEntry(
        id=1,
        original_url="https://youtu.be/demo",
        normalized_url="https://youtu.be/demo",
        extractor_key="Youtube",
        media_id="abc123",
        platform="youtube",
        output_path="C:/tmp/demo.mp4",
        mode="video",
        quality_key="best",
        preset_name="music",
        downloaded_at="2026-05-07T12:00:00+00:00",
        file_size=1024,
        sha256=None,
    )
    monkeypatch.setattr(cli, "load_entries", lambda **_kwargs: [entry])
    monkeypatch.setattr(Path, "exists", lambda self: True)

    assert cli.run_history_command([], AppConfig()) == 0
    output = capsys.readouterr().out
    assert "id         1" in output
    assert "identity   Youtube:abc123" in output
    assert "preset     music" in output


def test_run_doctor_command_reports_checks(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "collect_doctor_checks", lambda: [("ffmpeg", True, "available"), ("clipboard", True, "clipboard access available")])

    assert cli.run_doctor_command([], AppConfig()) == 0
    output = capsys.readouterr().out
    assert "doctor" in output
    assert "ffmpeg     ok" in output
    assert "clipboard  ok" in output


def test_run_batch_command_summarizes_downloaded_duplicates_and_failures(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    contexts = {
        "https://one.example": types.SimpleNamespace(settings=types.SimpleNamespace(force=False)),
        "https://dup.example": types.SimpleNamespace(settings=types.SimpleNamespace(force=False)),
    }
    duplicate_entry = types.SimpleNamespace(output_path="C:/tmp/existing.mp4")

    monkeypatch.setattr(cli, "load_batch_urls", lambda _path: list(contexts) + ["https://bad.example"])
    monkeypatch.setattr(cli, "build_download_context", lambda args, url: contexts[url])
    monkeypatch.setattr(cli, "find_duplicate_for_context", lambda context: duplicate_entry if context is contexts["https://dup.example"] else None)
    monkeypatch.setattr(cli, "run_download_from_context", lambda *args, **kwargs: "C:/tmp/out.mp4")

    def fail_context(_args: object, url: str) -> object:
        if url == "https://bad.example":
            raise Exception("boom")
        return contexts[url]

    monkeypatch.setattr(cli, "build_download_context", fail_context)

    assert cli.run_batch_command(["urls.txt"], AppConfig()) == 1
    output = capsys.readouterr().out
    assert "downloaded=1" in output
    assert "skipped_duplicate=1" in output
    assert "failed=1" in output


def test_run_history_export_command_renders_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    entry = HistoryEntry(
        id=1,
        original_url="https://youtu.be/demo",
        normalized_url="https://youtu.be/demo",
        extractor_key="Youtube",
        media_id="abc123",
        platform="youtube",
        output_path="C:/tmp/demo.mp4",
        mode="video",
        quality_key="best",
        preset_name="music",
        downloaded_at="2026-05-07T12:00:00+00:00",
        file_size=1024,
        sha256=None,
    )
    monkeypatch.setattr(cli, "load_entries", lambda **_kwargs: [entry])
    monkeypatch.setattr(Path, "exists", lambda self: True)

    assert cli.run_history_command(["export", "--format", "json"], AppConfig()) == 0
    output = capsys.readouterr().out
    assert '"platform": "youtube"' in output
    assert '"status": "present"' in output


def test_run_watch_status_command_reports_running(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_clear_stale_watch_pid", lambda: None)
    monkeypatch.setattr(cli, "_watch_pid_value", lambda: 123)
    monkeypatch.setattr(cli, "_watch_process_alive", lambda pid: pid == 123)

    assert cli.run_watch_command(["status"], AppConfig()) == 0
    output = capsys.readouterr().out
    assert "running pid=123" in output
