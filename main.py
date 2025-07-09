import os
import asyncio
import logging
import subprocess
import base64
from datetime import datetime
from urllib.parse import quote

import requests
import yt_dlp
from fastapi import (
    FastAPI, Request, Query, BackgroundTasks,
    HTTPException
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

# --- Spotify credentials (ako koristiš Spotify API) ---
SPOTIFY_CLIENT_ID = "spotify_client_id_kalian"
SPOTIFY_CLIENT_SECRET = "spotify_client_secret_kalian"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- FastAPI app ---
app = FastAPI(
    title="YouTube i Spotify Downloader API",
    description="API za download i streaming video/audio s YouTube-a i Spotify-a",
    version="2.0.3"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Paths i direktoriji ---
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE     = os.path.join(BASE_DIR, "yt.txt")
OUTPUT_DIR       = os.path.join(BASE_DIR, "output")
SPOTIFY_OUTPUT_DIR = os.path.join(BASE_DIR, "spotify_output")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

# --- Ograničenje paralelnih download-a ---
MAX_CONCURRENT_DOWNLOADS = 30
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# --- Pomoćne funkcije ---
async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"File {file_path} izbrisan nakon {delay}s.")
    except Exception as e:
        logger.warning(f"Ne mogu izbrisati {file_path}: {e}")

def get_spotify_access_token():
    auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    headers = {"Authorization": f"Basic {b64_auth}"}
    data = {"grant_type": "client_credentials"}
    resp = requests.post(SPOTIFY_TOKEN_URL, headers=headers, data=data)
    if resp.status_code != 200:
        raise Exception(f"Greška pri dobivanju tokena: {resp.text}")
    return resp.json()["access_token"]

# --- Middleware za logiranje svakog requesta ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.now()
    response = await call_next(request)
    ms = (datetime.now() - start).microseconds / 1000
    logger.info(f"{request.client.host} {request.method} {request.url} -> {response.status_code} [{ms:.1f}ms]")
    return response

# --- Root endpoint ---
@app.get("/", summary="Root", description="Provjera servisa")
async def root():
    return JSONResponse({"status": "OK", "version": app.version})

# --- Download video u file ---
@app.get("/download/", summary="Preuzmi video", description="Preuzmi cijeli video i vrati ga kao MP4")
async def download_video(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="YouTube URL"),
    resolution: int = Query(720, description="Rezolucija (npr. 720, 1080)")
):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            "format": f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
            "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s_%(resolution)sp.%(ext)s"),
            "cookiefile": COOKIES_FILE,
            "merge_output_format": "mp4"
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)

        if not os.path.exists(path):
            raise FileNotFoundError(path)

        background_tasks.add_task(delete_file_after_delay, path)
        return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))

    except Exception as e:
        logger.error(f"download_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        download_semaphore.release()

# --- Streaming endpoint (fragmentirani MP4 u odabranoj rezoluciji) ---
@app.get("/stream/", summary="Streamuj video", description="Fragmentirani MP4 stream u točno odabranoj rezoluciji")
async def stream_video(
    url: str = Query(..., description="YouTube URL"),
    resolution: int = Query(1080, description="Točno height==resolution")
):
    try:
        # 1) Izvuci DASH/HLS tokove
        ydl_opts = {
            "quiet": True,
            "cookiefile": COOKIES_FILE,
            "format": f"bestvideo[height=={resolution}][ext=mp4]+bestaudio/best",
            "hls_prefer_native": True,
            "hls_use_mpegts": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 2) Nađi video-only tok točno u resolution
        vid_fmt = next(
            f for f in info["formats"]
            if f.get("vcodec") != "none"
            and f.get("height") == resolution
            and f.get("ext") == "mp4"
        )
        # 3) Nađi najbolju audio-only traku
        aud_fmt = next(
            f for f in info["formats"]
            if f.get("vcodec") == "none"
            and f.get("acodec") != "none"
        )

        vid_url = vid_fmt["url"]
        aud_url = aud_fmt["url"]

        # 4) Pokreni ffmpeg za fragmentirani mp4
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", vid_url, "-i", aud_url,
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "frag_keyframe+empty_moov",
            "-f", "mp4", "pipe:1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")

    except StopIteration:
        raise HTTPException(
            status_code=404,
            detail=f"Nema dostupnog {resolution}p video tok-a."
        )
    except Exception as e:
        logger.error(f"stream_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# --- Download audio u mp3 ---
@app.get("/download/audio/", summary="Preuzmi audio", description="Preuzmi samo audio i vrati MP3")
async def download_audio(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="YouTube URL")
):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s_audio.mp3"),
            "format": "bestaudio/best",
            "cookiefile": COOKIES_FILE,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "128"
                }
            ],
            "prefer_ffmpeg": True,
            "quiet": True,
            "no_warnings": True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        fname = f"{info['title']}_audio.mp3"
        path = os.path.join(OUTPUT_DIR, fname)
        if not os.path.exists(path):
            raise FileNotFoundError(path)

        background_tasks.add_task(delete_file_after_delay, path)
        return FileResponse(path, media_type="audio/mpeg", filename=fname)

    except Exception as e:
        logger.error(f"download_audio error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        download_semaphore.release()

# --- Serve lokalni fajl ---
@app.get("/download/file/{filename}", summary="Poslužuje fajl", description="Vraća već preuzeti fajl")
async def serve_file(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "File nije pronađen"})
    return FileResponse(path, filename=filename)
