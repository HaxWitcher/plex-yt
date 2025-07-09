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

# --- Postavke za yt-dlp cache ---
CACHE_DIR = "/tmp/yt-dlp-cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# --- Spotify kredencijali ---
SPOTIFY_CLIENT_ID = "spotify_client_id kalian"
SPOTIFY_CLIENT_SECRET = "Spotify_client_secret kalian"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FastAPI app ---
app = FastAPI(
    title="YouTube i Spotify Downloader API",
    description="API za preuzimanje ili stream video/audio iz YouTube i Spotify.",
    version="2.0.3"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Mape za izlazne datoteke i kolačiće ---
OUTPUT_DIR = "output"
SPOTIFY_OUTPUT_DIR = "spotify_output"
COOKIES_FILE = "yt.txt"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

# --- Kontrola konkurentnih preuzimanja ---
MAX_CONCURRENT_DOWNLOADS = 5
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# --- Pomoćne funkcije ---
async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"Obrisana datoteka {file_path} nakon {delay}s.")
    except Exception as e:
        logger.warning(f"Ne mogu obrisati {file_path}: {e}")

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
    logger.info(f"{request.client.host} {request.method} {request.url} -> {response.status_code} [{ms:.1f}ms]")
    return response

@app.get("/", summary="Root")
async def root():
    index = "/app/Apiytdlp/index.html"
    return FileResponse(index) if os.path.exists(index) else JSONResponse(status_code=404, content={"error": "index.html not found"})

# --- Pomoćna funkcija za izbor najkvalitetnijeg video/audio toka ---
def select_best_streams(formats, res_limit):
    video_streams = [
        f for f in formats 
        if f.get("vcodec") != "none" 
           and f.get("height", 0) <= res_limit
    ]
    audio_streams = [
        f for f in formats 
        if f.get("acodec") != "none" 
           and f.get("vcodec") == "none"
    ]
    if not video_streams or not audio_streams:
        return None, None
    best_video = max(video_streams, key=lambda f: f.get("height", 0))
    best_audio = max(audio_streams, key=lambda f: f.get("abr", 0))
    return best_video["url"], best_audio["url"]

# --- Endpoint: preuzmi video u datoteku ---
@app.get("/download/", summary="Preuzmi video")
async def download_video(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    resolution: int = Query(720)
):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            "format": f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s_%(resolution)sp.%(ext)s"),
            "cookiefile": COOKIES_FILE,
            "merge_output_format": "mp4",
            "cache_dir": CACHE_DIR,
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

# --- Endpoint: fragmentirani MP4 stream putem ffmpeg-a ---
@app.get("/stream/", summary="Streamaj video odmah bez čekanja cijelog download-a")
async def stream_video(
    url: str = Query(...),
    resolution: int = Query(720)
):
    try:
        ydl_opts = {
            "format": f"bestvideo[height<={resolution}]+bestaudio/best",
            "quiet": True,
            "cookiefile": COOKIES_FILE,
            "cache_dir": CACHE_DIR,
            "noplaylist": True,
            "socket_timeout": 10,
            "hls_prefer_native": True,
            "hls_use_mpegts": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        video_url, audio_url = select_best_streams(info["formats"], resolution)
        if not video_url or not audio_url:
            raise HTTPException(status_code=500, detail="Nema odgovarajućih formata za streaming.")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", video_url, "-i", audio_url,
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "frag_keyframe+empty_moov",
            "-f", "mp4", "pipe:1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"stream_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# --- Novi endpoint: vraća direktne URL-ove (video + audio) ---
@app.get("/stream/url", summary="Dohvati direktne URL-ove za video i audio tokove")
async def get_stream_urls(
    url: str = Query(...),
    resolution: int = Query(720)
):
    try:
        ydl_opts = {
            "format": f"bestvideo[height<={resolution}]+bestaudio/best",
            "quiet": True,
            "cookiefile": COOKIES_FILE,
            "cache_dir": CACHE_DIR,
            "noplaylist": True,
            "socket_timeout": 10,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        video_url, audio_url = select_best_streams(info["formats"], resolution)
        if not video_url or not audio_url:
            raise HTTPException(status_code=500, detail="Nema odgovarajućih formata.")
        return {"video_url": video_url, "audio_url": audio_url}
    except Exception as e:
        logger.error(f"get_stream_urls error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# --- Endpoint: preuzmi samo audio ---
@app.get("/download/audio/", summary="Preuzmi audio")
async def download_audio(
    background_tasks: BackgroundTasks,
    url: str = Query(...)
):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s_audio.mp3"),
            "format": "bestaudio/best",
            "cookiefile": COOKIES_FILE,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
            "prefer_ffmpeg": True,
            "quiet": True,
            "no_warnings": True,
            "cache_dir": CACHE_DIR,
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

# --- Endpoint: poslužuje preuzete datoteke ---
@app.get("/download/file/{filename}", summary="Poslužuje preuzetu datoteku")
async def serve_file(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "Datoteka nije pronađena"})
    return FileResponse(path, filename=filename)
