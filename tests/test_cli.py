from __future__ import annotations

import builtins
import sys
import types

import pytest

from clipdock import cli


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
