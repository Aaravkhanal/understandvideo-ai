FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl clamdscan ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/worker-requirements.txt server/worker-requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r server/worker-requirements.txt

ENV PATH="/opt/venv/bin:${PATH}"
COPY --chown=1000:1000 server server
COPY --chown=1000:1000 docker/clamd-client.conf /etc/clamav/clamd.conf
RUN mkdir -p /app/server/uploads && chown -R 1000:1000 /app

USER 1000:1000
EXPOSE 8787
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips=*"]
