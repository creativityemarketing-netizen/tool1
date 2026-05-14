from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path

from api.routes import single, bulk, export, zip as zip_route

app = FastAPI(title="TikTok Downloader")

app.include_router(single.router, prefix="/api")
app.include_router(bulk.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(zip_route.router, prefix="/api")

# Serve frontend
frontend_dir = Path(__file__).parent / "frontend"
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
