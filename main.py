from fastapi import FastAPI, Request, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import asyncio
from datetime import datetime
from urllib.parse import quote
import io
import logging
import subprocess
import math
import base64
import requests  

SPOTIFY_CLIENT_ID = "spotify_client_id kalian "
SPOTIFY_CLIENT_SECRET = "Spotify_client_secret kalian "

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube dan Spotify Downloader API",
    description="API untuk mengunduh video dan audio dari YouTube dan Spotify.",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = "output"
SPOTIFY_OUTPUT_DIR = "spotify_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SPOTIFY_OUTPUT_DIR, exist_ok=True)

COOKIES_FILE = "yt.txt"

async def delete_file_after_delay(file_path: str, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logger.info(f"File {file_path} telah dihapus setelah {delay} detik.")
    except FileNotFoundError:
        logger.warning(f"File {file_path} tidak ditemukan, tidak dapat dihapus.")
    except Exception as e:
        logger.error(f"Gagal menghapus file {file_path}: {e}")

def get_spotify_access_token():
    auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()

    headers = {
        "Authorization": f"Basic {b64_auth}"
    }
    data = {
        "grant_type": "client_credentials"
    }

    response = requests.post(SPOTIFY_TOKEN_URL, headers=headers, data=data)
    if response.status_code != 200:
        raise Exception(f"Gagal mendapatkan token: {response.text}")

    return response.json()["access_token"]

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.now()
    response = await call_next(request)
    process_time = (datetime.now() - start_time).microseconds / 1000
    logger.info(
        f"IP: {request.client.host}, Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, "
        f"Method: {request.method}, URL: {request.url}, "
        f"Status: {response.status_code}, Process Time: {process_time:.3f} ms"
    )
    return response

@app.get("/", summary="Root Endpoint", description="Menampilkan halaman index.html.")
async def root():
    html_path = "/app/Apiytdlp/index.html"
    if os.path.exists(html_path):
        return FileResponse(html_path)
    return JSONResponse(status_code=404, content={"error": "index.html file not found"})

@app.get("/search/", summary="Pencarian Video YouTube")
async def search_video(query: str = Query(..., description="Kata kunci pencarian untuk video YouTube")):
    try:
        ydl_opts = {'quiet': True, 'cookiefile': COOKIES_FILE}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(f"ytsearch5:{query}", download=False)
            videos = [
                {"title": v["title"], "url": v["webpage_url"], "id": v["id"]}
                for v in search_result.get('entries', [])
                if 'title' in v and 'webpage_url' in v and 'id' in v
            ]
        logger.info(f"search | Query: {query} | Results: {len(videos)}")
        return {"results": videos}
    except Exception as e:
        logger.error(f"search | Query: {query} | Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/info/", summary="Informasi Lengkap Video/Playlist YouTube")
async def get_info(url: str = Query(..., description="URL video atau playlist YouTube")):
    try:
        ydl_opts = {'quiet': True, 'cookiefile': COOKIES_FILE}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        is_playlist = 'entries' in info

        if is_playlist:
            video_entries = info.get('entries', [])
            videos = []
            for idx, v in enumerate(video_entries, 1):
                if v:
                    videos.append({
                        "index": idx,
                        "title": v.get("title", "Unknown"),
                        "url": v.get("webpage_url", "Unknown"),
                        "duration": v.get("duration", 0),
                        "thumbnail": v.get("thumbnail"),
                    })

            return {
                "is_playlist": True,
                "playlist_id": info.get("id"),
                "playlist_title": info.get("title", "Unknown Playlist"),
                "uploader": info.get("uploader"),
                "uploader_url": info.get("uploader_url"),
                "webpage_url": info.get("webpage_url"),
                "total_videos": len(videos),
                "videos": videos
            }

        # Jika bukan playlist, tampilkan info video tunggal
        total_duration = info.get("duration", 0)
        size_bytes = sum(
            (f.get("filesize") or f.get("filesize_approx") or 0)
            for f in info.get("formats", [])
            if f.get("vcodec") != "none" and f.get("ext") == "mp4"
        )
        size_mb = round(size_bytes / 1024 / 1024, 2) if size_bytes else "Unknown"

        def seconds_to_hms(seconds):
            h = seconds // 3600
            m = (seconds % 3600) // 60
            s = seconds % 60
            return f"{int(h):02}:{int(m):02}:{int(s):02}"

        resolutions = []
        for fmt in info.get("formats", []):
            if fmt.get("vcodec") != 'none' and fmt.get("height"):
                size = fmt.get('filesize') or fmt.get('filesize_approx')
                resolutions.append({
                    "resolution": f"{fmt['height']}p",
                    "ext": fmt.get('ext', 'Unknown'),
                    "size": round(size / 1024 / 1024, 2) if size else "Unknown"
                })
        unique_resolutions = list({v['resolution']: v for v in resolutions}.values())

        subtitles = info.get("subtitles", {})
        auto_captions = info.get("automatic_captions", {})
        subtitle_languages = list(set(subtitles.keys()) | set(auto_captions.keys()))

        return {
            "is_playlist": False,
            "title": info.get("title"),
            "channel": info.get("channel"),
            "channel_url": info.get("channel_url"),
            "duration": seconds_to_hms(total_duration),
            "size_mb": size_mb,
            "has_subtitle": bool(subtitle_languages),
            "subtitle_languages": subtitle_languages,
            "resolutions": unique_resolutions,
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url"),
        }

    except Exception as e:
        logger.error(f"info | URL: {url} | Error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/download/", summary="Unduhan Video YouTube")
async def download_video(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    resolution: int = Query(720),
    mode: str = Query("url")
):
    try:
        ydl_opts = {
            'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_%(resolution)sp.%(ext)s'),
            'cookiefile': COOKIES_FILE,
            'merge_output_format': 'mp4'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File tidak ditemukan setelah unduhan: {file_path}")

        background_tasks.add_task(delete_file_after_delay, file_path)

        with open(file_path, "rb") as f:
            video_buffer = io.BytesIO(f.read())

        return StreamingResponse(
            video_buffer,
            media_type="video/mp4",
            headers={"Content-Disposition": f"attachment; filename={os.path.basename(file_path)}"}
            )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"download | URL: {url} | yt_dlp Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    except Exception as e:
        logger.error(f"download | URL: {url} | General Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
        
@app.get("/download/ytsub", summary="Unduh video dengan subtitle digabung")
async def download_with_subtitle(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    resolution: int = Query(720),
    lang: str = Query("id", description="Bahasa subtitle, contoh: en, id, fr"),
    mode: str = Query("url")
):
    if mode != "url":
        return JSONResponse(status_code=400, content={"error": "Hanya mode 'url' yang didukung untuk endpoint ini."})

    try:
        loop = asyncio.get_running_loop()
        result = {}

        def download():
            with yt_dlp.YoutubeDL({'quiet': True, 'cookiefile': COOKIES_FILE}) as ydl_info:
                info = ydl_info.extract_info(url, download=False)
                title = info.get("title", "video").replace("/", "_").replace("\\", "_")
                filename_base = f"ytsubbynvl-{title}-{resolution}p-{lang}"
                final_filename = f"{filename_base}.mp4"
                final_filepath = os.path.join(OUTPUT_DIR, final_filename)

                if os.path.exists(final_filepath):
                    file_size_mb = round(os.path.getsize(final_filepath) / (1024 * 1024), 2)
                    result.update({
                        "title": title,
                        "thumbnail": info.get("thumbnail"),
                        "size_mb": file_size_mb,
                        "download_url": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(final_filename)}"
                    })
                    background_tasks.add_task(delete_file_after_delay, final_filepath)
                    return

                ydl_opts = {
                    'quiet': True,
                    'cookiefile': COOKIES_FILE,
                    'writesubtitles': True,
                    'writeautomaticsub': True,
                    'subtitleslangs': [lang],
                    'skip_download': False,
                    'outtmpl': os.path.join(OUTPUT_DIR, filename_base + '.%(ext)s'),
                    'format': f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
                    'merge_output_format': 'mp4',
                    'postprocessors': [
                        {'key': 'FFmpegSubtitlesConvertor', 'format': 'srt'},
                        {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}
                    ]
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                raw_filepath = os.path.join(OUTPUT_DIR, f"{filename_base}.mp4")
                subtitle_path = os.path.join(OUTPUT_DIR, f"{filename_base}.{lang}.srt")
                burned_filepath = os.path.join(OUTPUT_DIR, f"{filename_base}.burned.mp4")

                # Burn subtitle jika ada
                if os.path.exists(subtitle_path):
                    ffmpeg_cmd = [
                        "ffmpeg", "-y",
                        "-i", raw_filepath,
                        "-vf", f"subtitles={subtitle_path}:force_style='FontName=Arial,FontSize=24,OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0'",
                        "-c:v", "libx264",
                        "-preset", "faster",    
                        "-crf", "27",           
                        "-c:a", "aac",
                        "-b:a", "96k",
                        burned_filepath
                    ]
                    subprocess.run(ffmpeg_cmd, check=True)
                    os.remove(raw_filepath)
                    os.rename(burned_filepath, final_filepath)
                else:
                    os.rename(raw_filepath, final_filepath)

                if os.path.exists(final_filepath):
                    file_size_mb = round(os.path.getsize(final_filepath) / (1024 * 1024), 2)
                    result.update({
                        "title": title,
                        "thumbnail": info.get("thumbnail"),
                        "size_mb": file_size_mb,
                        "download_url": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(final_filename)}"
                    })
                    background_tasks.add_task(delete_file_after_delay, final_filepath)

        await loop.run_in_executor(None, download)

        if not result:
            raise FileNotFoundError("Gagal mengunduh dan menggabungkan subtitle.")

        return result

    except Exception as e:
        logger.error(f"download/with-sub | URL: {url} | Error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/download/audio/", summary="Unduhan Audio YouTube")
async def download_audio(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    mode: str = Query("url")
):
    if mode not in ["url", "buffer"]:
        return JSONResponse(status_code=400, content={"error": "Mode unduhan tidak valid. Gunakan 'url' atau 'buffer'."})

    try:
        ydl_opts = {
    'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_audio_downloadbynauval.%(ext)s'),
    'format': 'bestaudio/best',
    'cookiefile': COOKIES_FILE,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '128',
    }],
    'postprocessor_args': [
        '-vn',
        '-preset', 'ultrafast',
        '-threads', '4' 
    ],
    'prefer_ffmpeg': True,
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True
}


        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        output_filename = f"{info['title']}_audio_downloadbynauval.mp3"
        file_path = os.path.join(OUTPUT_DIR, output_filename)

        if not os.path.exists(file_path):
            raise FileNotFoundError("File hasil konversi tidak ditemukan.")

        background_tasks.add_task(delete_file_after_delay, file_path)

        if mode == "url":
            return {
                "title": info['title'],
                "thumbnail": info.get('thumbnail'),
                "filesize": os.path.getsize(file_path),
                "author": "nauval",
                "download_url": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(os.path.basename(output_filename))}"
            }

        with open(file_path, "rb") as f:
            audio_buffer = io.BytesIO(f.read())

        return StreamingResponse(
            audio_buffer,
            media_type="audio/mp3",
            headers={"Content-Disposition": f"attachment; filename={os.path.basename(output_filename)}"}
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"menjadi/download/audio | URL: {url} | yt_dlp Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    except Exception as e:
        logger.error(f"menjadi/download/audio | URL: {url} | General Error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

from typing import Union, Literal

@app.get("/download/playlist", summary="Unduhan Playlist YouTube")
async def download_playlist(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    limit: int = Query(5, ge=1),
    resolution: Union[Literal["audio"], int] = Query(
        "720",
        description="Resolusi video maksimum (misalnya 720), atau 'audio' untuk hanya unduhan audio terbaik"
    ),
    mode: str = Query("url", description="Mode unduhan, saat ini hanya mendukung 'url'")
):
    if mode != "url":
        return JSONResponse(status_code=400, content={"error": "Mode tidak didukung. Gunakan mode 'url'."})

    try:
        is_audio_only = resolution == "audio"

        if is_audio_only:
            ydl_format = "bestaudio"
            merge_output = None
        else:
            video_resolution = int(resolution)
            ydl_format = f'bestvideo[height<={video_resolution}][ext=mp4]+bestaudio/best[ext=mp4]/best'
            merge_output = 'mp4'

        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'extract_flat': False,
            'playlistend': limit,
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s.%(ext)s'),
            'format': ydl_format,
        }

        if merge_output:
            ydl_opts['merge_output_format'] = merge_output

        loop = asyncio.get_running_loop()
        downloaded_files = []

        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                entries = info.get('entries', [])
                for idx, entry in enumerate(entries, start=1):
                    filepath = ydl.prepare_filename(entry)
                    if os.path.exists(filepath):
                        downloaded_files.append({
                            "index": idx,
                            "title": entry.get("title"),
                            "download_url": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(os.path.basename(filepath))}"
                        })
                        background_tasks.add_task(delete_file_after_delay, filepath)

        await loop.run_in_executor(None, download)

        return {
            "playlist_title": f"Download hasil playlist dari: {url}",
            "total_videos": len(downloaded_files),
            "videos": downloaded_files
        }

    except Exception as e:
        logger.error(f"playlist | URL: {url} | Error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
        
import tempfile

import requests

@app.get("/spotify/search", summary="Cari lagu di Spotify (dengan client ID)")
async def spotify_search(query: str = Query(..., description="Judul lagu atau artis")):
    try:
        token = get_spotify_access_token()

        headers = {
            "Authorization": f"Bearer {token}"
        }
        params = {
            "q": query,
            "type": "track",
            "limit": 5
        }

        resp = requests.get(f"{SPOTIFY_API_URL}/search", headers=headers, params=params)
        data = resp.json()

        tracks = data.get("tracks", {}).get("items", [])

        results = []
        for track in tracks:
            results.append({
                "spotify_url": track["external_urls"]["spotify"],
                "title": track["name"],
                "artist": ", ".join(artist["name"] for artist in track["artists"])
            })

        return {"query": query, "results": results}

    except Exception as e:
        logger.error(f"spotify_search | Query: {query} | Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
@app.get("/spotify/info", summary="Info lengkap Spotify URL (track/album/playlist)")
async def spotify_info(url: str = Query(..., description="URL Spotify track, album, atau playlist")):
    try:
        token = get_spotify_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        if "track" in url:
            spotify_type = "track"
        elif "album" in url:
            spotify_type = "album"
        elif "playlist" in url:
            spotify_type = "playlist"
        else:
            return JSONResponse(status_code=400, content={"error": "URL tidak dikenali. Harus track, album, atau playlist."})

        spotify_id = url.split("/")[-1].split("?")[0]
        endpoint = f"{SPOTIFY_API_URL}/{spotify_type}s/{spotify_id}"
        resp = requests.get(endpoint, headers=headers)
        if resp.status_code != 200:
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
        data = resp.json()

        result = {
            "type": spotify_type,
            "title": data.get("name"),
            "url": data.get("external_urls", {}).get("spotify", url),
            "thumbnail": data.get("images", [{}])[0].get("url"),
            "is_album": spotify_type == "album",
            "is_playlist": spotify_type == "playlist"
        }

        if spotify_type == "track":
            result.update({
                "artist": ", ".join(artist["name"] for artist in data["artists"]),
                "duration": round(data.get("duration_ms", 0) / 1000),
                "album": data["album"]["name"] if data.get("album") else None
            })

        elif spotify_type == "album":
            result["artist"] = ", ".join(artist["name"] for artist in data["artists"])
            result["total_tracks"] = data.get("total_tracks", len(data["tracks"]["items"]))
            tracks = [
                {
                    "title": track["name"],
                    "artist": ", ".join(artist["name"] for artist in track["artists"]),
                    "spotify_url": track["external_urls"]["spotify"]
                }
                for track in data["tracks"]["items"]
            ]
            result["tracks"] = tracks

        elif spotify_type == "playlist":
            result["owner"] = data["owner"]["display_name"]
            tracks = []
            next_url = data["tracks"]["next"]
            items = data["tracks"]["items"]
            for item in items:
                track = item.get("track")
                if track:
                    tracks.append({
                        "title": track["name"],
                        "artist": ", ".join(artist["name"] for artist in track["artists"]),
                        "spotify_url": track["external_urls"]["spotify"]
                    })

            while next_url:
                next_resp = requests.get(next_url, headers=headers)
                next_data = next_resp.json()
                for item in next_data.get("items", []):
                    track = item.get("track")
                    if track:
                        tracks.append({
                            "title": track["name"],
                            "artist": ", ".join(artist["name"] for artist in track["artists"]),
                            "spotify_url": track["external_urls"]["spotify"]
                        })
                next_url = next_data.get("next")

            result["total_tracks"] = len(tracks)
            result["tracks"] = tracks

        return result

    except Exception as e:
        logger.error(f"spotify_info | URL: {url} | Error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
        
@app.get("/spotify/download/audio", summary="Unduh audio dari Spotify track (via YouTube)")
async def spotify_download_from_track(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="URL Spotify track"),
    mode: str = Query("url")
):
    if "track" not in url:
        return JSONResponse(status_code=400, content={"error": "Hanya mendukung URL Spotify track."})

    try:
        token = get_spotify_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        spotify_id = url.split("/")[-1].split("?")[0]
        endpoint = f"{SPOTIFY_API_URL}/tracks/{spotify_id}"
        resp = requests.get(endpoint, headers=headers)
        if resp.status_code != 200:
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})

        data = resp.json()
        title = data["name"]
        artist = ", ".join(a["name"] for a in data["artists"])
        search_query = f"{title} {artist} audio"

        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_spotify_by_nauval.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'postprocessor_args': ['-vn', '-preset', 'ultrafast', '-threads', '4'],
            'prefer_ffmpeg': True,
            'noplaylist': True,
            'no_warnings': True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{search_query}", download=False)
            entry = info['entries'][0] if 'entries' in info else info

            output_filename = f"{entry['title']}_spotify_by_nauval.mp3"
            file_path = os.path.join(OUTPUT_DIR, output_filename)

            if os.path.exists(file_path):
                background_tasks.add_task(delete_file_after_delay, file_path)
                return {
                    "title": title,
                    "artist": artist,
                    "thumbnail": entry.get("thumbnail"),
                    "download_url": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(output_filename)}"
                }

            ydl.download([entry['webpage_url']])

        if not os.path.exists(file_path):
            raise FileNotFoundError("File hasil konversi tidak ditemukan.")

        background_tasks.add_task(delete_file_after_delay, file_path)

        return {
            "title": title,
            "artist": artist,
            "thumbnail": entry.get("thumbnail"),
            "download_url": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(output_filename)}"
        }

    except Exception as e:
        logger.error(f"spotify_download_audio | URL: {url} | Error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
@app.get("/spotify/download/playlist", summary="Unduh playlist Spotify jadi MP3 (via YouTube)")
async def spotify_download_playlist_audio(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="URL playlist Spotify"),
    limit: int = Query(10, ge=1, le=50, description="Jumlah maksimal lagu yang diunduh (1–50)"),
    mode: str = Query("url", description="Saat ini hanya mendukung mode 'url'")
):
    if "playlist" not in url:
        return JSONResponse(status_code=400, content={"error": "Hanya URL playlist Spotify yang didukung."})
    if mode != "url":
        return JSONResponse(status_code=400, content={"error": "Mode saat ini hanya mendukung 'url'."})

    try:
        token = get_spotify_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        spotify_id = url.split("/")[-1].split("?")[0]
        endpoint = f"{SPOTIFY_API_URL}/playlists/{spotify_id}"
        resp = requests.get(endpoint, headers=headers)
        if resp.status_code != 200:
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
        data = resp.json()

        playlist_title = data.get("name", "Spotify Playlist")
        tracks_data = data["tracks"]["items"]
        next_url = data["tracks"]["next"]

        all_tracks = []

        while len(all_tracks) < limit:
            for item in tracks_data:
                track = item.get("track")
                if track:
                    all_tracks.append({
                        "title": track["name"],
                        "artist": ", ".join(artist["name"] for artist in track["artists"])
                    })
                if len(all_tracks) >= limit:
                    break
            if next_url and len(all_tracks) < limit:
                next_resp = requests.get(next_url, headers=headers)
                next_data = next_resp.json()
                tracks_data = next_data["items"]
                next_url = next_data.get("next")
            else:
                break

        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_spotify_playlist.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'postprocessor_args': ['-vn', '-preset', 'ultrafast', '-threads', '4'],
            'prefer_ffmpeg': True,
            'noplaylist': True,
            'no_warnings': True
        }

        loop = asyncio.get_running_loop()
        downloaded = []

        def download_all():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                for idx, track in enumerate(all_tracks, start=1):
                    query = f"{track['title']} {track['artist']} audio"
                    try:
                        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                        entry = info['entries'][0] if 'entries' in info else info
                        filename = f"{entry['title']}_spotify_playlist.mp3"
                        filepath = os.path.join(OUTPUT_DIR, filename)

                        if os.path.exists(filepath):
                            downloaded.append({
                                "index": idx,
                                "title": track["title"],
                                "artist": track["artist"],
                                "download_url": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(filename)}"
                            })
                            background_tasks.add_task(delete_file_after_delay, filepath)
                            continue

                        ydl.download([entry['webpage_url']])

                        if os.path.exists(filepath):
                            downloaded.append({
                                "index": idx,
                                "title": track["title"],
                                "artist": track["artist"],
                                "download_url": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(filename)}"
                            })
                            background_tasks.add_task(delete_file_after_delay, filepath)

                    except Exception as e:
                        logger.warning(f"Gagal unduh lagu: {query} | Error: {e}")

        await loop.run_in_executor(None, download_all)

        return {
            "playlist": playlist_title,
            "total_downloaded": len(downloaded),
            "tracks": downloaded
        }

    except Exception as e:
        logger.error(f"spotify_download_playlist | URL: {url} | Error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
from zipfile import ZipFile

@app.get("/spotify/fullplaylist", summary="Unduh full playlist Spotify (MP3) dengan opsi ZIP/GDrive")
async def spotify_full_playlist_download(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="URL Spotify playlist"),
    limit: int = Query(10, ge=1, le=50),
    mode: str = Query("zip", description="Mode: url, zip")
):
    if "playlist" not in url:
        return JSONResponse(status_code=400, content={"error": "Hanya URL playlist Spotify yang didukung."})

    try:
        token = get_spotify_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        spotify_id = url.split("/")[-1].split("?")[0]
        endpoint = f"{SPOTIFY_API_URL}/playlists/{spotify_id}"
        resp = requests.get(endpoint, headers=headers)
        if resp.status_code != 200:
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
        data = resp.json()

        playlist_title = data.get("name", "Spotify Playlist")
        tracks_data = data["tracks"]["items"]
        next_url = data["tracks"]["next"]
        all_tracks = []

        while len(all_tracks) < limit:
            for item in tracks_data:
                track = item.get("track")
                if track:
                    all_tracks.append({
                        "title": track["name"],
                        "artist": ", ".join(artist["name"] for artist in track["artists"])
                    })
                if len(all_tracks) >= limit:
                    break
            if next_url and len(all_tracks) < limit:
                next_resp = requests.get(next_url, headers=headers)
                next_data = next_resp.json()
                tracks_data = next_data["items"]
                next_url = next_data.get("next")
            else:
                break

        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(OUTPUT_DIR, '%(title)s_spotifyfull.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'postprocessor_args': ['-vn', '-preset', 'ultrafast', '-threads', '4'],
            'prefer_ffmpeg': True,
            'noplaylist': True,
            'no_warnings': True
        }

        loop = asyncio.get_running_loop()
        downloaded_files = []

        def download_tracks():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                for track in all_tracks:
                    query = f"{track['title']} {track['artist']} audio"
                    try:
                        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                        entry = info['entries'][0] if 'entries' in info else info
                        filename = f"{entry['title']}_spotifyfull.mp3"
                        filepath = os.path.join(OUTPUT_DIR, filename)

                        if not os.path.exists(filepath):
                            ydl.download([entry['webpage_url']])
                        if os.path.exists(filepath):
                            downloaded_files.append(filepath)
                            background_tasks.add_task(delete_file_after_delay, filepath)

                    except Exception as e:
                        logger.warning(f"Gagal unduh: {query} | Error: {e}")

        await loop.run_in_executor(None, download_tracks)

        if mode == "url":
            return {
                "playlist": playlist_title,
                "mode": "url",
                "total_downloaded": len(downloaded_files),
                "files": [
                    f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(os.path.basename(f))}"
                    for f in downloaded_files
                ]
            }

        elif mode == "zip":
            zip_name = f"{playlist_title.replace(' ', '_')}_spotify.zip"
            zip_path = os.path.join(OUTPUT_DIR, zip_name)

            with ZipFile(zip_path, "w") as zipf:
                for file in downloaded_files:
                    zipf.write(file, arcname=os.path.basename(file))

            background_tasks.add_task(delete_file_after_delay, zip_path)

            return {
                "playlist": playlist_title,
                "mode": "zip",
                "download_zip": f"https://ytdlpyton.nvlgroup.my.id/download/file/{quote(zip_name)}"
            }

        else:
            return JSONResponse(status_code=400, content={"error": f"Mode tidak dikenali: {mode}"})

    except Exception as e:
        logger.error(f"spotify_fullplaylist | URL: {url} | Error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
        
@app.get("/download/file/{filename}", summary="Mengunduh file hasil")
async def download_file(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename)
    return JSONResponse(status_code=404, content={"error": "File tidak ditemukan"})
