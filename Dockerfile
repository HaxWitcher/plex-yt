# syntax=docker/dockerfile:1.4
FROM python:3.11-slim

# — instaliraj OS pakete
RUN apt-get update \
 && apt-get install -y git ffmpeg \
 && apt-get clean

WORKDIR /app

# — kopiraj sav kod (ili clone Git ako ti više paše)
COPY . .

# — output direktoriji
RUN mkdir -p output spotify_output \
 && chmod -R 777 output spotify_output \
 && chmod 777 yt.txt

# — instaliraj Python deps iz requirements
RUN pip install --no-cache-dir -r requirements.txt

# — izloži port (nije obavezno za Railway, ali dokumentira)
EXPOSE 7860

# — slušaj na $PORT ili 7860 ako nije definiran
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
