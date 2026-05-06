from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import AuthSettings, FailureHelp, PlatformOption, PlatformPolicy


def _normalize_generic(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return url.strip()
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith("utm_")]
    cleaned = parsed._replace(fragment="", query=urlencode(query))
    return urlunparse(cleaned)


def _normalize_youtube(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return url.strip()
    query_keys = {"v", "list", "index", "t", "start"}
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key in query_keys]
    cleaned = parsed._replace(fragment="", query=urlencode(query))
    return urlunparse(cleaned)


def _normalize_instagram(url: str) -> str:
    return _normalize_generic(url).rstrip("/")


def _normalize_reddit(url: str) -> str:
    normalized = _normalize_generic(url)
    if normalized.endswith("/?"):
        normalized = normalized[:-2]
    return normalized.rstrip("/") if "/comments/" in normalized else normalized


def _normalize_x(url: str) -> str:
    normalized = _normalize_generic(url)
    parsed = urlparse(normalized)
    host = parsed.netloc.lower().replace("mobile.twitter.com", "twitter.com")
    cleaned = parsed._replace(netloc=host, fragment="")
    return urlunparse(cleaned)


def _normalize_tiktok(url: str) -> str:
    return _normalize_generic(url)


def _normalize_pinterest(url: str) -> str:
    return _normalize_generic(url).rstrip("/")


def _base_option_overrides(auth: AuthSettings) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if auth.cookies:
        options["cookiefile"] = auth.cookies
    if auth.cookies_from_browser:
        browser, profile = auth.cookies_from_browser
        options["cookiesfrombrowser"] = (browser, profile) if profile else (browser,)
    return options


def _match_any(message: str, *needles: str) -> bool:
    lowered = message.lower()
    return any(needle in lowered for needle in needles)


def _youtube_error(message: str) -> FailureHelp | None:
    if _match_any(message, "age-restricted", "sign in to confirm your age"):
        return FailureHelp("YouTube marked the media as age-restricted.", ("Retry with --cookies-from-browser chrome or a cookie export.",))
    if _match_any(message, "members-only", "join this channel", "members only"):
        return FailureHelp("This YouTube media appears to require channel membership.", ("Try a signed-in browser session with active membership cookies.",))
    if _match_any(message, "this live event will begin", "live stream is offline", "live stream"):
        return FailureHelp("The YouTube URL points to live content that is not currently downloadable.", ("Retry when the stream is active or after the archive is published.",))
    if _match_any(message, "not available in your country", "geo restricted", "georestricted"):
        return FailureHelp("YouTube blocked this media for your region.", ("Try a browser session that can access the media or a network path allowed by the source.",))
    return None


def _reddit_error(message: str) -> FailureHelp | None:
    if _match_any(message, "requested format is not available", "format is not available"):
        return FailureHelp("Reddit exposed an unusual stream layout for this post.", ("Retry with --simulate or --list-formats to inspect the source plan.", "If only split streams exist, install ffmpeg for muxing support.",))
    if _match_any(message, "403", "forbidden", "login", "private", "quarantined"):
        return FailureHelp("Reddit appears to require an authenticated session for this media.", ("Retry with --cookies-from-browser chrome or a Reddit cookie export.",))
    return None


def _instagram_error(message: str) -> FailureHelp | None:
    if _match_any(message, "login required", "checkpoint", "not logged in", "requires login"):
        return FailureHelp("Instagram requires an authenticated session for this media.", ("Retry with --cookies-from-browser chrome or an exported cookie file.",))
    if _match_any(message, "restricted", "not available", "region"):
        return FailureHelp("Instagram restricted this media in the current session or region.", ("Retry with browser cookies from an account that can access the post.",))
    return None


def _x_error(message: str) -> FailureHelp | None:
    if _match_any(message, "login", "sign in", "authorization", "not authorized"):
        return FailureHelp("X requires a signed-in session for this post.", ("Retry with --cookies-from-browser chrome or a cookie export from a signed-in browser.",))
    if _match_any(message, "rate limit", "too many requests"):
        return FailureHelp("X rate-limited the extractor.", ("Retry later or use a signed-in browser cookie source.",))
    if _match_any(message, "sensitive", "restricted", "unavailable"):
        return FailureHelp("X marked this media as restricted.", ("Retry with browser cookies from an account that can view the post.",))
    return None


def _tiktok_error(message: str) -> FailureHelp | None:
    if _match_any(message, "login", "verify", "captcha", "rate limit"):
        return FailureHelp("TikTok blocked anonymous extraction for this media.", ("Retry with --cookies-from-browser chrome or a cookie export.",))
    if _match_any(message, "share", "redirect"):
        return FailureHelp("TikTok share-link resolution failed.", ("Paste the canonical TikTok video URL when possible.",))
    return None


def _pinterest_error(message: str) -> FailureHelp | None:
    if _match_any(message, "login", "private", "restricted"):
        return FailureHelp("Pinterest appears to require a signed-in session for this pin.", ("Retry with --cookies-from-browser chrome or a cookie export.",))
    if _match_any(message, "unsupported", "unable to extract"):
        return FailureHelp("Pinterest did not expose a downloadable media stream for this pin.", ("Use --list-formats to confirm what the extractor can currently see.",))
    return None


PLATFORMS = [
    PlatformPolicy(
        option=PlatformOption(
            key="youtube",
            label="YouTube",
            description="Videos, shorts, music, public posts, playlists",
            url_prompt="Paste a YouTube video or playlist URL",
            domains=("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"),
        ),
        normalize=_normalize_youtube,
        option_overrides=_base_option_overrides,
        classify_error=_youtube_error,
        diagnostics_notes=("YouTube failures often improve with browser cookies for age- or membership-gated media.",),
    ),
    PlatformPolicy(
        option=PlatformOption(
            key="tiktok",
            label="TikTok",
            description="Videos and share links",
            url_prompt="Paste a TikTok URL",
            domains=("tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com"),
        ),
        normalize=_normalize_tiktok,
        option_overrides=_base_option_overrides,
        classify_error=_tiktok_error,
        diagnostics_notes=("Prefer canonical TikTok URLs when share links bounce through several redirects.",),
    ),
    PlatformPolicy(
        option=PlatformOption(
            key="reddit",
            label="Reddit",
            description="Posts, hosted videos, v.redd.it",
            url_prompt="Paste a Reddit post URL",
            domains=("reddit.com", "www.reddit.com", "old.reddit.com", "v.redd.it"),
        ),
        normalize=_normalize_reddit,
        option_overrides=_base_option_overrides,
        classify_error=_reddit_error,
        diagnostics_notes=("Reddit often exposes split video/audio streams; ffmpeg improves muxing coverage.",),
    ),
    PlatformPolicy(
        option=PlatformOption(
            key="instagram",
            label="Instagram",
            description="Posts, reels, stories, highlights",
            url_prompt="Paste an Instagram post, reel, or story URL",
            domains=("instagram.com", "www.instagram.com"),
        ),
        normalize=_normalize_instagram,
        option_overrides=_base_option_overrides,
        classify_error=_instagram_error,
        diagnostics_notes=("Instagram frequently requires a signed-in browser session for reels and stories.",),
    ),
    PlatformPolicy(
        option=PlatformOption(
            key="x",
            label="X",
            description="x.com and twitter.com posts",
            url_prompt="Paste an X or Twitter post URL",
            domains=("x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"),
        ),
        normalize=_normalize_x,
        option_overrides=_base_option_overrides,
        classify_error=_x_error,
        diagnostics_notes=("X extraction is more stable with a signed-in browser session.",),
    ),
    PlatformPolicy(
        option=PlatformOption(
            key="pinterest",
            label="Pinterest",
            description="Pins and video pins",
            url_prompt="Paste a Pinterest pin URL",
            domains=("pinterest.com", "www.pinterest.com", "pin.it"),
        ),
        normalize=_normalize_pinterest,
        option_overrides=_base_option_overrides,
        classify_error=_pinterest_error,
        diagnostics_notes=("Pinterest extraction varies between public pins and signed-in-only boards.",),
    ),
]
PLATFORM_MAP = {platform.option.key: platform for platform in PLATFORMS}


def get_platform(platform_key: str) -> PlatformPolicy:
    return PLATFORM_MAP[platform_key]


def infer_platform_from_url(url: str) -> str | None:
    lowered = url.strip().lower()
    if not lowered:
        return None
    for platform in PLATFORMS:
        if any(domain in lowered for domain in platform.option.domains):
            return platform.option.key
    return None


def youtube_url_has_playlist(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme and "youtube" not in parsed.netloc.lower() and "youtu.be" not in parsed.netloc.lower():
        return False
    return any(key == "list" and bool(value) for key, value in parse_qsl(parsed.query, keep_blank_values=True))


def normalize_url(platform_key: str, url: str) -> str:
    return get_platform(platform_key).normalize(url)


def platform_notes(platform_key: str) -> list[str]:
    return list(get_platform(platform_key).diagnostics_notes)


def classify_platform_error(platform_key: str, error: Exception | str) -> FailureHelp | None:
    message = str(error)
    return get_platform(platform_key).classify_error(message)


def platform_overrides(platform_key: str, auth: AuthSettings) -> dict[str, Any]:
    return get_platform(platform_key).option_overrides(auth)


def option_from_policy(platform_key: str) -> PlatformOption:
    return replace(get_platform(platform_key).option)
