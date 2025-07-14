from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import yt_dlp, os, asyncio, subprocess, uuid, logging, pathlib, requests, base64
from datetime import datetime

# --- Paths ---
BASE_DIR      = pathlib.Path(__file__).parent.resolve()
COOKIES_FILE  = str(BASE_DIR / "yt.txt")
OUTPUT_DIR    = str(BASE_DIR / "output")
HLS_ROOT      = str(BASE_DIR / "hls_segments")

# --- Ensure dirs exist ---
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(HLS_ROOT, exist_ok=True)

# --- Concurrency & Logging ---
download_semaphore = asyncio.Semaphore(30)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FastAPI setup ---
app = FastAPI(title="YouTube Downloader with HLS", version="2.0.3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/hls", StaticFiles(directory=HLS_ROOT), name="hls")

def load_cookies_header() -> str:
    cookies = []
    with open(COOKIES_FILE) as f:
        for line in f:
            if line.startswith('#') or not line.strip(): continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies.append(f"{parts[5]}={parts[6]}")
    return '; '.join(cookies)

@app.middleware("http")
async def log_requests(req: Request, call_next):
    start = datetime.now()
    res = await call_next(req)
    ms = (datetime.now() - start).microseconds/1000
    logger.info(f"{req.client.host} {req.method} {req.url} -> {res.status_code} [{ms:.1f}ms]")
    return res

@app.get("/", summary="Root")
async def root():
    return JSONResponse({"status":"ok"})

@app.get("/stream/", summary="HLS stream")
async def stream_video(request: Request, url: str = Query(...), resolution: int = Query(1080)):
    try:
        # 1) Get formats via yt-dlp
        ydl_opts = {'quiet':True, 'cookiefile':COOKIES_FILE, 'no_warnings':True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 2) Pick formats
        vid = next(f for f in info['formats'] if f.get('vcodec')!='none' and f.get('height')==resolution and f.get('ext')=='mp4')
        aud = max((f for f in info['formats'] if f.get('vcodec')=='none' and f.get('acodec')!='none'),
                  key=lambda x: x.get('abr',0))

        # 3) HLS session dir
        sess = uuid.uuid4().hex
        sess_dir = os.path.join(HLS_ROOT, sess)
        os.makedirs(sess_dir, exist_ok=True)

        # 4) Spawn ffmpeg -> HLS
        cookie_header = load_cookies_header()
        hdr = ['-headers', f"Cookie: {cookie_header}\r\n"]
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            *hdr, '-i', vid['url'],
            *hdr, '-i', aud['url'],
            '-c:v','copy','-c:a','copy',
            '-f','hls','-hls_time','4','-hls_list_size','0',
            '-hls_flags','delete_segments+append_list',
            '-hls_segment_filename', os.path.join(sess_dir,'seg_%03d.ts'),
            os.path.join(sess_dir,'index.m3u8')
        ]
        subprocess.Popen(cmd, cwd=sess_dir)

        # 5) Redirect client to playlist
        playlist = request.url_for('hls', path=f"{sess}/index.m3u8")
        return RedirectResponse(playlist)

    except StopIteration:
        raise HTTPException(404, f"Nema {resolution}p video toka")
    except Exception as e:
        logger.error("stream_video:", exc_info=True)
        raise HTTPException(500, str(e))
