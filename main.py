import os
import re
import time
import asyncio
import yt_dlp
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

app = FastAPI()

# 📁 Putanje za Hugging Face Persistent Storage
PERSISTENT_DIR = "/data"
OUTPUT_DIR = os.path.join(PERSISTENT_DIR, "output")
SPOTIFY_OUTPUT_DIR = os.path.join(PERSISTENT_DIR, "spotify")
CACHE_DIR = os.path.join(PERSISTENT_DIR, ".cache")
COOKIES_FILE = "cookies.json"

# ✅ Kreiraj direktorijume ako ne postoje
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ✅ Postavi cache path za yt-dlp
os.environ["XDG_CACHE_HOME"] = CACHE_DIR

# ✅ ThreadPoolExecutor za paralelne yt-dlp zadatke
executor = ThreadPoolExecutor(max_workers=10)

async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))

def clean_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def clean_old_files(directory, max_age_minutes=120):
    now = time.time()
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if os.path.isfile(file_path):
            age_minutes = (now - os.path.getmtime(file_path)) / 60
            if age_minutes > max_age_minutes:
                try:
                    os.remove(file_path)
                except Exception:
                    pass

@app.get("/")
def read_root():
    return {"message": "YouTube & Spotify Downloader API with persistent storage and auto-cleaning"}

@app.get("/search")
async def search_video(q: str = Query(..., description="Search term for YouTube")):
    try:
        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'cachedir': CACHE_DIR
        }
        def _search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch10:{q}", download=False)['entries']
        search_result = await run_blocking(_search)

        videos = [{
            "title": video["title"],
            "url": video["webpage_url"],
            "duration": video["duration"],
            "thumbnail": video["thumbnail"]
        } for video in search_result]
        return {"results": videos}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/download")
async def download_video(request: Request, url: str, resolution: int = 720, lang: str = "en"):
    try:
        clean_old_files(OUTPUT_DIR)

        output_template = os.path.join(OUTPUT_DIR, '%(title)s.%(ext)s')
        ydl_opts = {
            'format': f"bestvideo[height<={resolution}]+bestaudio/best",
            'outtmpl': output_template,
            'cookiefile': COOKIES_FILE,
            'writesubtitles': True,
            'subtitleslangs': [lang],
            'cachedir': CACHE_DIR,
            'noplaylist': True,
            'nocheckcertificate': True,
            'retries': 3,
            'concurrent_fragment_downloads': 5,
        }

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        info = await run_blocking(_download)
        video_title = clean_filename(info.get('title', 'video'))
        ext = info.get('ext', 'mp4')
        filename = f"{video_title}.{ext}"
        output_path = os.path.join(OUTPUT_DIR, filename)

        if not os.path.exists(output_path):
            return JSONResponse(status_code=404, content={"error": "File not found"})

        download_url = str(request.base_url) + f"download/file/{quote(filename)}"
        return {"download_url": download_url}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/download/file/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(file_path):
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return StreamingResponse(
        open(file_path, "rb"),
        media_type="video/mp4",
        headers={"Content-Disposition": f"inline; filename={filename}"}
    )

@app.get("/download/spotify")
async def download_spotify_track(request: Request, url: str):
    try:
        clean_old_files(SPOTIFY_OUTPUT_DIR)

        output_template = os.path.join(SPOTIFY_OUTPUT_DIR, '%(title)s.%(ext)s')
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'cachedir': CACHE_DIR
        }

        def _download_spotify():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        info = await run_blocking(_download_spotify)
        audio_title = clean_filename(info.get('title', 'audio'))
        ext = info.get('ext', 'mp3')
        filename = f"{audio_title}.{ext}"
        output_path = os.path.join(SPOTIFY_OUTPUT_DIR, filename)

        if not os.path.exists(output_path):
            return JSONResponse(status_code=404, content={"error": "File not found"})

        download_url = str(request.base_url) + f"download/spotify/file/{quote(filename)}"
        return {"download_url": download_url}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/download/spotify/file/{filename}")
async def download_spotify_file(filename: str):
    file_path = os.path.join(SPOTIFY_OUTPUT_DIR, filename)
    if not os.path.exists(file_path):
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return StreamingResponse(
        open(file_path, "rb"),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f"inline; filename={filename}"}
    )

@app.get("/info/")
async def get_info(url: str):
    try:
        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'cachedir': CACHE_DIR
        }

        def _info():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await run_blocking(_info)
        return {
            "title": info.get("title"),
            "duration": info.get("duration"),
            "webpage_url": info.get("webpage_url"),
            "thumbnail": info.get("thumbnail"),
            "description": info.get("description")
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
