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
SPOTIFY_CLIENT_ID = "spotify_client_id kalian "
SPOTIFY_CLIENT_SECRET = "Spotify_client_secret kalian "
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FastAPI app ---
app = FastAPI(
    title="YouTube dan Spotify Downloader API",
    description="API untuk mengunduh atau stream video/audio dari YouTube dan Spotify.",
    version="2.0.2"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Directories and files ---
OUTPUT_DIR = "output"
SPOTIFY_OUTPUT_DIR = "spotify_output"
COOKIES_FILE = "yt.txt"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

# --- Concurrency control ---
MAX_CONCURRENT_DOWNLOADS = 5
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# --- Helpers ---
async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"File {file_path} deleted after {delay}s.")
    except Exception as e:
        logger.warning(f"Could not delete {file_path}: {e}")

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

# --- Download video to file (with aria2c) ---
@app.get("/download/", summary="Preuzmi video")
async def download_video(background_tasks: BackgroundTasks, url: str = Query(...), resolution: int = Query(720)):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_%(resolution)sp.%(ext)s'),
            'cookiefile': COOKIES_FILE,
            'merge_output_format': 'mp4',
            # подключаем aria2c
            'external_downloader': 'aria2c',
            'external_downloader_args': [
                '-x', '16',    # 16 параллельных подключений
                '-s', '16',    # 16 сегментов на файл
                '-k', '1M',    # размер сегмента 1M
                '--file-allocation=none'
            ]
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

# --- Direct ffmpeg-based streaming endpoint ---
@app.get("/stream/", summary="Streamuj video odmah tanpa tunggu full download")
async def stream_video(url: str = Query(...), resolution: int = Query(720)):
    try:
        ydl_opts = {
            'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best',
            'quiet': True,
            'cookiefile': COOKIES_FILE
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        video_url = next((f['url'] for f in info['formats'] if f.get('vcodec')!='none' and f.get('height')<=resolution), None)
        audio_url = next((f['url'] for f in info['formats'] if f.get('acodec')!='none' and f.get('vcodec')=='none'), None)
        if not video_url or not audio_url:
            raise HTTPException(status_code=500, detail="Unknown stream URL.")
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-i', video_url, '-i', audio_url,
            '-c:v', 'copy', '-c:a', 'copy',
            '-movflags', 'frag_keyframe+empty_moov',
            '-f', 'mp4', 'pipe:1'
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"stream_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# --- Download audio (with aria2c) ---
@app.get("/download/audio/", summary="Preuzmi audio")
async def download_audio(background_tasks: BackgroundTasks, url: str = Query(...)):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_audio.mp3'),
            'format': 'bestaudio/best',
            'cookiefile': COOKIES_FILE,
            'postprocessors': [{ 'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128' }],
            'prefer_ffmpeg': True,
            'quiet': True,
            'no_warnings': True,
            # подключаем aria2c
            'external_downloader': 'aria2c',
            'external_downloader_args': [
                '-x', '16',
                '-s', '16',
                '-k', '1M',
                '--file-allocation=none'
            ]
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

# --- Serve downloaded file ---
@app.get("/download/file/{filename}", summary="Poslužuje fajl")
async def serve_file(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "File nije pronađen"})
    return FileResponse(path, filename=filename)
