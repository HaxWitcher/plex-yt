import os
import re
import yt_dlp
import asyncio
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from concurrent.futures import ThreadPoolExecutor

app = FastAPI()

# 📁 Putanja ka cache folderu (Hugging Face storage)
PERSISTENT_DIR = "/data"
CACHE_DIR = os.path.join(PERSISTENT_DIR, ".cache")
COOKIES_FILE = "cookies.json"

# Kreiranje cache foldera i podešavanje environment varijable
os.makedirs(CACHE_DIR, exist_ok=True)
os.environ["XDG_CACHE_HOME"] = CACHE_DIR

# 🔁 ThreadPool za async rad
executor = ThreadPoolExecutor(max_workers=10)

async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))

@app.get("/")
def index():
    return {"message": "YouTube Stream API je aktivan 🚀"}

# ▶️ Direktan stream URL sa YouTube-a (max 1080p)
@app.get("/stream")
async def stream_url_only(url: str = Query(..., description="YouTube video URL")):
    try:
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'cookiefile': COOKIES_FILE,
            'cachedir': CACHE_DIR,
            'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'
        }

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    "title": info.get("title"),
                    "stream_url": info.get("url"),
                    "duration": info.get("duration"),
                    "ext": info.get("ext"),
                    "thumbnail": info.get("thumbnail")
                }

        result = await run_blocking(_extract)
        return result

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
