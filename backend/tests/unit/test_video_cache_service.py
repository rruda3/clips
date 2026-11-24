from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.video_cache_service import (
    clear_unused_video_cache,
    delete_cached_video,
    list_cached_videos,
)


def _mock_db(rows):
    res = MagicMock()
    res.fetchall.return_value = rows
    db = AsyncMock()
    db.execute.return_value = res
    return db


@pytest.mark.asyncio
async def test_list_and_delete_cached_videos(tmp_path):
    yt_dir = tmp_path / "youtube-cache" / "abc123XYZ"
    yt_dir.mkdir(parents=True)
    (yt_dir / "full-1080.mp4").write_bytes(b"a" * 4096)
    (yt_dir / "manifest.json").write_text("{}")

    yt_busy = tmp_path / "youtube-cache" / "busyVID99"
    yt_busy.mkdir(parents=True)
    (yt_busy / "full-1080.mp4").write_bytes(b"b" * 2048)

    up_dir = tmp_path / "uploads"
    up_dir.mkdir(parents=True)
    (up_dir / "11111111-2222-3333-4444-555555555555.mp4").write_bytes(b"c" * 3072)
    (up_dir / "11111111-2222-3333-4444-555555555555.transcription.wav").write_bytes(b"d" * 512)

    rows = [
        SimpleNamespace(
            id="t1",
            user_id="u1",
            status="completed",
            created_at=None,
            type="youtube",
            title="Gameplay Teste",
            url="https://www.youtube.com/watch?v=abc123XYZ",
        ),
        SimpleNamespace(
            id="t2",
            user_id="u1",
            status="processing",
            created_at=None,
            type="youtube",
            title="Video Processando",
            url="https://www.youtube.com/watch?v=busyVID99",
        ),
        SimpleNamespace(
            id="t3",
            user_id="u1",
            status="completed",
            created_at=None,
            type="video_url",
            title="meu-arquivo.mp4",
            url="upload://11111111-2222-3333-4444-555555555555.mp4",
        ),
    ]
    db = _mock_db(rows)

    listing = await list_cached_videos(db, user_id="u1", is_admin=False, temp_dir_override=str(tmp_path))
    assert listing["youtube_count"] == 2
    assert listing["upload_count"] == 1
    assert listing["total_cache_bytes"] == 4096 + 2 + 2048 + 3072 + 512

    by_id = {item["id"]: item for item in listing["items"]}
    assert by_id["yt:abc123XYZ"]["title"] == "Gameplay Teste"
    assert by_id["yt:abc123XYZ"]["in_use"] is False
    assert by_id["yt:busyVID99"]["in_use"] is True
    assert by_id["upload:11111111-2222-3333-4444-555555555555"]["title"] == "meu-arquivo.mp4"

    # Active processing video cannot be deleted
    with pytest.raises(RuntimeError):
        await delete_cached_video(db, "yt:busyVID99", user_id="u1", temp_dir_override=str(tmp_path))

    # Path traversal / invalid id rejected
    with pytest.raises(ValueError):
        await delete_cached_video(db, "yt:../secret", user_id="u1", temp_dir_override=str(tmp_path))

    # Delete single YouTube cache
    deleted = await delete_cached_video(db, "yt:abc123XYZ", user_id="u1", temp_dir_override=str(tmp_path))
    assert deleted["freed_bytes"] == 4098
    assert not yt_dir.exists()

    # Clear remaining unused (deletes upload, skips busyVID99)
    cleared = await clear_unused_video_cache(db, user_id="u1", temp_dir_override=str(tmp_path))
    assert cleared["deleted_count"] == 1
    assert cleared["skipped_in_use"] == 1
    assert cleared["freed_bytes"] == 3584
    assert yt_busy.exists()
