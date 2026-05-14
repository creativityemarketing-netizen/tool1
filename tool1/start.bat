@echo off
echo Starting TikTok Downloader...
echo.
echo Tip: Keep yt-dlp up to date if downloads stop working:
echo   pip install -U yt-dlp
echo.
echo Opening http://localhost:8000 ...
start "" "http://localhost:8000"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
