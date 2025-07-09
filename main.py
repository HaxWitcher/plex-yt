from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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

# --- Spotify credentials ---
SPOTIFY_CLIENT_ID = "tvoj_spotify_client_id"
SPOTIFY_CLIENT_SECRET = "tvoj_spotify_client_secret"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FastAPI app ---
app = FastAPI(
    title="YouTube i Spotify Downloader API",
    description="API za download ili stream video/audio s YouTube i Spotify.",
    version="2.0.3"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Direktne putanje ---
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
COOKIES_FILE = os.path.join(BASE_DIR, "yt.txt")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Provjera kolačića ---
if not os.path.isfile(COOKIES_FILE):
    logger.error(f"Kolačići nisu pronađeni na {COOKIES_FILE}")
    raise RuntimeError("Cookie file yt.txt nije pronađen u glavnom direktoriju aplikacije.")

# --- Semaphore za ograničenje paralelnih download-a ---
MAX_CONCURRENT_DOWNLOADS = 10
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# --- Pomocne funkcije ---
async def delete_file_after_delay(path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(path)
        logger.info(f"Obrisan privremeni fajl {path}")
    except Exception as e:
        logger.warning(f"Ne mogu obrisati {path}: {e}")

def get_spotify_access_token():
    auth = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    b64  = base64.b64encode(auth.encode()).decode()
    headers = {"Authorization": f"Basic {b64}"}
    data    = {"grant_type": "client_credentials"}
    r = requests.post(SPOTIFY_TOKEN_URL, headers=headers, data=data)
    if r.status_code != 200:
        raise RuntimeError(f"Spotify token error: {r.text}")
    return r.json()["access_token"]

@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = datetime.now()
    resp = await call_next(request)
    ms = (datetime.now()-t0).microseconds/1000
    logger.info(f"{request.client.host} {request.method} {request.url.path} -> {resp.status_code} [{ms:.1f}ms]")
    return resp

@app.get("/", summary="Root endpoint")
async def root():
    return JSONResponse({"status":"ok"}, status_code=200)

# --- Šaljemo direktne URL-ove za streamanje ---
@app.get("/stream/url", summary="Dohvati video/audio URL-ove")
async def stream_urls(url: str = Query(...), resolution: int = Query(1080)):
    try:
        ydl_opts = {
            "cookiefile": COOKIES_FILE,
            "cookies_from_browser": ("chrome","firefox"),
            "format": f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best"
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        video_url = next(f["url"] for f in info["formats"] if f.get("vcodec")!="none" and f.get("height")<=resolution)
        audio_url = next(f["url"] for f in info["formats"] if f.get("acodec")!="none" and f.get("vcodec")=="none")
        return {"video_url": video_url, "audio_url": audio_url}
    except Exception as e:
        logger.error(f"/stream/url error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

# --- Fragmentirani mp4 stream za klijente ---
@app.get("/stream/", summary="Fragmentirani MP4 stream")
async def stream_video(url: str = Query(...), resolution: int = Query(1080)):
    try:
        # prvo dohvatimo direktne tokove
        ydl_opts = {
            "cookiefile": COOKIES_FILE,
            "cookies_from_browser": ("chrome","firefox"),
            "format": f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best"
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        vid = next(f["url"] for f in info["formats"] if f.get("vcodec")!="none" and f.get("height")<=resolution)
        aud = next(f["url"] for f in info["formats"] if f.get("acodec")!="none" and f.get("vcodec")=="none")

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", vid, "-i", aud,
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "frag_keyframe+empty_moov",
            "-f", "mp4", "pipe:1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")
    except Exception as e:
        logger.error(f"/stream error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

# --- Download cijelog videa ---
@app.get("/download/", summary="Preuzmi MP4 u fajl")
async def download_video(background_tasks: BackgroundTasks, url: str = Query(...), resolution: int = Query(1080)):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            "cookiefile": COOKIES_FILE,
            "cookies_from_browser": ("chrome","firefox"),
            "format": f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best",
            "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s_%(resolution)sp.%(ext)s"),
            "merge_output_format": "mp4"
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
        background_tasks.add_task(delete_file_after_delay, path)
        return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))
    except Exception as e:
        logger.error(f"/download error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))
    finally:
        download_semaphore.release()

# --- Preuzmi samo audio ---
@app.get("/download/audio", summary="Preuzmi MP3 audio")
async def download_audio(background_tasks: BackgroundTasks, url: str = Query(...)):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            "cookiefile": COOKIES_FILE,
            "cookies_from_browser": ("chrome","firefox"),
            "format": "bestaudio/best",
            "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s_audio.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128"
            }]
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = os.path.join(OUTPUT_DIR, f"{info['title']}_audio.mp3")
        background_tasks.add_task(delete_file_after_delay, path)
        return FileResponse(path, media_type="audio/mpeg", filename=os.path.basename(path))
    except Exception as e:
        logger.error(f"/download/audio error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))
    finally:
        download_semaphore.release()
