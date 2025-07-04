from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
import yt_dlp
import requests

app = FastAPI(
    title="YouTube dan Spotify Downloader API",
    description="API untuk mengunduh video dan audio dari YouTube dan Spotify.",
    version="2.0.0"
)

# ... sve postojeće importe, middleware, varijable i endpointi ostaju nepromijenjeni ...

@app.get(
    "/stream",
    summary="Stream YouTube video s HTTP Range podrškom",
    description="Proxy-a progresivni MP4 (video+audio) izravno s YouTubea, s podrškom za Range zaglavlja."
)
async def stream_video(
    request: Request,
    url: str = Query(..., description="YouTube video URL"),
    resolution: int = Query(1080, description="Maksimalna visina videa u px")
):
    # 1) Izvući direktni URL progresivnog MP4 fajla
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'format': f'best[ext=mp4][height<={resolution}][acodec!=none]'
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        media_url = info.get('url')
    if not media_url:
        raise HTTPException(status_code=404, detail="Ne mogu dohvatiti media URL")

    # 2) Proslijedi Range zaglavlje ako ga je client poslao
    headers = {}
    range_header = request.headers.get('range')
    if range_header:
        headers['Range'] = range_header

    # 3) Proxy request na YouTube
    upstream = requests.get(media_url, headers=headers, stream=True)
    if upstream.status_code not in (200, 206):
        raise HTTPException(status_code=upstream.status_code, detail="Greška pri dohvaćanju streama")

    # 4) Generator koji šalje velike blokove (1 MiB)
    def iter_chunks():
        for chunk in upstream.iter_content(chunk_size=1024*1024):
            if chunk:
                yield chunk

    # 5) Kopiraj najvažnija zaglavlja natrag clientu
    response_headers = {}
    for h in ('Content-Range', 'Accept-Ranges', 'Content-Length', 'Content-Type'):
        val = upstream.headers.get(h)
        if val:
            response_headers[h] = val

    return StreamingResponse(
        iter_chunks(),
        status_code=upstream.status_code,
        headers=response_headers
    )

# ... ostatak vaših endpointa (/download/, /search/, spotify..., itd.) ...
