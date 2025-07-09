import os
import asyncio
import logging
import subprocess
from datetime import datetime
from urllib.parse import quote

import yt_dlp
import requests
from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

# --- Spotify (ako treba) ---
SPOTIFY_CLIENT_ID     = "spotify_client_id_kalian"
SPOTIFY_CLIENT_SECRET = "spotify_client_secret_kalian"
SPOTIFY_TOKEN_URL     = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL       = "https://api.spotify.com/v1"

# --- Logging ---
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- FastAPI ---
app = FastAPI(
    title="YouTube i Spotify Downloader",
    version="2.0.3",
    description="Download ili stream video/audio s YouTube i Spotify"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Direktori i kolačići ---
BASE_DIR        = os.path.dirname(__file__)
COOKIES_FILE    = os.path.join(BASE_DIR, "yt.txt")
OUTPUT_DIR      = os.path.join(BASE_DIR, "output")
SPOTIFY_OUT_DIR = os.path.join(BASE_DIR, "spotify_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUT_DIR, exist_ok=True)

# --- Ograničenje paralelnih downloadova ---
MAX_CONCURRENT = 30
sem_download   = asyncio.Semaphore(MAX_CONCURRENT)

# --- Pomoćna funkcija za brisanje nakon kašnjenja ---
async def delete_later(path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(path)
        logger.info(f"Obrisan {path} nakon {delay}s")
    except:
        pass

# --- Middleware za log svakog requesta ---
@app.middleware("http")
async def log_middleware(req: Request, call_next):
    start = datetime.now()
    resp  = await call_next(req)
    ms    = (datetime.now() - start).microseconds / 1000
    logger.info(f"{req.client.host} {req.method} {req.url} -> {resp.status_code} [{ms:.1f}ms]")
    return resp

# --- Root endpoint ---
@app.get("/", summary="Status")
async def root():
    return {"status": "OK", "version": app.version}

# --- Download video u MP4 fajl ---
@app.get("/download/", summary="Preuzmi video")
async def download_video(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="YouTube URL"),
    resolution: int = Query(720, description="Maks. visina (npr. 720, 1080)")
):
    await sem_download.acquire()
    try:
        ydl_opts = {
            "format": f"bestvideo[height<={resolution}][ext=mp4]+bestaudio/best",
            "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s_%(resolution)sp.%(ext)s"),
            "cookiefile": COOKIES_FILE,
            "merge_output_format": "mp4",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)

        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        background_tasks.add_task(delete_later, path)
        return FileResponse(path, media_type="video/mp4",
                            filename=os.path.basename(path))

    except Exception as e:
        logger.error(f"download error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sem_download.release()

# --- Stream fragmentirani MP4 u traženoj visini ---
@app.get("/stream/", summary="Stream video")
async def stream_video(
    url: str = Query(..., description="YouTube URL"),
    resolution: int = Query(1080, description="Točno height==resolution")
):
    try:
        # 1) Izvuci sve formate bez filtera
        ydl_opts = {
            "quiet": True,
            "cookiefile": COOKIES_FILE,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 2) Nađi tocno video-only tok s height==resolution
        video_fmt = next(
            f for f in info["formats"]
            if f.get("vcodec") != "none"
               and f.get("height") == resolution
               and f.get("ext") == "mp4"
        )
        # 3) Nađi najbolji audio-only tok
        audio_fmt = next(
            f for f in info["formats"]
            if f.get("vcodec") == "none"
               and f.get("acodec") != "none"
        )

        vid_url = video_fmt["url"]
        aud_url = audio_fmt["url"]

        # 4) Pokreni ffmpeg za fragmentirani MP4
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", vid_url,
            "-i", aud_url,
            "-c:v", "copy",
            "-c:a", "copy",
            "-movflags", "frag_keyframe+empty_moov",
            "-f", "mp4", "pipe:1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")

    except StopIteration:
        raise HTTPException(
            status_code=404,
            detail=f"Nije pronađen {resolution}p video tok."
        )
    except Exception as e:
        logger.error(f"stream error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# --- Download samo audio u MP3 ---
@app.get("/download/audio/", summary="Preuzmi audio")
async def download_audio(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="YouTube URL")
):
    await sem_download.acquire()
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
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        fname = f"{info['title']}_audio.mp3"
        path  = os.path.join(OUTPUT_DIR, fname)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        background_tasks.add_task(delete_later, path)
        return FileResponse(path, media_type="audio/mpeg", filename=fname)

    except Exception as e:
        logger.error(f"audio download error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sem_download.release()

# --- Posluži već preuzeti fajl ---
@app.get("/download/file/{filename}", summary="Poslužuje fajl")
async def serve_file(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(path):
        return JSONResponse(status_code=404, content={"error": "File nije pronađen"})
    return FileResponse(path, filename=filename)
