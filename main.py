from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import asyncio
from datetime import datetime
from urllib.parse import quote
import logging
import subprocess
import base64
import requests

SPOTIFY_CLIENT_ID = "spotify_client_id kalian "
SPOTIFY_CLIENT_SECRET = "Spotify_client_secret kalian "

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube dan Spotify Downloader API",
    description="API untuk mengunduh video dan audio dari YouTube i Spotify.",
    version="2.0.1"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = "output"
SPOTIFY_OUTPUT_DIR = "spotify_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

COOKIES_FILE = "yt.txt"

# ---------- ograničenje simultanih download-a ----------
MAX_CONCURRENT_DOWNLOADS = 5
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"File {file_path} je izbrisan nakon {delay}s.")
    except FileNotFoundError:
        logger.warning(f"File {file_path} nije pronađen za brisanje.")
    except Exception as e:
        logger.error(f"Greška prilikom brisanja {file_path}: {e}")

def get_spotify_access_token():
    auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    headers = {"Authorization": f"Basic {b64_auth}"}
    data = {"grant_type": "client_credentials"}
    resp = requests.post(SPOTIFY_TOKEN_URL, headers=headers, data=data)
    if resp.status_code != 200:
        raise Exception(f"Token error: {resp.text}")
    return resp.json()["access_token"]

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.now()
    response = await call_next(request)
    ms = (datetime.now() - start).microseconds / 1000
    logger.info(f"{request.client.host} {request.method} {request.url} -> {response.status_code} [{ms:.2f}ms]")
    return response

@app.get("/", summary="Root")
async def root():
    path = "/app/Apiytdlp/index.html"
    return FileResponse(path) if os.path.exists(path) else JSONResponse(status_code=404, content={"error":"index.html not found"})

# ... SEARCH i INFO endpointi ostaju nepromijenjeni ...

@app.get("/download/", summary="Preuzmi video")
async def download_video(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    resolution: int = Query(720)
):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_%(resolution)sp.%(ext)s'),
            'cookiefile': COOKIES_FILE,
            'merge_output_format': 'mp4'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Nije pronađen: {file_path}")

        background_tasks.add_task(delete_file_after_delay, file_path)
        # FileResponse služi streaming fajla u chunkovima i podržava Range headers
        return FileResponse(file_path, media_type="video/mp4", filename=os.path.basename(file_path))
    except Exception as e:
        logger.error(f"download_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        download_semaphore.release()

@app.get("/download/audio/", summary="Preuzmi audio")
async def download_audio(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_audio.mp3'),
            'format': 'bestaudio/best',
            'cookiefile': COOKIES_FILE,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'prefer_ffmpeg': True,
            'quiet': True,
            'no_warnings': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        filename = f"{info['title']}_audio.mp3"
        file_path = os.path.join(OUTPUT_DIR, filename)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Nije pronađen: {file_path}")

        background_tasks.add_task(delete_file_after_delay, file_path)
        return FileResponse(file_path, media_type="audio/mpeg", filename=filename)
    except Exception as e:
        logger.error(f"download_audio error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        download_semaphore.release()

# ... download_with_subtitle, download_playlist i Spotify endpointi stavite ispod,
# po istom principu: oko svakog dugotrajnog yt_dlp/ffmpeg poziva staviti:
#   await download_semaphore.acquire()
#   try: ... finally: download_semaphore.release()
# i za isporuku lokalnih fajlova koristiti FileResponse umjesto čitanja u memoriju.

@app.get("/download/file/{filename}", summary="Poslužuje fajl")
async def serve_file(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "File nije pronađen"})
    return FileResponse(path, filename=filename)
