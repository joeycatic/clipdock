from __future__ import annotations

import hashlib
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import CleanupAction, DownloadSettings, DuplicateGroup, HistoryEntry, QualityOption
from .paths import history_path
from .platforms import normalize_url


SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY,
    original_url TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    extractor_key TEXT NULL,
    media_id TEXT NULL,
    platform TEXT NOT NULL,
    output_path TEXT NOT NULL,
    mode TEXT NOT NULL,
    quality_key TEXT NOT NULL,
    preset_name TEXT NULL,
    downloaded_at TEXT NOT NULL,
    file_size INTEGER NULL,
    sha256 TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_downloads_normalized_url ON downloads(normalized_url);
CREATE INDEX IF NOT EXISTS idx_downloads_sha256 ON downloads(sha256);
CREATE INDEX IF NOT EXISTS idx_downloads_downloaded_at ON downloads(downloaded_at);
"""


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(downloads)").fetchall()}
    if "extractor_key" not in columns:
        connection.execute("ALTER TABLE downloads ADD COLUMN extractor_key TEXT NULL")
    if "media_id" not in columns:
        connection.execute("ALTER TABLE downloads ADD COLUMN media_id TEXT NULL")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_downloads_media_identity ON downloads(extractor_key, media_id)")


def connect_history(path: Path | None = None) -> sqlite3.Connection:
    target = path or history_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    _ensure_schema(connection)
    return connection


def _entry_from_row(row: sqlite3.Row) -> HistoryEntry:
    return HistoryEntry(
        id=int(row["id"]),
        original_url=str(row["original_url"]),
        normalized_url=str(row["normalized_url"]),
        extractor_key=row["extractor_key"],
        media_id=row["media_id"],
        platform=str(row["platform"]),
        output_path=str(row["output_path"]),
        mode=str(row["mode"]),
        quality_key=str(row["quality_key"]),
        preset_name=row["preset_name"],
        downloaded_at=str(row["downloaded_at"]),
        file_size=row["file_size"],
        sha256=row["sha256"],
    )


def entry_to_dict(entry: HistoryEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "original_url": entry.original_url,
        "normalized_url": entry.normalized_url,
        "extractor_key": entry.extractor_key,
        "media_id": entry.media_id,
        "platform": entry.platform,
        "output_path": entry.output_path,
        "mode": entry.mode,
        "quality_key": entry.quality_key,
        "preset_name": entry.preset_name,
        "downloaded_at": entry.downloaded_at,
        "file_size": entry.file_size,
        "sha256": entry.sha256,
        "status": "present" if Path(entry.output_path).exists() else "missing",
    }


def extract_media_identity(info: dict[str, Any]) -> tuple[str | None, str | None]:
    extractor_key = info.get("extractor_key") or info.get("extractor")
    media_id = info.get("id")
    return (
        str(extractor_key) if extractor_key else None,
        str(media_id) if media_id else None,
    )


def duplicate_lookup_key(entry: HistoryEntry | tuple[str | None, str | None, str]) -> str:
    if isinstance(entry, HistoryEntry):
        extractor_key = entry.extractor_key
        media_id = entry.media_id
        normalized_url = entry.normalized_url
    else:
        extractor_key, media_id, normalized_url = entry
    if extractor_key and media_id:
        return f"{extractor_key}:{media_id}"
    return normalized_url


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_download(
    settings: DownloadSettings,
    quality: QualityOption,
    original_url: str,
    output_path: str,
    *,
    info: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> HistoryEntry | None:
    output = Path(output_path).expanduser()
    if not output.exists():
        return None
    file_size = output.stat().st_size if output.is_file() else None
    normalized = normalize_url(settings.platform, original_url)
    downloaded_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    extractor_key, media_id = extract_media_identity(info or {})
    with connect_history(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO downloads (
                original_url, normalized_url, extractor_key, media_id, platform, output_path, mode,
                quality_key, preset_name, downloaded_at, file_size, sha256
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                original_url,
                normalized,
                extractor_key,
                media_id,
                settings.platform,
                str(output),
                settings.mode,
                quality.key,
                settings.preset_name,
                downloaded_at,
                file_size,
                None,
            ),
        )
        row = connection.execute("SELECT * FROM downloads WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _entry_from_row(row) if row is not None else None


def latest_duplicate(
    normalized_url: str,
    *,
    extractor_key: str | None = None,
    media_id: str | None = None,
    db_path: Path | None = None,
    existing_only: bool = True,
) -> HistoryEntry | None:
    with connect_history(db_path) as connection:
        if extractor_key and media_id:
            rows = connection.execute(
                """
                SELECT * FROM downloads
                WHERE extractor_key = ? AND media_id = ?
                ORDER BY downloaded_at DESC, id DESC
                """,
                (extractor_key, media_id),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM downloads WHERE normalized_url = ? ORDER BY downloaded_at DESC, id DESC",
                (normalized_url,),
            ).fetchall()
    entries = [_entry_from_row(row) for row in rows]
    if existing_only:
        entries = [entry for entry in entries if Path(entry.output_path).exists()]
    return entries[0] if entries else None


def load_entries(
    *,
    db_path: Path | None = None,
    entry_id: int | None = None,
    platform: str | None = None,
    preset_name: str | None = None,
    query_text: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[HistoryEntry]:
    query = "SELECT * FROM downloads"
    filters: list[str] = []
    params: list[Any] = []
    if entry_id is not None:
        filters.append("id = ?")
        params.append(entry_id)
    if platform:
        filters.append("platform = ?")
        params.append(platform)
    if preset_name:
        filters.append("preset_name = ?")
        params.append(preset_name)
    if query_text:
        filters.append("(original_url LIKE ? OR normalized_url LIKE ? OR output_path LIKE ?)")
        like = f"%{query_text}%"
        params.extend([like, like, like])
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY downloaded_at DESC, id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with connect_history(db_path) as connection:
        rows = connection.execute(query, params).fetchall()
    entries = [_entry_from_row(row) for row in rows]
    if status in {"present", "missing"}:
        wanted_exists = status == "present"
        entries = [entry for entry in entries if Path(entry.output_path).exists() is wanted_exists]
    return entries


def get_entry(entry_id: int, *, db_path: Path | None = None) -> HistoryEntry | None:
    entries = load_entries(db_path=db_path, entry_id=entry_id, limit=1)
    return entries[0] if entries else None


def _hash_entries(entries: Iterable[HistoryEntry], *, db_path: Path | None = None) -> list[HistoryEntry]:
    pending: list[tuple[int, str]] = []
    resolved: list[HistoryEntry] = []
    for entry in entries:
        output = Path(entry.output_path)
        if entry.sha256 or not output.is_file():
            resolved.append(entry)
            continue
        digest = compute_sha256(output)
        pending.append((entry.id, digest))
        resolved.append(
            HistoryEntry(
                id=entry.id,
                original_url=entry.original_url,
                normalized_url=entry.normalized_url,
                extractor_key=entry.extractor_key,
                media_id=entry.media_id,
                platform=entry.platform,
                output_path=entry.output_path,
                mode=entry.mode,
                quality_key=entry.quality_key,
                preset_name=entry.preset_name,
                downloaded_at=entry.downloaded_at,
                file_size=entry.file_size,
                sha256=digest,
            )
        )
    if pending:
        with connect_history(db_path) as connection:
            connection.executemany("UPDATE downloads SET sha256 = ? WHERE id = ?", [(digest, entry_id) for entry_id, digest in pending])
    return resolved


def duplicate_groups(use_hash: bool = False, *, db_path: Path | None = None) -> list[DuplicateGroup]:
    entries = load_entries(db_path=db_path)
    if use_hash:
        entries = _hash_entries(entries, db_path=db_path)
    grouped: dict[str, list[HistoryEntry]] = defaultdict(list)
    for entry in entries:
        key = entry.sha256 if use_hash else duplicate_lookup_key(entry)
        if not key:
            continue
        grouped[key].append(entry)
    groups = [DuplicateGroup(key=key, entries=value) for key, value in grouped.items() if len(value) > 1]
    groups.sort(key=lambda group: group.entries[0].downloaded_at, reverse=True)
    return groups


def cleanup_actions(use_hash: bool = False, *, db_path: Path | None = None) -> list[CleanupAction]:
    actions: list[CleanupAction] = []
    for group in duplicate_groups(use_hash=use_hash, db_path=db_path):
        existing_files = [entry for entry in group.entries if Path(entry.output_path).is_file()]
        if len(existing_files) < 2:
            continue
        existing_files.sort(key=lambda entry: (entry.downloaded_at, entry.id), reverse=True)
        actions.append(CleanupAction(keep=existing_files[0], remove=existing_files[1:]))
    return actions


def delete_entries(entries: Iterable[HistoryEntry], *, db_path: Path | None = None) -> None:
    entry_ids = [entry.id for entry in entries]
    if not entry_ids:
        return
    with connect_history(db_path) as connection:
        placeholders = ", ".join("?" for _ in entry_ids)
        connection.execute(f"DELETE FROM downloads WHERE id IN ({placeholders})", entry_ids)


def remove_file(entry: HistoryEntry) -> None:
    output = Path(entry.output_path)
    if output.is_file():
        output.unlink()


def existing_path_size(path: Path) -> int | None:
    try:
        return os.path.getsize(path) if path.is_file() else None
    except OSError:
        return None
