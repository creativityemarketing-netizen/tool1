import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

_IMPERSONATE = ImpersonateTarget("chrome")

DOWNLOADS_DIR = Path(__file__).parent.parent.parent / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
_FORMAT_SORT = ["quality", "codec:h264", "size"]


def _normalize(info: dict) -> dict:
    """Extract a consistent metadata dict from a yt-dlp info dict."""
    timestamp = info.get("timestamp")
    upload_date = info.get("upload_date", "")

    # Derive upload_date from timestamp when extract_flat omits it
    if not upload_date and timestamp:
        try:
            dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            upload_date = dt.strftime("%Y%m%d")
        except Exception:
            pass

    # Pick best thumbnail: prefer 'cover' (preference -1) over 'dynamicCover'
    thumbnail = info.get("thumbnail") or ""
    if not thumbnail:
        thumbs = info.get("thumbnails") or []
        # Sort by preference descending (less negative = better), pick first with a url
        thumbs_sorted = sorted(thumbs, key=lambda t: t.get("preference", -99), reverse=True)
        for t in thumbs_sorted:
            if t.get("url"):
                thumbnail = t["url"]
                break

    video_id = info.get("id", "")
    return {
        "id": video_id,
        "uploader": info.get("uploader") or info.get("creator") or info.get("channel") or "",
        "title": info.get("title") or info.get("description", "")[:80],
        "description": info.get("description", ""),
        "upload_date": upload_date,
        "timestamp": timestamp,
        "duration": info.get("duration"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "repost_count": info.get("repost_count"),
        "thumbnail": thumbnail,
        "webpage_url": info.get("webpage_url") or info.get("url", ""),
        "availability": info.get("availability", "public"),
    }


def _base_opts(cookiefile: str | None = None) -> dict:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "impersonate": _IMPERSONATE,  # bypass TikTok bot detection
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    return opts


# ── Single video ─────────────────────────────────────────────────────────────

def get_info(url: str, cookiefile: str | None = None) -> dict:
    opts = {**_base_opts(cookiefile), "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return _normalize(info)


def download_single(url: str, cookiefile: str | None = None) -> Path:
    """Download one video and return the local file path."""
    opts = {
        **_base_opts(cookiefile),
        "format": _FORMAT,
        "format_sort": _FORMAT_SORT,
        "outtmpl": str(DOWNLOADS_DIR / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info.get("id", "unknown")

    # Find the file yt-dlp wrote
    matches = list(DOWNLOADS_DIR.glob(f"{video_id}.*"))
    if not matches:
        raise FileNotFoundError(f"Downloaded file for {video_id} not found")
    return matches[0]


# ── Bulk user scrape ──────────────────────────────────────────────────────────

def fetch_user_videos(
    username: str,
    max_videos: int = 0,
    on_progress: Callable[[int, int, str], None] | None = None,
    cookiefile: str | None = None,
) -> list[dict]:
    """Fetch metadata for all (or up to max_videos) videos from a TikTok profile.
    max_videos=0 means fetch everything."""
    url = f"https://www.tiktok.com/@{username.lstrip('@')}"

    class _Logger:
        def debug(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass

    opts: dict[str, Any] = {
        **_base_opts(cookiefile),
        "extract_flat": True,
        "ignoreerrors": True,
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "logger": _Logger(),
    }
    if max_videos and max_videos > 0:
        opts["playlistend"] = max_videos

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        return []

    entries = info.get("entries", []) or []
    if max_videos and max_videos > 0:
        entries = entries[:max_videos]

    collected: list[dict] = []
    total = len(entries)

    for i, entry in enumerate(entries):
        if entry is None:
            continue
        meta = _normalize(entry)
        collected.append(meta)
        if on_progress:
            on_progress(i + 1, total, meta.get("title", ""))

    return collected


# ── Bulk download selected videos ─────────────────────────────────────────────

def download_video_by_id(webpage_url: str, video_id: str, job_dir: Path, cookiefile: str | None = None) -> Path | None:
    """Download a single video into job_dir. Returns path or None on failure."""
    job_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        **_base_opts(cookiefile),
        "ignoreerrors": True,
        "format": _FORMAT,
        "format_sort": _FORMAT_SORT,
        "outtmpl": str(job_dir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(webpage_url, download=True)
        matches = list(job_dir.glob(f"{video_id}.*"))
        return matches[0] if matches else None
    except Exception:
        return None


async def download_video_async(webpage_url: str, video_id: str, job_dir: Path, cookiefile: str | None = None) -> Path | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, download_video_by_id, webpage_url, video_id, job_dir, cookiefile
    )
