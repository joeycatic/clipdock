from __future__ import annotations

from typing import Any

from clipdock.downloader import assemble_ydl_options, build_quality_options, build_video_selector, encode_playlist_items, extract_video_info
from clipdock.models import AuthSettings, DownloadSettings, QualityOption


def test_build_video_selector_generic_and_reddit() -> None:
    assert build_video_selector(True, platform_key="youtube") == "bestvideo+bestaudio/best"
    assert build_video_selector(False, 720, platform_key="youtube") == "best[height<=720]/bestvideo[height<=720]/best"
    assert build_video_selector(True, platform_key="reddit") == "bv*+ba/b/bv*"
    assert build_video_selector(False, 480, platform_key="reddit") == "b[height<=480]/bv*[height<=480]/b/bv*"


def test_assemble_ydl_options_includes_auth_and_postprocessors() -> None:
    settings = DownloadSettings(
        platform="reddit",
        url="https://www.reddit.com/video/example",
        playlist=True,
        output_dir="C:/tmp",
        filename_template="%(title)s.%(ext)s",
        auth=AuthSettings(cookies="cookies.txt", cookies_from_browser=("chrome", "Default")),
    )
    quality = QualityOption(
        key="mp3",
        label="MP3",
        description="audio",
        format_selector="bestaudio/best",
        postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        final_extension="mp3",
    )

    options = assemble_ydl_options(settings, quality, quiet=True, debug=False, skip_download=True)

    assert options["format"] == "bestaudio/best"
    assert options["cookiefile"] == "cookies.txt"
    assert options["cookiesfrombrowser"] == ("chrome", "Default")
    assert options["postprocessors"] == [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
    assert options["skip_download"] is True
    assert options["noplaylist"] is False
    assert options["ignoreerrors"] is True


def test_encode_playlist_items_sorts_and_deduplicates() -> None:
    assert encode_playlist_items([4, 2, 2, 1]) == "1,2,4"
    assert encode_playlist_items([]) is None
    assert encode_playlist_items(None) is None


def test_assemble_ydl_options_includes_playlist_item_filter_when_queue_trimmed() -> None:
    settings = DownloadSettings(
        platform="youtube",
        url="https://www.youtube.com/playlist?list=demo",
        playlist=True,
        playlist_items=[5, 2, 2],
        output_dir="C:/tmp",
        filename_template="%(title)s.%(ext)s",
    )

    options = assemble_ydl_options(settings, quiet=True, debug=False, skip_download=True)

    assert options["playlist_items"] == "2,5"


def test_extract_video_info_uses_tolerant_playlist_metadata_options(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}

    class FakeYoutubeDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            captured.update(opts)

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            assert url == "https://www.youtube.com/watch?v=demo&list=PLdemo"
            assert download is False
            return {"_type": "playlist", "title": "Demo playlist", "entries": [{"title": "First item"}]}

        def sanitize_info(self, info: dict[str, Any]) -> dict[str, Any]:
            return info

    monkeypatch.setattr("clipdock.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL)

    info = extract_video_info(
        "https://www.youtube.com/watch?v=demo&list=PLdemo",
        "youtube",
        AuthSettings(),
        playlist=True,
    )

    assert info["_type"] == "playlist"
    assert captured["noplaylist"] is False
    assert captured["ignoreerrors"] is True
    assert captured["extract_flat"] == "in_playlist"


def test_build_quality_options_adds_fallback_caps_when_playlist_metadata_has_no_formats() -> None:
    video_options, _audio_options = build_quality_options(
        {"_type": "playlist", "title": "Demo playlist", "entries": [{"title": "One"}]},
        has_ffmpeg=False,
        platform_key="youtube",
    )

    keys = [option.key for option in video_options]
    assert "best" in keys
    assert "1080p" in keys
    assert "720p" in keys
    assert "480p" in keys
