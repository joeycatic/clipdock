from __future__ import annotations

import pytest

from clipdock.platforms import classify_platform_error, infer_platform_from_url, normalize_url, youtube_url_has_playlist


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtu.be/demo?v=1", "youtube"),
        ("https://www.tiktok.com/@user/video/1", "tiktok"),
        ("https://www.reddit.com/r/test/comments/abc/demo/", "reddit"),
        ("https://www.instagram.com/reel/abc/?utm_source=ig_web_copy_link", "instagram"),
        ("https://x.com/example/status/1", "x"),
        ("https://www.pinterest.com/pin/12345/", "pinterest"),
    ],
)
def test_infer_platform_from_url(url: str, expected: str) -> None:
    assert infer_platform_from_url(url) == expected


def test_normalize_url_keeps_youtube_video_id_and_drops_tracking() -> None:
    normalized = normalize_url("youtube", "https://www.youtube.com/watch?v=abc123&utm_source=test&feature=share")
    assert normalized == "https://www.youtube.com/watch?v=abc123"


def test_youtube_url_has_playlist_detects_watch_and_playlist_urls() -> None:
    assert youtube_url_has_playlist("https://www.youtube.com/watch?v=abc123&list=PLdemo") is True
    assert youtube_url_has_playlist("https://www.youtube.com/playlist?list=PLdemo") is True
    assert youtube_url_has_playlist("https://www.youtube.com/watch?v=abc123") is False


@pytest.mark.parametrize(
    ("platform_key", "message", "expected"),
    [
        ("youtube", "Sign in to confirm your age", "age-restricted"),
        ("reddit", "Requested format is not available", "unusual stream layout"),
        ("instagram", "login required to view this media", "authenticated session"),
        ("x", "rate limit exceeded", "rate-limited"),
        ("tiktok", "captcha required", "blocked anonymous extraction"),
        ("pinterest", "unsupported URL", "did not expose a downloadable media stream"),
    ],
)
def test_platform_error_classification(platform_key: str, message: str, expected: str) -> None:
    help_text = classify_platform_error(platform_key, message)
    assert help_text is not None
    assert expected in help_text.summary
