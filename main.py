from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
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
from zipfile import ZipFile
from typing import Union, Literal

# --- Konfiguracija i direktoriji ---
SPOTIFY_CLIENT_ID = "spotify_client_id kalian"
SPOTIFY_CLIENT_SECRET = "Spotify_client_secret kalian"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"
OUTPUT_DIR = "output"
SPOTIFY_OUTPUT_DIR = "spotify_output"
COOKIES_FILE = "yt.txt"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Ograničenje simultanih download-a ---
MAX_CONCURRENT_DOWNLOADS = 5
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# --- FastAPI instanca ---
app = FastAPI(
    title="YouTube i Spotify Downloader API",
    description="API za preuzimanje video i audio sa YouTubea i Spotifyja.",
    version="2.0.2"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pomoćne funkcije ---
async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"File {file_path} obrisan nakon {delay}s.")
    except FileNotFoundError:
        logger.warning(f"File {file_path} nije pronađen za brisanje.")
    except Exception as e:
        logger.error(f"Greška pri brisanju {file_path}: {e}")


def get_spotify_access_token():
    auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    b64 = base64.b64encode(auth_str.encode()).decode()
    headers = {"Authorization": f"Basic {b64}"}
    data = {"grant_type": "client_credentials"}
    resp = requests.post(SPOTIFY_TOKEN_URL, headers=headers, data=data)
    if resp.status_code != 200:
        raise Exception(f"Spotify token error: {resp.text}")
    return resp.json()["access_token"]


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.now()
    response = await call_next(request)
    ms = (datetime.now() - start).microseconds / 1000
    logger.info(f"{request.client.host} | {request.method} {request.url} -> {response.status_code} [{ms:.2f}ms]")
    return response

# --- Root, Search i Info endpointi ---
@app.get("/", summary="Root")
async def root():
    path = "/app/Apiytdlp/index.html"
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse(status_code=404, content={"error": "index.html not found"})

@app.get("/search/", summary="Pretraga YouTube video")
async def search_video(query: str = Query(..., description="Ključna riječ")):
    try:
        opts = {'quiet': True, 'cookiefile': COOKIES_FILE}
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(f"ytsearch5:{query}", download=False)
        videos = [{"title": v["title"], "url": v["webpage_url"], "id": v["id"]}
                  for v in res.get('entries', []) if v]
        return {"results": videos}
    except Exception as e:
        logger.error(f"search error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/info/", summary="Detalji video/playlist")
async def get_info(url: str = Query(..., description="YouTube URL")):
    try:
        opts = {'quiet': True, 'cookiefile': COOKIES_FILE}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if 'entries' in info:
            videos = [{
                "index": idx+1,
                "title": v.get("title"),
                "url": v.get("webpage_url"),
                "duration": v.get("duration"),
                "thumbnail": v.get("thumbnail")
            } for idx, v in enumerate(info['entries']) if v]
            return {
                "is_playlist": True,
                "playlist_id": info.get("id"),
                "playlist_title": info.get("title"),
                "uploader": info.get("uploader"),
                "total_videos": len(videos),
                "videos": videos
            }
        # pojedinačni video
        dur = info.get("duration", 0)
        def hms(s): return f"{s//3600:02}:{(s%3600)//60:02}:{s%60:02}"
        size = sum((f.get("filesize") or f.get("filesize_approx") or 0)
                   for f in info.get("formats", [])
                   if f.get("vcodec") != 'none' and f.get("ext") == 'mp4')
        resolutions = sorted({fmt["height"] for fmt in info.get("formats", []) if fmt.get("height")})
        return {
            "is_playlist": False,
            "title": info.get("title"),
            "duration": hms(dur),
            "size_mb": round(size/1024/1024, 2),
            "thumbnail": info.get("thumbnail"),
            "resolutions": resolutions
        }
    except Exception as e:
        logger.error(f"info error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

# --- Download video ---
@app.get("/download/", summary="Preuzmi video")
async def download_video(background_tasks: BackgroundTasks,
                         url: str = Query(...),
                         resolution: int = Query(720)):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_%(resolution)sp.%(ext)s'),
            'cookiefile': COOKIES_FILE,
            'merge_output_format': 'mp4'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} not found")
        background_tasks.add_task(delete_file_after_delay, path)
        return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))
    except Exception as e:
        logger.error(f"download_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        download_semaphore.release()

# --- Download audio ---
@app.get("/download/audio/", summary="Preuzmi audio")
async def download_audio(background_tasks: BackgroundTasks,
                         url: str = Query(...)):
    await download_semaphore.acquire()
    try:
        ydl_opts = {
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_audio.mp3'),
            'format': 'bestaudio/best',
            'cookiefile': COOKIES_FILE,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'prefer_ffmpeg': True,
            'quiet': True,
            'no_warnings': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        filename = f"{info['title']}_audio.mp3"
        path = os.path.join(OUTPUT_DIR, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} not found")
        background_tasks.add_task(delete_file_after_delay, path)
        return FileResponse(path, media_type="audio/mpeg", filename=filename)
    except Exception as e:
        logger.error(f"download_audio error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        download_semaphore.release()

# --- Download sa titlovima ---
@app.get("/download/ytsub", summary="Preuzmi video s titlovima")
async def download_with_subtitle(background_tasks: BackgroundTasks,
                                 url: str = Query(...),
                                 resolution: int = Query(720),
                                 lang: str = Query("en")):
    await download_semaphore.acquire()
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'cookiefile': COOKIES_FILE}) as ydl_info:
            info = ydl_info.extract_info(url, download=False)
        title = info.get("title", "video").replace("/", "_").replace("\\", "_")
        base = f"{title}_{resolution}p_{lang}"
        raw_mp4 = os.path.join(OUTPUT_DIR, base + ".mp4")
        srt = os.path.join(OUTPUT_DIR, base + f".{lang}.srt")

        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [lang],
            'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'merge_output_format': 'mp4',
            'outtmpl': os.path.join(OUTPUT_DIR, base + '.%(ext)s')
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if os.path.exists(srt):
            burned = os.path.join(OUTPUT_DIR, base + "_burned.mp4")
            cmd = [
                "ffmpeg", "-y",
                "-i", raw_mp4,
                "-vf", f"subtitles={srt}:force_style='FontName=Arial,FontSize=24'",
                "-c:v", "libx264", "-preset", "faster", "-crf", "27",
                "-c:a", "aac", "-b:a", "96k",
                burned
            ]
            subprocess.run(cmd, check=True)
            os.replace(burned, raw_mp4)

        background_tasks.add_task(delete_file_after_delay, raw_mp4)
        return FileResponse(raw_mp4, media_type="video/mp4", filename=os.path.basename(raw_mp4))
    except Exception as e:
        logger.error(f"download_with_subtitle error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        download_semaphore.release()

# --- Download playlist ---
@app.get("/download/playlist", summary="Download YouTube playlist")
async def download_playlist(background_tasks
