FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv clamdscan ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/worker-requirements.txt server/worker-requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install -r server/worker-requirements.txt

COPY package.json package-lock.json ./
RUN npm ci --omit=dev \
    && chown -R node:node /app /opt/venv

ENV PATH="/opt/venv/bin:${PATH}"
COPY --chown=node:node server server
COPY --chown=node:node scripts/python.sh scripts/python.sh
COPY --chown=node:node docker/clamd-client.conf /etc/clamav/clamd.conf
USER node

CMD ["npm", "run", "jobs"]
