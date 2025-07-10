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
    description="API untuk mengunduh atau stream video/audio dari YouTube i Spotify.",
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
MAX_CONCURRENT_DOWNLOADS = 30
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# --- Helpers ---
async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"File {file_path} deleted after {delay}s.")
    except Exception as e:
        logger.warning(f"Could not delete {file_path}: {e}")

def load_cookies_header() -> str:
    """
    Parsira Netscape cookies iz yt.txt i vraća ih kao 'name1=val1; name2=val2; ...'
    """
    cookies = []
    with open(COOKIES_FILE, 'r') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.strip().split('\t')
            if len(parts) >= 7:
                name, value = parts[5], parts[6]
                cookies.append(f"{name}={value}")
    return '; '.join(cookies)

# ... ovdje su download_video i download_audio točno onakvi kakvi su bili ...

# --- Streaming endpoint (fragmentirani MP4 u 1080p) ---
@app.get("/stream/", summary="Streamuj video odmah bez čekanja čitavog download-a")
async def stream_video(url: str = Query(...), resolution: int = Query(1080)):
    try:
        # 1) Dohvati sve tokove s yt-dlp koristeći samo cookiefile
        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 2) Nađi EXPLICITNO video-only tok 1080p mp4
        vid_fmt = next(
            f for f in info['formats']
            if f.get('vcodec') != 'none'
               and f.get('height') == resolution
               and f.get('ext') == 'mp4'
        )

        # 3) Nađi najbolji audio-only tok
        aud_fmt = max(
            (f for f in info['formats'] if f.get('vcodec') == 'none' and f.get('acodec') != 'none'),
            key=lambda x: x.get('abr', 0)
        )

        vid_url = vid_fmt['url']
        aud_url = aud_fmt['url']

        # 4) Pripremi cookies za FFmpeg
        cookie_header = load_cookies_header()
        headers_arg = ['-headers', f"Cookie: {cookie_header}\r\n"]

        # 5) Pokreni FFmpeg za fragmentirani MP4
        proc = subprocess.Popen(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error', 
             *headers_arg, '-i', vid_url, 
             *headers_arg, '-i', aud_url,
             '-c:v', 'copy', '-c:a', 'copy',
             '-movflags', 'frag_keyframe+empty_moov',
             '-f', 'mp4', 'pipe:1'],
            stdout=subprocess.PIPE,
            bufsize=10**6
        )

        # Generiraj streaming odgovor: flushaj odmah praznim chunkom
        async def streamer():
            yield b""  # odmah pošalji header
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk

        return StreamingResponse(streamer(), media_type="video/mp4")

    except StopIteration:
        raise HTTPException(status_code=404, detail=f"Nema dostupnog {resolution}p video toka.")
    except Exception as e:
        logger.error(f"stream_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
