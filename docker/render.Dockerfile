FROM node:22-bookworm-slim AS frontend

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src src
COPY public public

ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_PUBLISHABLE_KEY
ENV VITE_SUPABASE_URL=${VITE_SUPABASE_URL} \
    VITE_SUPABASE_PUBLISHABLE_KEY=${VITE_SUPABASE_PUBLISHABLE_KEY}
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVE_FRONTEND_DIR=/app/frontend

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl clamdscan ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/worker-requirements.txt server/worker-requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r server/worker-requirements.txt

ENV PATH="/opt/venv/bin:${PATH}"
COPY --chown=1000:1000 server server
COPY --from=frontend --chown=1000:1000 /app/dist /app/frontend
RUN mkdir -p /tmp/understandvideo-uploads \
    && chown -R 1000:1000 /app /tmp/understandvideo-uploads

USER 1000:1000
EXPOSE 10000
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
