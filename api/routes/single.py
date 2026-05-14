import asyncio
from pathlib import Path
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from api.services import ytdlp_service

router = APIRouter()


class InfoRequest(BaseModel):
    url: str
    cookiefile: str | None = None


class DownloadRequest(BaseModel):
    url: str
    cookiefile: str | None = None


@router.post("/info")
async def get_info(req: InfoRequest):
    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, ytdlp_service.get_info, req.url, req.cookiefile)
        return info
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


_pending_files: dict[str, Path] = {}

_THUMB_HEADERS = {
    "Referer": "https://www.tiktok.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
}


@router.get("/thumbnail")
async def proxy_thumbnail(url: str = Query(...)):
    """Proxy TikTok thumbnail images to bypass hotlink protection."""
    decoded = unquote(url)
    if not decoded.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            r = await client.get(decoded, headers=_THUMB_HEADERS)
        content_type = r.headers.get("content-type", "image/jpeg")
        return Response(content=r.content, media_type=content_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/download/single")
async def download_single(req: DownloadRequest, background_tasks: BackgroundTasks):
    try:
        loop = asyncio.get_event_loop()
        file_path = await loop.run_in_executor(
            None, ytdlp_service.download_single, req.url, req.cookiefile
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = file_path.name

    def cleanup():
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass

    background_tasks.add_task(cleanup)

    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=None,
    )
