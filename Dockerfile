# ── W.I.N.S.T.O.N. Docker Image ──────────────────────
# Multi-stage build for a lean production image.
# Runs in server mode (Web UI + Telegram + Discord).

FROM python:3.12-slim AS base

# System deps for audio processing, browser automation, and general tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        git \
        libportaudio2 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependencies layer (cached unless requirements change) ─
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium for browser skill
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

# ── Application code ──────────────────────────────────
COPY winston/ ./winston/
COPY config/ ./config/
COPY docs/ ./docs/
COPY setup.py ./

# Expose web UI port
EXPOSE 8000

# Data volume for persistent settings, memory, notes
VOLUME ["/app/data"]

# Default: server mode (Web UI + channels)
ENV WINSTON_MODE=server
ENV OLLAMA_HOST=http://ollama:11434

ENTRYPOINT ["python", "-m", "winston.main"]
CMD ["server", "--port", "8000"]
