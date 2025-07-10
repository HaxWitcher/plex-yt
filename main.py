from fastapi.responses import RedirectResponse

@app.get("/stream/", summary="Streamuj video odmah bez čekanja čitavog download-a")
async def stream_video(url: str = Query(...), resolution: int = Query(1080)):
    try:
        # 1) Učitaj cookies iz tvog yt.txt i složi ih u header
        cookie_header = load_cookies_header()  # ista funkcija koju već imaš

        # 2) Pozovi yt-dlp samo za ekstrakciju info, s tvojim kolačićima
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "http_headers": {"Cookie": cookie_header},
            # forsiraj hls ako ga ima
            "hls_prefer_native": True,
            "hls_allow_cache": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 3) Pronađi HLS master playlist (m3u8) format
        hls_fmt = next(
            f for f in info["formats"]
            if f.get("protocol", "").startswith("m3u8")
        )

        # 4) Vraćamo redirect na tu playlistu — klijent (VLC/browser) sad izravno povlači segmente
        return RedirectResponse(hls_fmt["url"])

    except StopIteration:
        raise HTTPException(
            status_code=404,
            detail=f"Nema HLS (m3u8) tokova za odabranu rezoluciju."
        )
    except Exception as e:
        logger.error(f"stream_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
