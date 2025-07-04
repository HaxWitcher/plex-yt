import os
import io
import math
import base64
import logging
import yt_dlp
import requests
import subprocess
import asyncio
from datetime import datetime
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="YouTube dan Spotify Downloader API",
    description="API untuk mengunduh video dan audio dari YouTube dan Spotify.",
    version="2.1.0"
)

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories
OUTPUT_DIR = "output"
SPOTIFY_OUTPUT_DIR = "spotify_output"
for d in (OUTPUT_DIR, SPOTIFY_OUTPUT_DIR):
    os.makedirs(d, exist_ok=True)

COOKIES_FILE = "yt.txt"

# ThreadPool for blocking operations
executor = ThreadPoolExecutor(max_workers=8)

# Spotify credentials
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "your_spotify_client_id")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "your_spotify_client_secret")
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

# Utility: Delete file after delay
async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"Deleted {file_path} after {delay}s")
    except Exception as e:
        logger.warning(f"Failed deleting {file_path}: {e}")

# Utility: Spotify token
def get_spotify_access_token():
    auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        headers={"Authorization": f"Basic {b64_auth}"},
        data={"grant_type": "client_credentials"}
    )
    if resp.status_code != 200:
        raise Exception(f"Spotify token error: {resp.text}")
    return resp.json()["access_token"]

# Log all requests
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.utcnow()
    response = await call_next(request)
    latency = (datetime.utcnow() - start).total_seconds() * 1000
    logger.info(f"{request.client.host} {request.method} {request.url} -> {response.status_code} ({latency:.1f}ms)")
    return response

@app.get("/", summary="Root Endpoint")
async def root():
    return {"message": "API is running"}

# ------ YouTube endpoints ------

@app.get("/download/", summary="Unduhan Video YouTube")
async def download_video(
    url: str = Query(...),
    resolution: int = Query(720),
    background_tasks: BackgroundTasks = None
):
    """
    Download video and stream from local file with efficient handling.
    """
    # Blocking download in thread pool
    def _dl():
        opts = {
            'format': f'bestvideo[height<={resolution}]+bestaudio',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_%(resolution)sp.%(ext)s'),
            'cookiefile': COOKIES_FILE,
            'merge_output_format': 'mp4'
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    file_path = await asyncio.get_event_loop().run_in_executor(executor, _dl)

    if not os.path.exists(file_path):
        return JSONResponse(status_code=500, content={"error": "Download failed"})

    # Schedule deletion
    background_tasks.add_task(delete_file_after_delay, file_path)

    # Stream file efficiently
    return FileResponse(
        path=file_path,
        media_type='video/mp4',
        filename=os.path.basename(file_path)
    )

@app.get("/download/audio/", summary="Unduhan Audio YouTube")
async def download_audio(
    url: str = Query(...),
    background_tasks: BackgroundTasks = None
):
    def _dl_audio():
        opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s.%(ext)s'),
            'cookiefile': COOKIES_FILE,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }]
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return f"{info['title']}.mp3"

    filename = await asyncio.get_event_loop().run_in_executor(executor, _dl_audio)
    file_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(file_path):
        return JSONResponse(status_code=500, content={"error": "Audio download failed"})
    background_tasks.add_task(delete_file_after_delay, file_path)
    return FileResponse(path=file_path, media_type='audio/mpeg', filename=filename)

# ------ Spotify endpoints (unchanged) ------
@app.get("/spotify/search", summary="Cari lagu di Spotify")
async def spotify_search(query: str = Query(...)):
    try:
        token = get_spotify_access_token()
        resp = requests.get(
            f"{SPOTIFY_API_URL}/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "type": "track", "limit": 5}
        )
        data = resp.json()
        results = []
        for track in data.get('tracks', {}).get('items', []):
            results.append({
                'title': track['name'],
                'artist': ', '.join(a['name'] for a in track['artists']),
                'spotify_url': track['external_urls']['spotify']
            })
        return {"results": results}
    except Exception as e:
        logger.error(f"Spotify search error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ... other Spotify endpoints remain the same ...
