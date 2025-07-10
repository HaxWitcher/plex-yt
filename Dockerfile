# syntax=docker/dockerfile:1.4
FROM python:3.11-slim

# instaliraj OS pakete
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg git \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# kopiraj dependencies pa instaliraj
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# kopiraj sav kod (main.py, yt.txt, output mape, ...)
COPY . .

# da container knows to listen on whatever PORT Railway postavi
ENV PORT  ${PORT:-7860}

# expose bez obzira
EXPOSE $PORT

# koristi shell form da $PORT zaživi
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
