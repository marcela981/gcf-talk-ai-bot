FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/app

# `curl` is used by the HEALTHCHECK below; everything else stays slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so layer cache survives source-only changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/

# AppAPI talks to the container on APP_PORT. Bind to 0.0.0.0 so the
# Nextcloud network can reach us.
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8080
EXPOSE 8080

# `/heartbeat` is registered by nc_py_api's set_handlers(); AppAPI also polls it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT}/heartbeat" || exit 1

CMD ["python", "-m", "app.main"]
