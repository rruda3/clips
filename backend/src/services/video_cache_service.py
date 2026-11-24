"""Manage cached source videos stored under TEMP_DIR (youtube-cache and uploads)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import logging
import re
import shutil
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config

logger = logging.getLogger(__name__)

_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_ACTIVE_STATUSES = {"pending", "queued", "processing"}
_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}


def _extract_youtube_id(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if "youtube.com" in host or "youtube-nocookie.com" in host:
        qs = parse_qs(parsed.query)
        if qs.get("v"):
            vid = qs["v"][0]
            if _YT_ID_RE.match(vid):
                return vid
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed", "v"} and _YT_ID_RE.match(parts[1]):
            return parts[1]
    if host == "youtu.be":
        vid = parsed.path.strip("/").split("/")[0]
        if _YT_ID_RE.match(vid):
            return vid
    return None


def _extract_upload_id(url: str | None) -> str | None:
    if not url or not url.startswith("upload://"):
        return None
    name = url[len("upload://"):].strip()
    stem = Path(name).stem
    return stem if _UPLOAD_ID_RE.match(stem) else None


def _dir_size_and_stats(path: Path) -> tuple[int, int, float]:
    total = 0
    count = 0
    latest = path.stat().st_mtime if path.exists() else 0.0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                st = item.stat()
            except OSError:
                continue
            total += st.st_size
            count += 1
            if st.st_mtime > latest:
                latest = st.st_mtime
    return total, count, latest


def _group_upload_files(uploads_dir: Path) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    if not uploads_dir.is_dir():
        return groups
    for entry in uploads_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        clean_name = name[1:] if name.startswith(".") else name
        # UUID / upload stem is everything before the first dot
        stem = clean_name.split(".", 1)[0]
        if not _UPLOAD_ID_RE.match(stem):
            continue
        groups.setdefault(stem, []).append(entry)
    return groups


async def _load_source_index(db: AsyncSession) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build lookup maps by youtube video_id and upload stem from Postgres tasks + sources."""
    result = await db.execute(
        text(
            """
            SELECT t.id, t.user_id, t.status, t.created_at, s.type, s.title, s.url
            FROM tasks t
            LEFT JOIN sources s ON t.source_id = s.id
            ORDER BY t.created_at DESC
            """
        )
    )
    yt_map: dict[str, dict[str, Any]] = {}
    upload_map: dict[str, dict[str, Any]] = {}

    def _merge(bucket: dict[str, dict[str, Any]], key: str, row: Any) -> None:
        entry = bucket.setdefault(
            key,
            {
                "title": row.title,
                "url": row.url,
                "user_ids": set(),
                "task_ids": [],
                "in_use": False,
            },
        )
        if not entry["title"] and row.title:
            entry["title"] = row.title
        if not entry["url"] and row.url:
            entry["url"] = row.url
        if row.user_id:
            entry["user_ids"].add(str(row.user_id))
        entry["task_ids"].append(str(row.id))
        if (row.status or "").lower() in _ACTIVE_STATUSES:
            entry["in_use"] = True

    for row in result.fetchall():
        yt_id = _extract_youtube_id(row.url)
        if yt_id:
            _merge(yt_map, yt_id, row)
        up_id = _extract_upload_id(row.url)
        if up_id:
            _merge(upload_map, up_id, row)

    return yt_map, upload_map


def _can_access(meta: dict[str, Any] | None, user_id: str, is_admin: bool) -> bool:
    if is_admin or not meta:
        return True
    user_ids = meta.get("user_ids") or set()
    return not user_ids or user_id in user_ids


async def list_cached_videos(
    db: AsyncSession,
    user_id: str,
    is_admin: bool = False,
    temp_dir_override: str | None = None,
) -> dict[str, Any]:
    root = Path(temp_dir_override or get_config().temp_dir)
    yt_root = root / "youtube-cache"
    up_root = root / "uploads"
    yt_map, upload_map = await _load_source_index(db)

    items: list[dict[str, Any]] = []
    youtube_bytes = 0
    upload_bytes = 0
    youtube_count = 0
    upload_count = 0

    if yt_root.is_dir():
        for folder in sorted(yt_root.iterdir()):
            if not folder.is_dir() or not _YT_ID_RE.match(folder.name):
                continue
            vid = folder.name
            meta = yt_map.get(vid)
            if not _can_access(meta, user_id, is_admin):
                continue
            size_bytes, file_count, mtime = _dir_size_and_stats(folder)
            if file_count == 0 and size_bytes == 0:
                continue
            youtube_bytes += size_bytes
            youtube_count += 1
            items.append(
                {
                    "id": f"yt:{vid}",
                    "kind": "youtube",
                    "key": vid,
                    "title": (meta or {}).get("title") or f"YouTube ({vid})",
                    "source_url": (meta or {}).get("url") or f"https://www.youtube.com/watch?v={vid}",
                    "size_bytes": size_bytes,
                    "file_count": file_count,
                    "updated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat() if mtime else None,
                    "in_use": bool((meta or {}).get("in_use", False)),
                    "task_ids": ((meta or {}).get("task_ids") or [])[:5],
                }
            )

    for stem, files in sorted(_group_upload_files(up_root).items()):
        meta = upload_map.get(stem)
        if not _can_access(meta, user_id, is_admin):
            continue
        size_bytes = 0
        latest = 0.0
        primary_name = None
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            size_bytes += st.st_size
            if st.st_mtime > latest:
                latest = st.st_mtime
            if not f.name.startswith(".") and f.suffix.lower() in _VIDEO_EXTS:
                primary_name = f.name
        if not files:
            continue
        upload_bytes += size_bytes
        upload_count += 1
        items.append(
            {
                "id": f"upload:{stem}",
                "kind": "upload",
                "key": stem,
                "title": (meta or {}).get("title") or primary_name or f"Upload ({stem})",
                "source_url": (meta or {}).get("url"),
                "size_bytes": size_bytes,
                "file_count": len(files),
                "updated_at": datetime.fromtimestamp(latest, tz=timezone.utc).isoformat() if latest else None,
                "in_use": bool((meta or {}).get("in_use", False)),
                "task_ids": ((meta or {}).get("task_ids") or [])[:5],
            }
        )

    items.sort(key=lambda x: x["size_bytes"], reverse=True)

    clips_bytes = _dir_size_and_stats(root / "clips")[0] if (root / "clips").is_dir() else 0
    editor_bytes = _dir_size_and_stats(root / "editor")[0] if (root / "editor").is_dir() else 0

    return {
        "total_cache_bytes": youtube_bytes + upload_bytes,
        "youtube_cache_bytes": youtube_bytes,
        "upload_cache_bytes": upload_bytes,
        "clips_storage_bytes": clips_bytes,
        "editor_storage_bytes": editor_bytes,
        "youtube_count": youtube_count,
        "upload_count": upload_count,
        "items": items,
    }


def _parse_cache_id(cache_id: str) -> tuple[str, str]:
    if cache_id.startswith("yt:"):
        key = cache_id[3:]
        if _YT_ID_RE.match(key):
            return "youtube", key
    elif cache_id.startswith("upload:"):
        key = cache_id[7:]
        if _UPLOAD_ID_RE.match(key):
            return "upload", key
    raise ValueError("Invalid cache identifier")


async def delete_cached_video(
    db: AsyncSession,
    cache_id: str,
    user_id: str,
    is_admin: bool = False,
    temp_dir_override: str | None = None,
) -> dict[str, Any]:
    kind, key = _parse_cache_id(cache_id)
    root = Path(temp_dir_override or get_config().temp_dir).resolve()
    yt_map, upload_map = await _load_source_index(db)

    if kind == "youtube":
        meta = yt_map.get(key)
        if not _can_access(meta, user_id, is_admin):
            raise PermissionError("Not allowed to delete this cached video")
        if meta and meta.get("in_use"):
            raise RuntimeError("Video is currently being processed by an active task")
        target = (root / "youtube-cache" / key).resolve()
        yt_base = (root / "youtube-cache").resolve()
        if target.parent != yt_base or not target.is_dir():
            raise FileNotFoundError("Cached video not found")
        freed_bytes, _, _ = _dir_size_and_stats(target)
        shutil.rmtree(target)
        logger.info("Deleted YouTube cached video %s (%d bytes)", key, freed_bytes)
        return {"deleted_id": cache_id, "freed_bytes": freed_bytes}

    meta = upload_map.get(key)
    if not _can_access(meta, user_id, is_admin):
        raise PermissionError("Not allowed to delete this cached upload")
    if meta and meta.get("in_use"):
        raise RuntimeError("Upload is currently being processed by an active task")
    up_base = (root / "uploads").resolve()
    groups = _group_upload_files(up_base)
    files = groups.get(key)
    if not files:
        raise FileNotFoundError("Cached upload not found")
    freed_bytes = 0
    for f in files:
        resolved = f.resolve()
        if resolved.parent != up_base or not resolved.is_file():
            continue
        try:
            freed_bytes += resolved.stat().st_size
            resolved.unlink()
        except OSError as exc:
            logger.warning("Failed removing upload cache file %s: %s", resolved, exc)
    logger.info("Deleted upload cache %s (%d bytes)", key, freed_bytes)
    return {"deleted_id": cache_id, "freed_bytes": freed_bytes}


async def clear_unused_video_cache(
    db: AsyncSession,
    user_id: str,
    is_admin: bool = False,
    temp_dir_override: str | None = None,
) -> dict[str, Any]:
    listing = await list_cached_videos(
        db, user_id=user_id, is_admin=is_admin, temp_dir_override=temp_dir_override
    )
    deleted_count = 0
    freed_bytes = 0
    skipped_in_use = 0
    for item in listing["items"]:
        if item["in_use"]:
            skipped_in_use += 1
            continue
        try:
            res = await delete_cached_video(
                db,
                item["id"],
                user_id=user_id,
                is_admin=is_admin,
                temp_dir_override=temp_dir_override,
            )
            deleted_count += 1
            freed_bytes += int(res["freed_bytes"])
        except (FileNotFoundError, PermissionError, RuntimeError):
            continue
    return {
        "deleted_count": deleted_count,
        "freed_bytes": freed_bytes,
        "skipped_in_use": skipped_in_use,
    }
