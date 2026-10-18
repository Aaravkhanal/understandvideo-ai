from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent
UPLOADS = ROOT / "uploads"
DATABASE = ROOT / "understandvideo.db"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 100 * 1024 * 1024))
ALLOWED_MIME = {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"}

app = FastAPI(title="UnderstandVideo AI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:4310,http://127.0.0.1:4310").split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

requests_by_identity: dict[str, deque[float]] = defaultdict(deque)


class ChatRequest(BaseModel):
    video_id: str
    message: str
    conversation_id: str | None = None


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize() -> None:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS videos (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              path TEXT NOT NULL,
              mime_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              duration REAL,
              width INTEGER,
              height INTEGER,
              status TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              conversation_id TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            """
        )


@app.on_event("startup")
def on_startup() -> None:
    initialize()


def enforce_rate_limit(identity: str, limit: int = 50, window: int = 3600) -> int:
    now = time.time()
    bucket = requests_by_identity[identity]
    while bucket and bucket[0] <= now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Hourly request limit reached")
    bucket.append(now)
    return limit - len(bucket)


def safe_name(name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9._-]", "-", Path(name).name)
    return stem[:120] or "video.mp4"


def probe_video(path: Path) -> dict[str, float | int | None]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        payload = json.loads(result.stdout)
        stream = (payload.get("streams") or [{}])[0]
        return {
            "duration": round(float(payload.get("format", {}).get("duration", 0)), 2),
            "width": stream.get("width"),
            "height": stream.get("height"),
        }
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return {"duration": None, "width": None, "height": None}


def video_record(video_id: str) -> sqlite3.Row:
    with db() as connection:
        record = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not record:
        raise HTTPException(status_code=404, detail="Video not found")
    return record


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": os.getenv("INFERENCE_MODE", "demo")}


@app.post("/api/videos/upload", status_code=201)
async def upload_video(request: Request, file: UploadFile = File(...)) -> dict:
    enforce_rate_limit(f"upload:{request.client.host if request.client else 'unknown'}", limit=3)
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="Unsupported video format")

    video_id = uuid.uuid4().hex
    target = UPLOADS / f"{video_id}-{safe_name(file.filename or 'video.mp4')}"
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Video exceeds the 100 MB limit")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    metadata = probe_video(target)
    with db() as connection:
        connection.execute(
            "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (video_id, file.filename or target.name, str(target), mime, size, metadata["duration"], metadata["width"], metadata["height"], "ready", int(time.time())),
        )
    return {"id": video_id, "name": file.filename, "size_bytes": size, "status": "ready", **metadata}


@app.get("/api/videos")
def list_videos() -> list[dict]:
    with db() as connection:
        records = connection.execute("SELECT id, name, size_bytes, duration, width, height, status, created_at FROM videos ORDER BY created_at DESC").fetchall()
    return [dict(record) for record in records]


@app.get("/api/videos/{video_id}/summary")
def summary(video_id: str) -> dict:
    video = video_record(video_id)
    duration = video["duration"] or 12
    return {
        "video_id": video_id,
        "summary": "The clip establishes its subject, develops the central action, moves through a key transition, and closes by reinforcing the main idea.",
        "tags": ["visual narrative", "key moments", "scene transition"],
        "scenes": [
            {"timestamp": 0, "label": "Opening"},
            {"timestamp": round(duration * 0.25, 1), "label": "Context"},
            {"timestamp": round(duration * 0.58, 1), "label": "Key moment"},
            {"timestamp": round(duration * 0.86, 1), "label": "Conclusion"},
        ],
        "inference_mode": os.getenv("INFERENCE_MODE", "demo"),
    }


def grounded_answer(question: str, duration: float) -> str:
    normalized = question.lower()
    if any(term in normalized for term in ("weather tomorrow", "stock price", "capital of", "write code", "recipe")):
        return "I’m focused on understanding the uploaded video. Ask me about what appears, happens, changes, or is said in this footage, and I’ll link the answer to the relevant moment."
    if "summar" in normalized:
        return "The video progresses through four main moments: an opening at [[00:00]], context-setting, a central transition, and a concise conclusion."
    if any(term in normalized for term in ("setting", "where", "location", "mood")):
        return "The video is set in a misty alpine valley with evergreen slopes, exposed rock, a waterfall, and snow-lined peaks. The cool fog and warm sunrise create a calm, adventurous mood around [[00:03]]."
    if any(term in normalized for term in ("who", "person", "wearing", "clothes")):
        return "One trail runner is visible, wearing a vivid orange jacket, dark shorts, and a small running pack. The clearest view appears around [[00:07]]."
    if any(term in normalized for term in ("change", "middle", "happen", "action", "doing")):
        return "The clip begins by establishing the mountain landscape, then shifts attention toward a runner moving along the rocky trail. Around [[00:06]], the person becomes the visual focus while the sunrise and valley remain visible behind them."
    moment = min(43, max(0, duration * 0.55))
    return f"The most relevant evidence appears near [[{int(moment // 60):02d}:{int(moment % 60):02d}]]. Based on that moment, the clip centers on a runner moving through a large mountain landscape at sunrise."


@app.post("/api/chat/stream")
async def stream_chat(request: Request, payload: ChatRequest) -> StreamingResponse:
    remaining = enforce_rate_limit(f"chat:{request.client.host if request.client else 'unknown'}")
    video = video_record(payload.video_id)
    conversation_id = payload.conversation_id or uuid.uuid4().hex
    answer = grounded_answer(payload.message, video["duration"] or 12)
    now = int(time.time())
    with db() as connection:
        connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, payload.video_id, conversation_id, "user", payload.message, now))
        connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, payload.video_id, conversation_id, "assistant", answer, now))

    async def events() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conversation_id, 'remaining': remaining})}\n\n"
        for token in answer.split(" "):
            if await request.is_disconnected():
                return
            yield f"data: {json.dumps({'type': 'token', 'text': token + ' '})}\n\n"
            await asyncio.sleep(0.035)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/videos/{video_id}/messages")
def messages(video_id: str) -> list[dict]:
    video_record(video_id)
    with db() as connection:
        records = connection.execute("SELECT id, conversation_id, role, content, created_at FROM messages WHERE video_id = ? ORDER BY created_at", (video_id,)).fetchall()
    return [dict(record) for record in records]
