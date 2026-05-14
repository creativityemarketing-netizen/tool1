import csv
import io
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()

EXPORT_FIELDS = [
    "id", "uploader", "title", "description", "upload_date",
    "duration", "view_count", "like_count", "comment_count",
    "repost_count", "thumbnail", "webpage_url",
]


class ExportRequest(BaseModel):
    videos: list[dict]
    format: str = "csv"  # "csv" or "json"


@router.post("/export")
async def export_metadata(req: ExportRequest):
    if not req.videos:
        raise HTTPException(status_code=400, detail="No videos provided")

    if req.format == "json":
        content = json.dumps(req.videos, ensure_ascii=False, indent=2)
        buf = io.BytesIO(content.encode("utf-8"))
        return StreamingResponse(
            buf,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="tiktok_metadata.json"'},
        )

    # CSV — wrap large numeric IDs in ="..." so Excel keeps full precision as text
    TEXT_FIELDS = {"id", "uploader_id"}
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for v in req.videos:
        row = dict(v)
        for field in TEXT_FIELDS:
            if field in row and row[field]:
                row[field] = f'="{row[field]}"'
        writer.writerow(row)

    buf = io.BytesIO(out.getvalue().encode("utf-8-sig"))  # utf-8-sig for Excel compat
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="tiktok_metadata.csv"'},
    )
