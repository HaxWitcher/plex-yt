from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import asyncio
from datetime import datetime
import logging
import subprocess
import pathlib

# --- Paths ---
BASE_DIR = pathlib.Path(__file__).parent.resolve()
COOKIES_FILE = str(BASE_DIR / "yt.txt")
OUTPUT_DIR = str(BASE_DIR / "output")
SPOTIFY_OUTPUT_DIR = str(BASE_DIR / "spotify_output")

# --- Ensure dirs exist ---
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

# --- Concurrency control ---
MAX_CONCURRENT_DOWNLOADS = 30
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube i Spotify Downloader API",
    description="API za download i streaming video/audio sa YouTube i Spotify",
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
        logger.info(f"File {file_path} obrisan nakon {delay}s.")
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

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.now()
    response = await call_next(request)
    ms = (datetime.now() - start).microseconds / 1000
    logger.info(f"{request.client.host} {request.method} {request.url} -> {response.status_code} [{ms:.1f}ms]")
    return response

@app.get("/stream/", summary="Streamuj video odmah bez čekanja download-a")
async def stream_video(url: str = Query(...), resolution: int = Query(1080)):
    try:
        # 1) Izvuci sve tokove koristeći yt-dlp i cookies
        ydl_opts = {'quiet': True, 'cookiefile': COOKIES_FILE, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 2) Pronađi tačno 1080p mp4 video-only tok
        vid_fmt = next(
            f for f in info['formats']
            if f.get('vcodec') != 'none'
               and f.get('height') == resolution
               and f.get('ext') == 'mp4'
        )

        # 3) Pronađi audio-only tok najveće brzine
        aud_fmt = max(
            (f for f in info['formats'] if f.get('vcodec') == 'none' and f.get('acodec') != 'none'),
            key=lambda x: x.get('abr', 0)
        )

        vid_url = vid_fmt['url']
        aud_url = aud_fmt['url']

        # 4) Sastavi Cookie header
        cookie_header = load_cookies_header()
        headers_arg = ['-headers', f"Cookie: {cookie_header}\r\n"]

        # 5) Pokreni ffmpeg za fragmentirani MP4 sa dodatnim flagovima
        cmd = [
            'ffmpeg',
            '-hide_banner', '-loglevel', 'error',
            '-fflags', '+genpts',                 # generiši PTS
            '-avoid_negative_ts', 'make_zero',    # poravnaj timestamp-e
            *headers_arg, '-i', vid_url,
            *headers_arg, '-i', aud_url,
            '-c:v', 'copy', '-c:a', 'copy',
            '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
            '-f', 'mp4', 'pipe:1'
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")

    except StopIteration:
        raise HTTPException(status_code=404, detail=f"Nema dostupan {resolution}p video tok.")
    except Exception as e:
        logger.error(f"stream_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
