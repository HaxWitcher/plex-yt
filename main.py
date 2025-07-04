from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import yt_dlp
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

app = FastAPI()

executor = ThreadPoolExecutor(max_workers=10)
CACHE_DIR = "/data/.cache"
os.makedirs(CACHE_DIR, exist_ok=True)
os.environ["XDG_CACHE_HOME"] = CACHE_DIR

@app.get("/")
def index():
    return {"message": "API je aktivan ✅"}

@app.get("/stream")
async def stream_url_only(url: str = Query(...)):
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'cachedir': CACHE_DIR,
        'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'
    }

    def extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title"),
                "stream_url": info.get("url"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
            }

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, extract)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
