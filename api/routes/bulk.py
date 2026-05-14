import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from api.services import job_store, ytdlp_service, metadata_service

router = APIRouter()

DOWNLOADS_DIR = Path(__file__).parent.parent.parent / "downloads"


class BulkStartRequest(BaseModel):
    username: str
    max_videos: int = 0  # 0 = fetch all
    date_from: str | None = None
    date_to: str | None = None
    min_duration: int | None = None
    max_duration: int | None = None
    keywords: list[str] | None = None
    cookiefile: str | None = None


class DownloadSelectedRequest(BaseModel):
    job_id: str
    video_ids: list[str]
    cookiefile: str | None = None


def _run_bulk(job_id: str, req: BulkStartRequest):
    def on_progress(fetched, total, title):
        job_store.update_progress(job_id, fetched, total, title)

    try:
        videos = ytdlp_service.fetch_user_videos(
            req.username,
            max_videos=req.max_videos,
            on_progress=on_progress,
            cookiefile=req.cookiefile,
        )
        filtered = metadata_service.apply_filters(
            videos,
            date_from=req.date_from,
            date_to=req.date_to,
            min_duration=req.min_duration,
            max_duration=req.max_duration,
            keywords=req.keywords,
        )
        job_store.complete_job(job_id, filtered)
    except Exception as e:
        job_store.fail_job(job_id, str(e))


@router.post("/bulk/start")
async def bulk_start(req: BulkStartRequest, background_tasks: BackgroundTasks):
    job_id = job_store.create_job()
    background_tasks.add_task(_run_bulk, job_id, req)
    return {"job_id": job_id}


@router.get("/bulk/{job_id}/status")
async def bulk_status(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/bulk/{job_id}/progress")
async def bulk_progress(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        while True:
            j = job_store.get_job(job_id)
            if not j:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Job not found'})}\n\n"
                break

            status = j["status"]
            if status == "running" or status == "pending":
                p = j["progress"]
                yield f"data: {json.dumps({'type': 'progress', 'fetched': p['fetched'], 'total': p['total'], 'title': p['title']})}\n\n"
                await asyncio.sleep(0.5)
            elif status == "done":
                yield f"data: {json.dumps({'type': 'progress', 'fetched': len(j['videos']), 'total': len(j['videos']), 'title': ''})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'videos': j['videos'], 'count': len(j['videos'])})}\n\n"
                break
            elif status == "error":
                yield f"data: {json.dumps({'type': 'error', 'message': j.get('error', 'Unknown error')})}\n\n"
                break
            else:
                await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/bulk/download-selected")
async def download_selected(req: DownloadSelectedRequest, background_tasks: BackgroundTasks):
    """Download multiple selected videos. Returns a ZIP file."""
    job = job_store.get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    video_map: dict[str, dict] = {v["id"]: v for v in job.get("videos", [])}
    selected = [video_map[vid] for vid in req.video_ids if vid in video_map]

    if not selected:
        raise HTTPException(status_code=400, detail="No valid video IDs provided")

    job_dir = DOWNLOADS_DIR / req.job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Download each video sequentially (avoids hammering TikTok)
    downloaded: list[Path] = []
    for v in selected:
        path = await ytdlp_service.download_video_async(
            v["webpage_url"], v["id"], job_dir, req.cookiefile
        )
        if path:
            downloaded.append(path)

    if not downloaded:
        raise HTTPException(status_code=500, detail="No videos could be downloaded")

    from api.services.zip_service import build_zip
    buf = build_zip(downloaded)

    def cleanup():
        for p in downloaded:
            p.unlink(missing_ok=True)

    background_tasks.add_task(cleanup)

    username = selected[0].get("uploader", "tiktok") if selected else "tiktok"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{username}_videos.zip"'},
    )


@router.delete("/cleanup/{job_id}")
async def cleanup(job_id: str):
    job_dir = DOWNLOADS_DIR / job_id
    if job_dir.exists():
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)
    job_store.delete_job(job_id)
    return {"deleted": job_id}
