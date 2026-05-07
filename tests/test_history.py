from __future__ import annotations

from pathlib import Path
import sqlite3

from clipdock.history import cleanup_actions, connect_history, duplicate_groups, latest_duplicate, record_download
from clipdock.models import AuthSettings, DownloadSettings, QualityOption


QUALITY = QualityOption(key="best", label="Best", description="best", format_selector="best")


def make_settings(url: str) -> DownloadSettings:
    return DownloadSettings(
        platform="youtube",
        url=url,
        mode="video",
        output_dir="C:/tmp",
        filename_template="%(title)s.%(ext)s",
        auth=AuthSettings(),
    )


def test_record_download_and_latest_duplicate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "history.sqlite3"
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    record_download(make_settings("https://www.youtube.com/watch?v=abc123"), QUALITY, "https://www.youtube.com/watch?v=abc123", str(first), db_path=db_path)
    newest = record_download(make_settings("https://www.youtube.com/watch?v=abc123"), QUALITY, "https://www.youtube.com/watch?v=abc123", str(second), db_path=db_path)

    duplicate = latest_duplicate("https://www.youtube.com/watch?v=abc123", db_path=db_path)

    assert newest is not None
    assert duplicate is not None
    assert duplicate.output_path == str(second)


def test_duplicate_groups_by_hash_and_cleanup_actions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "history.sqlite3"
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"same-bytes")
    second.write_bytes(b"same-bytes")

    record_download(make_settings("https://www.youtube.com/watch?v=one"), QUALITY, "https://www.youtube.com/watch?v=one", str(first), db_path=db_path)
    record_download(make_settings("https://www.youtube.com/watch?v=two"), QUALITY, "https://www.youtube.com/watch?v=two", str(second), db_path=db_path)

    groups = duplicate_groups(use_hash=True, db_path=db_path)
    actions = cleanup_actions(use_hash=True, db_path=db_path)

    assert len(groups) == 1
    assert len(groups[0].entries) == 2
    assert len(actions) == 1
    assert Path(actions[0].keep.output_path).exists()
    assert len(actions[0].remove) == 1


def test_latest_duplicate_prefers_media_identity(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "history.sqlite3"
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    info = {"extractor_key": "Youtube", "id": "video-123"}
    record_download(make_settings("https://www.youtube.com/watch?v=abc123"), QUALITY, "https://www.youtube.com/watch?v=abc123", str(first), info=info, db_path=db_path)
    newest = record_download(make_settings("https://youtu.be/alias123"), QUALITY, "https://youtu.be/alias123", str(second), info=info, db_path=db_path)

    duplicate = latest_duplicate("https://youtu.be/alias123", extractor_key="Youtube", media_id="video-123", db_path=db_path)

    assert newest is not None
    assert duplicate is not None
    assert duplicate.output_path == str(second)
    assert duplicate.media_id == "video-123"


def test_connect_history_migrates_legacy_database(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "history.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE downloads (
                id INTEGER PRIMARY KEY,
                original_url TEXT NOT NULL,
                normalized_url TEXT NOT NULL,
                platform TEXT NOT NULL,
                output_path TEXT NOT NULL,
                mode TEXT NOT NULL,
                quality_key TEXT NOT NULL,
                preset_name TEXT NULL,
                downloaded_at TEXT NOT NULL,
                file_size INTEGER NULL,
                sha256 TEXT NULL
            );
            """
        )

    with connect_history(db_path) as connection:
        columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(downloads)").fetchall()}

    assert "extractor_key" in columns
    assert "media_id" in columns
