from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import asyncio
from datetime import datetime
from subprocess import Popen, PIPE, run, CalledProcessError
import logging
import yt_dlp  # za download endpoint

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FastAPI app ---
app = FastAPI(
    title="YouTube dan Spotify Downloader API",
    description="API za download i stream video/audio s YouTube.",
    version="2.0.2"
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

# --- Paths & globals ---
OUTPUT_DIR = "output"
COOKIES_FILE = "yt.txt"
os.makedirs(OUTPUT_DIR, exist_ok=True)
MAX_CONCURRENT = 30
sem = asyncio.Semaphore(MAX_CONCURRENT)

async def delete_later(path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(path)
        logger.info(f"Deleted {path}")
    except:
        pass

def load_cookies_header() -> str:
    cookies = []
    with open(COOKIES_FILE) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies.append(f"{parts[5]}={parts[6]}")
    return "; ".join(cookies)

def yt_dlp_get_urls(url: str, resolution: int):
    """
    CLI yt-dlp -g s ispravnom sintaksom height={resolution} za video i audio.
    """
    try:
        # video-only
        vid_out = run(
            ["yt-dlp", "-g", "-f", f"bestvideo[height={resolution}][ext=mp4]", "--cookies", COOKIES_FILE, url],
            check=True, capture_output=True, text=True
        ).stdout.strip().splitlines()
        # audio-only
        aud_out = run(
            ["yt-dlp", "-g", "-f", "bestaudio[ext=m4a]", "--cookies", COOKIES_FILE, url],
            check=True, capture_output=True, text=True
        ).stdout.strip().splitlines()
        if not vid_out or not aud_out:
            raise CalledProcessError(1, "yt-dlp -g")
        return vid_out[0], aud_out[0]
    except CalledProcessError as e:
        logger.error("yt-dlp CLI extraction failed", exc_info=True)
        raise RuntimeError("yt-dlp CLI nije uspio izvući URL") from e

@app.middleware("http")
async def log_middleware(req: Request, call_next):
    start = datetime.now()
    resp = await call_next(req)
    ms = (datetime.now() - start).microseconds / 1000
    logger.info(f"{req.client.host} {req.method} {req.url} -> {resp.status_code} [{ms:.1f}ms]")
    return resp

@app.get("/", summary="Root")
async def root():
    return JSONResponse({"status": "OK", "version": app.version})

# --- Download video to file ---
@app.get("/download/", summary="Preuzmi video")
async def download_video(background_tasks: BackgroundTasks, url: str = Query(...), resolution: int = Query(720)):
    await sem.acquire()
    try:
        ydl_opts = {
            'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_%(resolution)sp.%(ext)s'),
            'cookiefile': COOKIES_FILE,
            'merge_output_format': 'mp4'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        background_tasks.add_task(delete_later, path)
        return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))
    except Exception as e:
        logger.error("download_video error", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sem.release()

# --- Streaming endpoint (fragmentirani MP4 u 1080p) ---
@app.get("/stream/", summary="Streamuj video odmah")
async def stream_video(url: str = Query(...), resolution: int = Query(1080)):
    try:
        vid_url, aud_url = yt_dlp_get_urls(url, resolution)
        ck = load_cookies_header()
        headers_arg = ['-headers', f"Cookie: {ck}\r\n"]
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            *headers_arg, '-i', vid_url,
            *headers_arg, '-i', aud_url,
            '-c:v', 'copy', '-c:a', 'copy',
            '-movflags', 'frag_keyframe+empty_moov',
            '-f', 'mp4', 'pipe:1'
        ]
        proc = Popen(cmd, stdout=PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("stream_video error", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# --- Download audio ---
@app.get("/download/audio/", summary="Preuzmi audio")
async def download_audio(background_tasks: BackgroundTasks, url: str = Query(...)):
    await sem.acquire()
    try:
        ydl_opts = {
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_audio.mp3'),
            'format': 'bestaudio/best',
            'cookiefile': COOKIES_FILE,
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '128'}],
            'prefer_ffmpeg': True,
            'quiet': True,
            'no_warnings': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        fname = f"{info['title']}_audio.mp3"
        path = os.path.join(OUTPUT_DIR, fname)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        background_tasks.add_task(delete_later, path)
        return FileResponse(path, media_type="audio/mpeg", filename=fname)
    except Exception as e:
        logger.error("download_audio error", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sem.release()

# --- Serve downloaded file ---
@app.get("/download/file/{filename}", summary="Serve file")
async def serve_file(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "File nije pronađen"})
    return FileResponse(path, filename=filename)
