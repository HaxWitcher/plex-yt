# syntax=docker/dockerfile:1.4
FROM python:3.11-slim

# 1) sistema paketi
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git \
      ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# 2) da Python ne buffer-uje logove
ENV PYTHONUNBUFFERED=1

# 3) radni direktorij
WORKDIR /app/plex-yt

# 4) kopiranje koda
COPY . /app/plex-yt

# 5) permissions
RUN mkdir -p output spotify_output \
 && chmod -R 777 output spotify_output yt.txt

# 6) dependencies
RUN pip install --no-cache-dir \
      requests \
      "uvicorn[standard]" \
 && pip install --no-cache-dir -r requirements.txt

# 7) expose
EXPOSE 7860

# 8) start na portu koji Railway zadaje u env VAR PORT
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port $PORT"]


