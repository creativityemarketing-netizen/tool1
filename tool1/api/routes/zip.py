from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.services import job_store
from api.services.zip_service import build_zip

router = APIRouter()

DOWNLOADS_DIR = Path(__file__).parent.parent.parent / "downloads"


class ZipRequest(BaseModel):
    job_id: str
    video_ids: list[str]


@router.post("/zip")
async def create_zip(req: ZipRequest, background_tasks: BackgroundTasks):
    job = job_store.get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dir = DOWNLOADS_DIR / req.job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="No downloaded files found for this job. Download videos first.")

    # Collect files that match requested video IDs
    file_paths: list[Path] = []
    for vid_id in req.video_ids:
        matches = list(job_dir.glob(f"{vid_id}.*"))
        file_paths.extend(matches)

    if not file_paths:
        raise HTTPException(status_code=404, detail="No matching downloaded files found")

    buf = build_zip(file_paths)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="tiktok_videos_{req.job_id[:8]}.zip"'},
    )
