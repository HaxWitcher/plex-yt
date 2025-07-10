from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import asyncio
from datetime import datetime
import subprocess
import logging

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FastAPI app ---
app = FastAPI(
    title="YouTube Downloader & Streamer",
    description="Download i stream video/audio s YouTube koristeći cookies iz yt.txt",
    version="2.0.2"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

# --- Paths & semafor za konkurentnost ---
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
    return "; ".join(cookies)

@app.middleware("http")
async def log_requests(req: Request, call_next):
    start = datetime.now()
    resp = await call_next(req)
    ms = (datetime.now() - start).microseconds / 1000
    logger.info(f"{req.client.host} {req.method} {req.url} -> {resp.status_code} [{ms:.1f}ms]")
    return resp

@app.get("/", summary="Root")
async def root():
    return JSONResponse({"status": "OK", "version": app.version})

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

@app.get("/stream/", summary="Streamuj video odmah")
async def stream_video(url: str = Query(...), resolution: int = Query(1080)):
    try:
        # 1) Izvuci sve dostupne tokove koristeći yt_dlp Python API
        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio/best'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        formats = info.get('formats', [])

        # 2) Odaberi najbolji video-only format do zadane rezolucije
        vids = [f for f in formats if f.get('vcodec')!='none' and f.get('height',0)<=resolution and f.get('ext')=='mp4']
        if not vids:
            raise HTTPException(status_code=404, detail=f"Nema dostupnog {resolution}p video toka.")
        vid_fmt = max(vids, key=lambda f: f.get('height',0))
        vid_url = vid_fmt['url']

        # 3) Odaberi najbolji audio-only tok
        auds = [f for f in formats if f.get('vcodec')=='none' and f.get('acodec')!='none']
        if not auds:
            raise HTTPException(status_code=404, detail="Nema audio toka.")
        aud_fmt = max(auds, key=lambda f: f.get('abr',0))
        aud_url = aud_fmt['url']

        # 4) S istim cookies za ffmpeg
        cookie_header = load_cookies_header()
        headers_arg = ['-headers', f"Cookie: {cookie_header}\r\n"]

        # 5) Pokreni ffmpeg za fragmentirani MP4 stream
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            *headers_arg, '-i', vid_url,
            *headers_arg, '-i', aud_url,
            '-c:v', 'copy', '-c:a', 'copy',
            '-movflags', 'frag_keyframe+empty_moov',
            '-f', 'mp4', 'pipe:1'
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**6)
        return StreamingResponse(proc.stdout, media_type="video/mp4")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("stream_video error", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/audio/", summary="Preuzmi audio")
async def download_audio(background_tasks: BackgroundTasks, url: str = Query(...)):
    await sem.acquire()
    try:
        ydl_opts = {
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_audio.mp3'),
            'format': 'bestaudio/best',
            'cookiefile': COOKIES_FILE,
            'postprocessors': [{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'128'}],
            'prefer_ffmpeg': True,
            'quiet': True, 'no_warnings': True
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

@app.get("/download/file/{filename}", summary="Serve file")
async def serve_file(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error":"File nije pronađen"})
    return FileResponse(path, filename=filename)
