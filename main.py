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
import pathlib

# --- Paths ---
BASE_DIR = pathlib.Path(__file__).parent.resolve()
COOKIES_FILE = str(BASE_DIR / "yt.txt")
OUTPUT_DIR = str(BASE_DIR / "output")
SPOTIFY_OUTPUT_DIR = str(BASE_DIR / "spotify_output")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

# --- Spotify credentials ---
SPOTIFY_CLIENT_ID = "spotify_client_id kalian"
SPOTIFY_CLIENT_SECRET = "spotify_client_secret kalian"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

# --- Concurrency control ---
MAX_CONCURRENT_DOWNLOADS = 30
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube dan Spotify Downloader API",
    description="API untuk download i stream video/audio",
    version="2.0.3"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"File {file_path} deleted after {delay}s.")
    except Exception:
        pass

def load_cookies_header() -> str:
    cookies = []
    with open(COOKIES_FILE, 'r') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.strip().split('\t')
            if len(parts) >= 7:
                cookies.append(f"{parts[5]}={parts[6]}")
    return '; '.join(cookies)

@app.get("/stream/", summary="Streamuj video odmah")
async def stream_video(url: str = Query(...), resolution: int = Query(1080)):
    try:
        # 1) Povuci info sa yt-dlp (koristeći cookiefile)
        ydl_opts = {'quiet': True, 'cookiefile': COOKIES_FILE, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 2) Nađi tačno 1080p video-only
        vid_fmt = next(f for f in info['formats']
                       if f.get('vcodec') != 'none'
                       and f.get('height') == resolution
                       and f.get('ext') == 'mp4')

        # 3) Izaberi audio visokog bitrate-a
        aud_fmt = max(
            (f for f in info['formats'] if f.get('vcodec') == 'none' and f.get('acodec') != 'none'),
            key=lambda x: x.get('abr', 0)
        )

        vid_url = vid_fmt['url']
        aud_url = aud_fmt['url']
        cookie_header = load_cookies_header()
        headers_arg = ['-headers', f"Cookie: {cookie_header}\r\n"]

        # 4) FFMPEG: fragmentirani MP4 sa generisanim PTS i resetovanim timestamp-ima
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-fflags', '+genpts',
            '-reset_timestamps', '1',
            *headers_arg, '-i', vid_url,
            *headers_arg, '-i', aud_url,
            '-c:v', 'copy', '-c:a', 'copy',
            '-movflags', 'frag_keyframe+empty_moov+default_base_moof+omit_tfhd_offset',
            '-f', 'mp4', 'pipe:1'
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")

    except StopIteration:
        raise HTTPException(status_code=404, detail=f"Nema {resolution}p toka.")
    except Exception as e:
        logger.error(f"stream_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
