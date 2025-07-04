import os
import re
import time
import asyncio
import yt_dlp
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from concurrent.futures import ThreadPoolExecutor

app = FastAPI()

# 📁 Hugging Face Persistent Storage
PERSISTENT_DIR = "/data"
CACHE_DIR = os.path.join(PERSISTENT_DIR, ".cache")
COOKIES_FILE = "cookies.json"

# 🔧 Kreiraj potrebne direktorijume
os.makedirs(CACHE_DIR, exist_ok=True)
os.environ["XDG_CACHE_HOME"] = CACHE_DIR

# 🔁 ThreadPoolExecutor za paralelne zadatke
executor = ThreadPoolExecutor(max_workers=10)

async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))

def clean_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

@app.get("/")
def root():
    return {"message": "YouTube stream API radi 🚀"}

# 🔍 Pretraga YouTube videa
@app.get("/search")
async def search_video(q: str = Query(..., description="YouTube pretraga")):
    try:
        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'cachedir': CACHE_DIR
        }

        def _search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch10:{q}", download=False)['entries']

        results = await run_blocking(_search)

        return [{
            "title": video["title"],
            "url": video["webpage_url"],
            "duration": video["duration"],
            "thumbnail": video["thumbnail"]
        } for video in results]

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ▶️ Generiši direktan stream URL (bez preuzimanja)
@app.get("/stream")
async def stream_url_only(url: str):
    try:
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'cookiefile': COOKIES_FILE,
            'format': 'best[ext=mp4]/best',
            'cachedir': CACHE_DIR
        }

        def _extract_url():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    "title": info.get("title"),
                    "stream_url": info.get("url"),
                    "duration": info.get("duration"),
                    "ext": info.get("ext"),
                    "thumbnail": info.get("thumbnail")
                }

        result = await run_blocking(_extract_url)
        return result

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ℹ️ Info o videu
@app.get("/info/")
async def get_info(url: str):
    try:
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
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
