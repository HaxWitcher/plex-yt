from fastapi.responses import RedirectResponse

# --- Streaming endpoint (HLS preusmjerenje) ---
@app.get("/stream/", summary="Streamuj video odmah bez čekanja čitavog download-a")
async def stream_video(url: str = Query(...), resolution: int = Query(1080)):
    try:
        # 1) Izvuci sve tokove uz cookies
        ydl_opts = {
            "quiet": True,
            "cookiefile": COOKIES_FILE,
            # omogućimo da yt-dlp prijavi m3u8 natvie URL
            "hls_prefer_native": True,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 2) Pronađi HLS (m3u8) tok koji sadrži varijante (master playlist)
        hls = next(
            fmt for fmt in info["formats"]
            if fmt.get("protocol", "").startswith("m3u8")
        )

        # 3) Preusmjeri klijenta direktno na tu playlistu
        return RedirectResponse(hls["url"])

    except StopIteration:
        raise HTTPException(
            status_code=404,
            detail=f"Nema HLS (m3u8) tokova za odabranu rezoluciju."
        )
    except Exception as e:
        logger.error(f"stream_video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
