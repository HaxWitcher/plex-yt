import os
import re
import io
import time
import shutil
import uvicorn
import yt_dlp
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from urllib.parse import quote

app = FastAPI()

# ✅ Postavi cache direktorijum za yt-dlp koji je dozvoljen na Hugging Face
os.environ["XDG_CACHE_HOME"] = "/data/.cache"
os.makedirs("/data/.cache", exist_ok=True)

OUTPUT_DIR = "./output"
SPOTIFY_OUTPUT_DIR = "./spotify"
COOKIES_FILE = "cookies.json"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

def clean_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def clean_old_files(directory, max_age_minutes=10):
    now = time.time()
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if os.path.isfile(file_path):
            if now - os.path.getmtime(file_path) > max_age_minutes * 60:
                os.remove(file_path)

@app.get("/")
def read_root():
    return {"message": "YouTube & Spotify Downloader API"}

@app.get("/search")
async def search_video(q: str = Query(..., description="Search term for YouTube")):
    try:
        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'cachedir': '/data/.cache'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(f"ytsearch10:{q}", download=False)['entries']
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
        ydl_opts = {
            'format': f"bestvideo[height<={resolution}]+bestaudio/best",
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s.%(ext)s'),
            'cookiefile': COOKIES_FILE,
            'writesubtitles': True,
            'subtitleslangs': [lang],
            'cachedir': '/data/.cache'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
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
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(SPOTIFY_OUTPUT_DIR, '%(title)s.%(ext)s'),
            'cachedir': '/data/.cache'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
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
            'cachedir': '/data/.cache'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "title": info.get("title"),
            "duration": info.get("duration"),
            "webpage_url": info.get("webpage_url"),
            "thumbnail": info.get("thumbnail"),
            "description": info.get("description")
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
