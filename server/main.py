from __future__ import annotations

import asyncio
import hashlib
import html
import hmac
import importlib.util
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import secrets
import time
import urllib.request
import uuid
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator, Any, Iterator

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl, field_validator

from server.analysis import AnalysisError, extract_scene_thumbnails
from server.auth import Identity, auth_mode, verify_bearer_token
from server.database import connect, using_postgres
from server.inference import answer_video_question, inference_mode, is_demo_video
from server.job_queue import notify_jobs, redis_client
from server.remote_import import RemoteImportCancelled, RemoteImportError, import_remote_video, platform_hosts, remote_source_details, validate_public_https_url
from server.storage import LocalStorage, StorageError, SupabaseStorage
from server.transcription import NoAudioTrack, TranscriptionError, TranscriptionUnavailable, transcribe_video


ROOT = Path(__file__).resolve().parent
UPLOADS = Path(os.getenv("UPLOAD_ROOT", str(ROOT / "uploads"))).expanduser().resolve()
DATABASE = Path(os.getenv("DATABASE_PATH", str(ROOT / "understandvideo.db"))).expanduser().resolve()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 100 * 1024 * 1024))
ALLOWED_MIME = {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"}
CHAT_REQUESTS_PER_HOUR = int(os.getenv("CHAT_REQUESTS_PER_HOUR", "50"))
UPLOADS_PER_HOUR = int(os.getenv("UPLOADS_PER_HOUR", "20"))
MAX_REMOTE_DURATION_SECONDS = int(os.getenv("MAX_REMOTE_DURATION_SECONDS", "3600"))
REMOTE_IMPORT_TIMEOUT_SECONDS = int(os.getenv("REMOTE_IMPORT_TIMEOUT_SECONDS", "180"))
IMPORT_AUTO_RETRIES = int(os.getenv("IMPORT_AUTO_RETRIES", "2"))
MALWARE_SCAN_MODE = os.getenv("MALWARE_SCAN_MODE", "optional").strip().lower()
DEFAULT_RETENTION_DAYS = int(os.getenv("DEFAULT_RETENTION_DAYS", "90"))
LOCAL_ACCOUNT_ID = os.getenv("LOCAL_ACCOUNT_ID", "local-account")
ACTIVE_WORKSPACE_ID = os.getenv("LOCAL_WORKSPACE_ID", "local-workspace")
LOCAL_WORKSPACE_NAME = os.getenv("LOCAL_WORKSPACE_NAME", "Personal workspace")
request_identity: ContextVar[Identity | None] = ContextVar("request_identity", default=None)
logger = logging.getLogger(__name__)

if DEFAULT_RETENTION_DAYS not in {0, 30, 90, 365}:
    raise RuntimeError("DEFAULT_RETENTION_DAYS must be 0, 30, 90, or 365")

app = FastAPI(title="UnderstandVideo AI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:4310,http://127.0.0.1:4310").split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

startup_tasks: set[asyncio.Task] = set()
cancelled_video_ids: set[str] = set()
cancelled_import_ids: set[str] = set()
request_metrics: Counter[str] = Counter()
request_duration_ms = 0.0
# Kept as a harmless compatibility surface for older local test fixtures.
requests_by_identity: dict = {}


def active_account_id() -> str:
    identity = request_identity.get()
    return identity.account_id if identity else LOCAL_ACCOUNT_ID


def active_workspace_id() -> str:
    identity = request_identity.get()
    return identity.workspace_id if identity else ACTIVE_WORKSPACE_ID


class ChatRequest(BaseModel):
    video_id: str
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Message cannot be blank")
        return cleaned


class UrlImportRequest(BaseModel):
    url: HttpUrl


class RenameVideoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=180)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return value.strip() or "New conversation"


class ConversationRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Title cannot be blank")
        return cleaned


class AnnotationCreateRequest(BaseModel):
    timestamp: float = Field(ge=0, le=86_400)
    note: str = Field(min_length=1, max_length=1_000)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Annotation cannot be blank")
        return cleaned


class TagCreateRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=40)

    @field_validator("tag")
    @classmethod
    def clean_tag(cls, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip().lower())
        if not cleaned:
            raise ValueError("Tag cannot be blank")
        return cleaned


class ShareCreateRequest(BaseModel):
    timestamp: float = Field(default=0, ge=0, le=86_400)
    expires_hours: int = Field(default=168, ge=1, le=720)


class CompareVideosRequest(BaseModel):
    video_ids: list[str] = Field(min_length=2, max_length=2)

    @field_validator("video_ids")
    @classmethod
    def distinct_videos(cls, value: list[str]) -> list[str]:
        if len(set(value)) != 2:
            raise ValueError("Choose two different videos")
        return value


class RetentionRequest(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)


class ReviewUpdateRequest(BaseModel):
    state: str = Field(pattern="^(draft|in_review|approved|changes_requested)$")
    assignee: str | None = Field(default=None, max_length=120)


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    timestamp: float | None = Field(default=None, ge=0, le=86400)

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        return value.strip()


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class InvitationCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(default="viewer", pattern="^(viewer|editor)$")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("Enter a valid email address")
        return normalized


class InvitationAcceptRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class WebhookCreateRequest(BaseModel):
    url: HttpUrl
    events: list[str] = Field(min_length=1, max_length=10)

    @field_validator("events")
    @classmethod
    def supported_events(cls, value: list[str]) -> list[str]:
        allowed = {"video.ready", "import.failed", "review.updated"}
        normalized = sorted(set(value))
        if not set(normalized).issubset(allowed):
            raise ValueError("Unsupported webhook event")
        return normalized


def db() -> Iterator[Any]:
    return connect(DATABASE)


def verify_api_key(request: Request) -> Identity | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer uv_"):
        return None
    raw_key = authorization.removeprefix("Bearer ").strip()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    with db() as connection:
        record = connection.execute(
            """
            SELECT api_keys.id, api_keys.workspace_id, workspaces.owner_account_id,
                   accounts.email, accounts.display_name
            FROM api_keys
            JOIN workspaces ON workspaces.id = api_keys.workspace_id
            JOIN accounts ON accounts.id = workspaces.owner_account_id
            WHERE api_keys.key_hash = ? AND api_keys.revoked_at IS NULL
            """,
            (key_hash,),
        ).fetchone()
        if record:
            connection.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (int(time.time()), record["id"]),
            )
    if not record:
        raise HTTPException(status_code=401, detail="The API key is invalid or revoked")
    return Identity(
        account_id=record["owner_account_id"],
        workspace_id=record["workspace_id"],
        email=record["email"],
        display_name=record["display_name"],
    )


def storage() -> LocalStorage:
    if os.getenv("OBJECT_STORAGE_PROVIDER", "").strip().lower() == "supabase":
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        bucket = os.getenv("OBJECT_STORAGE_BUCKET", "").strip()
        endpoint = os.getenv("OBJECT_STORAGE_ENDPOINT", "").strip()
        if service_key and bucket and endpoint:
            return SupabaseStorage(UPLOADS, endpoint, bucket, service_key)
    return LocalStorage(UPLOADS)


def initialize() -> None:
    storage().ensure_ready()
    if not using_postgres():
        DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
              id TEXT PRIMARY KEY,
              email TEXT,
              display_name TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspaces (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              owner_account_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              FOREIGN KEY (owner_account_id) REFERENCES accounts(id)
            );
            CREATE TABLE IF NOT EXISTS workspace_members (
              workspace_id TEXT NOT NULL,
              account_id TEXT NOT NULL,
              role TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY (workspace_id, account_id),
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
              FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS videos (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL DEFAULT 'local-workspace',
              name TEXT NOT NULL,
              path TEXT NOT NULL,
              mime_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              duration REAL,
              width INTEGER,
              height INTEGER,
              status TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              source_type TEXT NOT NULL DEFAULT 'upload',
              source_url TEXT,
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              conversation_id TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              title TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS analysis_jobs (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              error TEXT,
              attempts INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS scenes (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              start_seconds REAL NOT NULL,
              end_seconds REAL NOT NULL,
              label TEXT NOT NULL,
              thumbnail_path TEXT NOT NULL,
              detection_method TEXT NOT NULL DEFAULT 'uniform-reference',
              created_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
              UNIQUE(video_id, position)
            );
            CREATE TABLE IF NOT EXISTS transcript_jobs (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL,
              error TEXT,
              attempts INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS transcript_segments (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              start_seconds REAL NOT NULL,
              end_seconds REAL NOT NULL,
              text TEXT NOT NULL,
              speaker TEXT,
              created_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
              UNIQUE(video_id, position)
            );
            CREATE TABLE IF NOT EXISTS transcript_chapters (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              start_seconds REAL NOT NULL,
              end_seconds REAL NOT NULL,
              title TEXT NOT NULL,
              summary TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
              UNIQUE(video_id, position)
            );
            CREATE TABLE IF NOT EXISTS transcript_highlights (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              segment_id TEXT NOT NULL,
              created_by TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
              FOREIGN KEY (segment_id) REFERENCES transcript_segments(id) ON DELETE CASCADE,
              UNIQUE(video_id, segment_id)
            );
            CREATE TABLE IF NOT EXISTS annotations (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              workspace_id TEXT NOT NULL,
              account_id TEXT NOT NULL,
              timestamp REAL NOT NULL,
              note TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
              FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS video_tags (
              video_id TEXT NOT NULL,
              tag TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY (video_id, tag),
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS share_links (
              token TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              workspace_id TEXT NOT NULL,
              timestamp REAL NOT NULL,
              expires_at INTEGER NOT NULL,
              created_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS import_jobs (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              source_url TEXT NOT NULL,
              status TEXT NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              detail TEXT,
              video_id TEXT,
              error TEXT,
              cancel_requested INTEGER NOT NULL DEFAULT 0,
              attempts INTEGER NOT NULL DEFAULT 0,
              platform TEXT,
              downloaded_bytes INTEGER,
              total_bytes INTEGER,
              speed_bps REAL,
              eta_seconds INTEGER,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS rate_limit_buckets (
              identity TEXT NOT NULL,
              action TEXT NOT NULL,
              window_start INTEGER NOT NULL,
              request_count INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (identity, action, window_start)
            );
            CREATE TABLE IF NOT EXISTS video_reviews (
              video_id TEXT PRIMARY KEY,
              state TEXT NOT NULL DEFAULT 'draft',
              assignee TEXT,
              updated_by TEXT NOT NULL,
              updated_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS video_comments (
              id TEXT PRIMARY KEY,
              video_id TEXT NOT NULL,
              account_id TEXT NOT NULL,
              body TEXT NOT NULL,
              timestamp REAL,
              created_at INTEGER NOT NULL,
              FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
              FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS api_keys (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              name TEXT NOT NULL,
              key_prefix TEXT NOT NULL,
              key_hash TEXT NOT NULL UNIQUE,
              created_at INTEGER NOT NULL,
              last_used_at INTEGER,
              revoked_at INTEGER,
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS workspace_invitations (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              email TEXT NOT NULL,
              role TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,
              invited_by TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              accepted_at INTEGER,
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
              FOREIGN KEY (invited_by) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS webhook_subscriptions (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              url TEXT NOT NULL,
              events TEXT NOT NULL,
              signing_secret TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              disabled_at INTEGER,
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_messages_video_conversation_created
              ON messages(video_id, conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_conversations_video_updated
              ON conversations(video_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_created_at
              ON videos(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_scenes_video_position
              ON scenes(video_id, position);
            CREATE INDEX IF NOT EXISTS idx_transcript_segments_video_position
              ON transcript_segments(video_id, position);
            CREATE INDEX IF NOT EXISTS idx_transcript_chapters_video_position
              ON transcript_chapters(video_id, position);
            CREATE INDEX IF NOT EXISTS idx_video_comments_video_created
              ON video_comments(video_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_annotations_video_time
              ON annotations(video_id, timestamp, created_at);
            CREATE INDEX IF NOT EXISTS idx_video_tags_tag
              ON video_tags(tag, video_id);
            CREATE INDEX IF NOT EXISTS idx_share_links_video
              ON share_links(video_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_import_jobs_workspace_created
              ON import_jobs(workspace_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_workspace_invitations_workspace_created
              ON workspace_invitations(workspace_id, created_at DESC);
            """
        )
        now = int(time.time())
        connection.execute(
            "INSERT OR IGNORE INTO accounts (id, email, display_name, created_at) VALUES (?, NULL, ?, ?)",
            (LOCAL_ACCOUNT_ID, "Local user", now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, name, owner_account_id, created_at) VALUES (?, ?, ?, ?)",
            (ACTIVE_WORKSPACE_ID, LOCAL_WORKSPACE_NAME, LOCAL_ACCOUNT_ID, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workspace_members (workspace_id, account_id, role, created_at) VALUES (?, ?, 'owner', ?)",
            (ACTIVE_WORKSPACE_ID, LOCAL_ACCOUNT_ID, now),
        )
        video_columns = {row["name"] for row in connection.execute("PRAGMA table_info(videos)")}
        if "workspace_id" not in video_columns:
            connection.execute("ALTER TABLE videos ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'local-workspace'")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_workspace_created ON videos(workspace_id, created_at DESC)"
        )
        connection.execute(
            "UPDATE videos SET workspace_id = ? WHERE workspace_id IS NULL OR workspace_id = '' OR workspace_id = 'local-workspace'",
            (ACTIVE_WORKSPACE_ID,),
        )
        if "source_type" not in video_columns:
            connection.execute("ALTER TABLE videos ADD COLUMN source_type TEXT NOT NULL DEFAULT 'upload'")
        if "storage_key" not in video_columns:
            connection.execute("ALTER TABLE videos ADD COLUMN storage_key TEXT")
        if "source_url" not in video_columns:
            connection.execute("ALTER TABLE videos ADD COLUMN source_url TEXT")
        if "expires_at" not in video_columns:
            connection.execute("ALTER TABLE videos ADD COLUMN expires_at INTEGER")
        transcript_columns = {row["name"] for row in connection.execute("PRAGMA table_info(transcript_segments)")}
        if "speaker" not in transcript_columns:
            connection.execute("ALTER TABLE transcript_segments ADD COLUMN speaker TEXT")
        import_columns = {row["name"] for row in connection.execute("PRAGMA table_info(import_jobs)")}
        if "attempts" not in import_columns:
            connection.execute("ALTER TABLE import_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        for column, definition in {
            "platform": "TEXT",
            "downloaded_bytes": "INTEGER",
            "total_bytes": "INTEGER",
            "speed_bps": "REAL",
            "eta_seconds": "INTEGER",
        }.items():
            if column not in import_columns:
                connection.execute(f"ALTER TABLE import_jobs ADD COLUMN {column} {definition}")
        scene_columns = {row["name"] for row in connection.execute("PRAGMA table_info(scenes)")}
        if "detection_method" not in scene_columns:
            connection.execute("ALTER TABLE scenes ADD COLUMN detection_method TEXT NOT NULL DEFAULT 'uniform-reference'")
        migrated_uploads = connection.execute(
            "SELECT id, path FROM videos WHERE source_type = 'upload' AND source_url IS NULL"
        ).fetchall()
        for record in migrated_uploads:
            if is_demo_video(Path(record["path"])):
                connection.execute("UPDATE videos SET source_type = 'demo' WHERE id = ?", (record["id"],))
        legacy_conversations = connection.execute(
            """
            SELECT video_id, conversation_id, MIN(created_at) AS created_at, MAX(created_at) AS updated_at
            FROM messages GROUP BY video_id, conversation_id
            """
        ).fetchall()
        for legacy in legacy_conversations:
            first_message = connection.execute(
                """
                SELECT content FROM messages
                WHERE video_id = ? AND conversation_id = ? AND role = 'user'
                ORDER BY created_at, rowid LIMIT 1
                """,
                (legacy["video_id"], legacy["conversation_id"]),
            ).fetchone()
            title = (first_message["content"] if first_message else "Conversation").strip()[:120] or "Conversation"
            connection.execute(
                """
                INSERT OR IGNORE INTO conversations (id, video_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (legacy["conversation_id"], legacy["video_id"], title, legacy["created_at"], legacy["updated_at"]),
            )
        connection.execute(
            """
            UPDATE analysis_jobs
            SET status = 'queued', progress = 0, error = NULL, updated_at = ?
            WHERE video_id IN (
              SELECT DISTINCT video_id FROM scenes
              WHERE detection_method = 'uniform-reference'
            )
            """,
            (now,),
        )
        connection.execute("UPDATE analysis_jobs SET status = 'queued', progress = 0 WHERE status = 'processing'")
        connection.execute(
            """
            INSERT INTO analysis_jobs (id, video_id, status, progress, error, attempts, created_at, updated_at)
            SELECT lower(hex(randomblob(16))), videos.id, 'queued', 0, NULL, 0, ?, ?
            FROM videos LEFT JOIN analysis_jobs ON analysis_jobs.video_id = videos.id
            WHERE analysis_jobs.video_id IS NULL AND videos.source_type != 'demo'
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO transcript_jobs (id, video_id, status, error, attempts, created_at, updated_at)
            SELECT lower(hex(randomblob(16))), videos.id, 'queued', NULL, 0, ?, ?
            FROM videos LEFT JOIN transcript_jobs ON transcript_jobs.video_id = videos.id
            WHERE transcript_jobs.video_id IS NULL AND videos.source_type != 'demo'
            """,
            (now, now),
        )
        connection.execute("UPDATE transcript_jobs SET status = 'queued' WHERE status = 'processing'")
        connection.execute(
            "UPDATE import_jobs SET status = 'queued', progress = MIN(progress, 5), error = NULL WHERE status = 'processing'"
        )


def ensure_identity(identity: Identity) -> None:
    now = int(time.time())
    name = identity.display_name or identity.email or "User"
    with db() as connection:
        connection.execute(
            """
            INSERT INTO accounts (id, email, display_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              email = COALESCE(excluded.email, accounts.email),
              display_name = excluded.display_name
            """,
            (identity.account_id, identity.email, name, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, name, owner_account_id, created_at) VALUES (?, ?, ?, ?)",
            (identity.workspace_id, "Personal workspace", identity.account_id, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workspace_members (workspace_id, account_id, role, created_at) VALUES (?, ?, 'owner', ?)",
            (identity.workspace_id, identity.account_id, now),
        )


def select_workspace(identity: Identity, requested_workspace_id: str | None) -> Identity:
    if not requested_workspace_id or requested_workspace_id == identity.workspace_id:
        return identity
    with db() as connection:
        membership = connection.execute(
            "SELECT 1 FROM workspace_members WHERE workspace_id = ? AND account_id = ?",
            (requested_workspace_id, identity.account_id),
        ).fetchone()
    if not membership:
        raise HTTPException(status_code=403, detail="You do not have access to this workspace")
    return Identity(
        account_id=identity.account_id,
        workspace_id=requested_workspace_id,
        email=identity.email,
        display_name=identity.display_name,
    )


def require_workspace_role(*allowed_roles: str) -> str:
    with db() as connection:
        membership = connection.execute(
            "SELECT role FROM workspace_members WHERE workspace_id = ? AND account_id = ?",
            (active_workspace_id(), active_account_id()),
        ).fetchone()
    if not membership or membership["role"] not in allowed_roles:
        raise HTTPException(status_code=403, detail="Your workspace role does not allow this action")
    return membership["role"]


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    public_path = (
        request.method == "OPTIONS"
        or not request.url.path.startswith("/api")
        or request.url.path in {"/api/health", "/api/ready", "/api/config"}
        or request.url.path.startswith("/api/shares/")
    )
    identity = None
    if not public_path or request.headers.get("authorization"):
        try:
            identity = verify_api_key(request) or verify_bearer_token(request)
        except HTTPException as exc:
            return Response(
                content=json.dumps({"detail": exc.detail}),
                status_code=exc.status_code,
                media_type="application/json",
            )
    if not identity and auth_mode() == "required" and not public_path:
        return Response(content='{"detail":"Sign in is required"}', status_code=401, media_type="application/json")
    token = request_identity.set(identity)
    try:
        if identity:
            ensure_identity(identity)
            identity = select_workspace(identity, request.headers.get("x-workspace-id"))
            request_identity.set(identity)
        return await call_next(request)
    finally:
        request_identity.reset(token)


@app.on_event("startup")
async def on_startup() -> None:
    initialize()
    if os.getenv("RUN_BACKGROUND_JOBS", "true").strip().lower() in {"0", "false", "off"}:
        return
    purge_expired_videos()
    import_task = asyncio.create_task(asyncio.to_thread(run_pending_import_jobs))
    startup_tasks.add(import_task)
    import_task.add_done_callback(startup_tasks.discard)
    task = asyncio.create_task(asyncio.to_thread(run_pending_analysis_jobs))
    startup_tasks.add(task)
    task.add_done_callback(startup_tasks.discard)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    global request_duration_ms
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        request_metrics["status_5xx"] += 1
        raise
    elapsed = (time.perf_counter() - started) * 1000
    request_duration_ms += elapsed
    request_metrics["requests"] += 1
    request_metrics[f"status_{response.status_code // 100}xx"] += 1
    response.headers["X-Request-ID"] = request_id
    response.headers["Server-Timing"] = f"app;dur={elapsed:.1f}"
    logger.info(
        "request_complete request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


def enforce_rate_limit(action: str, limit: int = 50, window: int = 3600) -> int:
    now = int(time.time())
    window_start = now - (now % window)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO rate_limit_buckets (identity, action, window_start, request_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(identity, action, window_start)
            DO UPDATE SET request_count = rate_limit_buckets.request_count + 1
            """,
            (active_account_id(), action, window_start),
        )
        count = connection.execute(
            "SELECT request_count FROM rate_limit_buckets WHERE identity = ? AND action = ? AND window_start = ?",
            (active_account_id(), action, window_start),
        ).fetchone()["request_count"]
        connection.execute("DELETE FROM rate_limit_buckets WHERE window_start < ?", (window_start - window * 2,))
    if count > limit:
        raise HTTPException(status_code=429, detail="This account has reached its hourly request limit")
    return max(0, limit - count)


def safe_name(name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9._-]", "-", Path(name).name)
    return stem[:120] or "video.mp4"


def default_expiry(created_at: int) -> int | None:
    return created_at + DEFAULT_RETENTION_DAYS * 86400 if DEFAULT_RETENTION_DAYS else None


def purge_expired_videos() -> int:
    now = int(time.time())
    with db() as connection:
        expired = connection.execute(
            "SELECT id, path, storage_key FROM videos WHERE workspace_id = ? AND expires_at IS NOT NULL AND expires_at <= ?",
            (active_workspace_id(), now),
        ).fetchall()
    for video in expired:
        cancelled_video_ids.add(video["id"])
        with db() as connection:
            connection.execute("DELETE FROM messages WHERE video_id = ?", (video["id"],))
            connection.execute("DELETE FROM conversations WHERE video_id = ?", (video["id"],))
            connection.execute("DELETE FROM videos WHERE id = ?", (video["id"],))
        try:
            storage().delete_video(Path(video["path"]), video["id"], video["storage_key"])
        except StorageError:
            logger.exception("Could not remove expired video files for %s", video["id"])
    return len(expired)


def sniff_video_mime(path: Path) -> str | None:
    try:
        with path.open("rb") as media_file:
            header = media_file.read(16)
    except OSError:
        return None
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/quicktime" if header[8:12] == b"qt  " else "video/mp4"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return "video/x-msvideo"
    return None


def validated_video_metadata(path: Path, declared_mime: str) -> tuple[str, dict[str, float | int | None]]:
    detected_mime = sniff_video_mime(path)
    if not detected_mime:
        raise HTTPException(status_code=415, detail="The file contents are not a supported video container")
    compatible = {
        "video/mp4": {"video/mp4", "video/quicktime"},
        "video/quicktime": {"video/mp4", "video/quicktime"},
        "video/webm": {"video/webm"},
        "video/x-msvideo": {"video/x-msvideo"},
    }
    if detected_mime not in compatible.get(declared_mime, set()):
        raise HTTPException(status_code=415, detail="The file contents do not match the selected video type")
    scan_for_malware(path)
    metadata = probe_video(path)
    if not metadata["duration"] or not metadata["width"] or not metadata["height"]:
        raise HTTPException(status_code=422, detail="The video could not be decoded")
    return detected_mime, metadata


def scan_for_malware(path: Path) -> None:
    """Use the host scanner when present; production can require it explicitly."""
    if MALWARE_SCAN_MODE == "off":
        return
    scanner = shutil.which("clamdscan") or shutil.which("clamscan")
    if not scanner:
        if MALWARE_SCAN_MODE == "required":
            raise HTTPException(status_code=503, detail="Malware scanning is required but no scanner is available")
        return
    try:
        scan_args = [scanner, "--no-summary"]
        if Path(scanner).name == "clamdscan":
            # Stream bytes to a private ClamAV service; it does not need access
            # to the worker's media volume or host filesystem paths.
            scan_args.append("--stream")
        scan_args.append(str(path))
        result = subprocess.run(
            scan_args, capture_output=True, text=True, timeout=90, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=503, detail="Malware scanning timed out") from exc
    if result.returncode == 1:
        raise HTTPException(status_code=422, detail="The file was rejected by the malware scanner")
    if result.returncode not in (0, 1) and MALWARE_SCAN_MODE == "required":
        raise HTTPException(status_code=503, detail="Malware scanning could not be completed")


def build_chapters(segments) -> list[dict]:
    """Build readable chapters locally, without pretending to be model-generated."""
    if not segments:
        return []
    chapters: list[list] = []
    current: list = []
    chapter_start = float(segments[0].start_seconds)
    for segment in segments:
        if current and (float(segment.end_seconds) - chapter_start >= 180 or float(segment.start_seconds) - float(current[-1].end_seconds) >= 12):
            chapters.append(current)
            current = []
            chapter_start = float(segment.start_seconds)
        current.append(segment)
    if current:
        chapters.append(current)
    result = []
    for position, group in enumerate(chapters):
        summary = " ".join(item.text.strip() for item in group).strip()
        title_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", summary)[:8]
        title = " ".join(title_words) or f"Chapter {position + 1}"
        if len(summary) > 280:
            summary = summary[:277].rsplit(" ", 1)[0] + "…"
        result.append({
            "position": position,
            "start_seconds": float(group[0].start_seconds),
            "end_seconds": float(group[-1].end_seconds),
            "title": title,
            "summary": summary,
        })
    return result


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
        try:
            import cv2

            capture = cv2.VideoCapture(str(path))
            try:
                if not capture.isOpened():
                    raise ValueError("Video metadata unavailable")
                frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
                fps = capture.get(cv2.CAP_PROP_FPS)
                return {
                    "duration": round(frame_count / fps, 2) if frame_count and fps else None,
                    "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or None,
                    "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None,
                }
            finally:
                capture.release()
        except (ImportError, ValueError):
            return {"duration": None, "width": None, "height": None}


def queue_analysis(connection: Any, video_id: str) -> None:
    now = int(time.time())
    connection.execute(
        """
        INSERT INTO analysis_jobs (id, video_id, status, progress, error, attempts, created_at, updated_at)
        VALUES (?, ?, 'queued', 0, NULL, 0, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET status = 'queued', progress = 0, error = NULL, updated_at = excluded.updated_at
        """,
        (uuid.uuid4().hex, video_id, now, now),
    )
    notify_jobs()


def queue_transcript(connection: Any, video_id: str) -> None:
    now = int(time.time())
    connection.execute(
        """
        INSERT INTO transcript_jobs (id, video_id, status, error, attempts, created_at, updated_at)
        VALUES (?, ?, 'queued', NULL, 0, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET status = 'queued', error = NULL, updated_at = excluded.updated_at
        """,
        (uuid.uuid4().hex, video_id, now, now),
    )
    notify_jobs()


def run_analysis_job(video_id: str) -> None:
    if video_id in cancelled_video_ids:
        cancelled_video_ids.discard(video_id)
        return
    now = int(time.time())
    with db() as connection:
        claimed = connection.execute(
            """
            UPDATE analysis_jobs
            SET status = 'processing', progress = 10, attempts = attempts + 1, updated_at = ?
            WHERE video_id = ? AND status = 'queued'
            """,
            (now, video_id),
        )
        video = connection.execute("SELECT path FROM videos WHERE id = ?", (video_id,)).fetchone()
    if claimed.rowcount == 0 or not video:
        return

    try:
        metadata = probe_video(Path(video["path"]))
        with db() as connection:
            connection.execute(
                "UPDATE videos SET duration = ?, width = ?, height = ? WHERE id = ?",
                (metadata["duration"], metadata["width"], metadata["height"], video_id),
            )
            connection.execute(
                "UPDATE analysis_jobs SET progress = 35, updated_at = ? WHERE video_id = ?",
                (int(time.time()), video_id),
            )
        extracted = extract_scene_thumbnails(Path(video["path"]), UPLOADS, video_id)
        if video_id in cancelled_video_ids:
            shutil.rmtree(UPLOADS / "derived" / video_id, ignore_errors=True)
            cancelled_video_ids.discard(video_id)
            return
        with db() as connection:
            if not connection.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone():
                shutil.rmtree(UPLOADS / "derived" / video_id, ignore_errors=True)
                return
            connection.execute("DELETE FROM scenes WHERE video_id = ?", (video_id,))
            created_at = int(time.time())
            connection.executemany(
                """
                INSERT INTO scenes
                  (id, video_id, position, start_seconds, end_seconds, label, thumbnail_path, detection_method, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        uuid.uuid4().hex,
                        video_id,
                        scene.position,
                        scene.start_seconds,
                        scene.end_seconds,
                        scene.label,
                        str(scene.thumbnail_path),
                        scene.detection_method,
                        created_at,
                    )
                    for scene in extracted
                ],
            )
            connection.execute(
                "UPDATE analysis_jobs SET status = 'ready', progress = 100, error = NULL, updated_at = ? WHERE video_id = ?",
                (created_at, video_id),
            )
    except AnalysisError as exc:
        with db() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET status = 'failed', progress = 0, error = ?, updated_at = ? WHERE video_id = ?",
                (str(exc), int(time.time()), video_id),
            )
    except Exception:
        with db() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET status = 'failed', progress = 0, error = ?, updated_at = ? WHERE video_id = ?",
                ("Timeline extraction failed unexpectedly", int(time.time()), video_id),
            )


def run_transcript_job(video_id: str) -> None:
    if video_id in cancelled_video_ids:
        return
    with db() as connection:
        claimed = connection.execute(
            """
            UPDATE transcript_jobs
            SET status = 'processing', attempts = attempts + 1, updated_at = ?
            WHERE video_id = ? AND status = 'queued'
            """,
            (int(time.time()), video_id),
        )
        video = connection.execute("SELECT path FROM videos WHERE id = ?", (video_id,)).fetchone()
    if claimed.rowcount == 0 or not video:
        return

    try:
        segments = transcribe_video(Path(video["path"]), UPLOADS, video_id)
        if video_id in cancelled_video_ids:
            return
        with db() as connection:
            if not connection.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone():
                return
            connection.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
            connection.execute("DELETE FROM transcript_chapters WHERE video_id = ?", (video_id,))
            created_at = int(time.time())
            connection.executemany(
                """
                INSERT INTO transcript_segments
                  (id, video_id, position, start_seconds, end_seconds, text, speaker, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (uuid.uuid4().hex, video_id, index, segment.start_seconds, segment.end_seconds, segment.text, getattr(segment, "speaker", None), created_at)
                    for index, segment in enumerate(segments)
                ],
            )
            connection.executemany(
                """
                INSERT INTO transcript_chapters
                  (id, video_id, position, start_seconds, end_seconds, title, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (uuid.uuid4().hex, video_id, chapter["position"], chapter["start_seconds"], chapter["end_seconds"], chapter["title"], chapter["summary"], created_at)
                    for chapter in build_chapters(segments)
                ],
            )
            status = "ready" if segments else "no_speech"
            connection.execute(
                "UPDATE transcript_jobs SET status = ?, error = NULL, updated_at = ? WHERE video_id = ?",
                (status, created_at, video_id),
            )
    except TranscriptionUnavailable as exc:
        status, detail = "not_configured", str(exc)
    except NoAudioTrack as exc:
        status, detail = "no_audio", str(exc)
    except TranscriptionError as exc:
        status, detail = "failed", str(exc)
    except Exception:
        status, detail = "failed", "Transcription failed unexpectedly"
    else:
        return
    with db() as connection:
        connection.execute(
            "UPDATE transcript_jobs SET status = ?, error = ?, updated_at = ? WHERE video_id = ?",
            (status, detail, int(time.time()), video_id),
        )


def run_video_jobs(video_id: str) -> None:
    run_analysis_job(video_id)
    run_transcript_job(video_id)


def import_error_is_retryable(message: str) -> bool:
    lowered = message.lower()
    permanent = ("private", "drm", "sign in", "login", "age-restricted", "not available", "unsupported", "limit")
    return not any(marker in lowered for marker in permanent)


def run_import_job(import_id: str) -> None:
    with db() as connection:
        job = connection.execute(
            "SELECT * FROM import_jobs WHERE id = ?",
            (import_id,),
        ).fetchone()
        if not job or job["status"] not in {"queued", "processing"}:
            return
        owner = connection.execute(
            "SELECT owner_account_id FROM workspaces WHERE id = ?",
            (job["workspace_id"],),
        ).fetchone()
        if not owner:
            raise RuntimeError("Import job workspace has no owner")
        job_workspace_id = job["workspace_id"]
        job_account_id = owner["owner_account_id"]
        if job["cancel_requested"]:
            connection.execute(
                "UPDATE import_jobs SET status = 'cancelled', detail = 'Import cancelled', updated_at = ? WHERE id = ?",
                (int(time.time()), import_id),
            )
            return
        connection.execute(
            "UPDATE import_jobs SET status = 'processing', progress = MAX(progress, 3), detail = 'Checking the link', attempts = attempts + 1, updated_at = ? WHERE id = ?",
            (int(time.time()), import_id),
        )

    last_progress = 3

    def cancelled() -> bool:
        if import_id in cancelled_import_ids:
            return True
        with db() as connection:
            record = connection.execute(
                "SELECT cancel_requested FROM import_jobs WHERE id = ?", (import_id,)
            ).fetchone()
        return not record or bool(record["cancel_requested"])

    def update_progress(progress: int, **metrics) -> None:
        nonlocal last_progress
        progress = max(last_progress, min(95, int(progress)))
        if progress <= last_progress:
            return
        last_progress = progress
        detail = metrics.get("stage") or ("Downloading video" if progress < 90 else "Checking playback")
        with db() as connection:
            connection.execute(
                """
                UPDATE import_jobs
                SET progress = ?, detail = ?, downloaded_bytes = COALESCE(?, downloaded_bytes),
                    total_bytes = COALESCE(?, total_bytes), speed_bps = COALESCE(?, speed_bps),
                    eta_seconds = COALESCE(?, eta_seconds), updated_at = ?
                WHERE id = ?
                """,
                (
                    progress, detail, metrics.get("downloaded_bytes"), metrics.get("total_bytes"),
                    metrics.get("speed_bps"), metrics.get("eta_seconds"), int(time.time()), import_id,
                ),
            )

    try:
        media = None
        last_error: RemoteImportError | None = None
        for retry in range(IMPORT_AUTO_RETRIES + 1):
            try:
                media = import_remote_video(
                    job["source_url"], UPLOADS, import_id, MAX_UPLOAD_BYTES,
                    MAX_REMOTE_DURATION_SECONDS, REMOTE_IMPORT_TIMEOUT_SECONDS,
                    update_progress, cancelled,
                )
                break
            except RemoteImportCancelled:
                raise
            except RemoteImportError as exc:
                last_error = exc
                if retry >= IMPORT_AUTO_RETRIES or not import_error_is_retryable(str(exc)):
                    raise
                with db() as connection:
                    connection.execute(
                        "UPDATE import_jobs SET detail = ?, updated_at = ? WHERE id = ?",
                        (f"Platform unavailable — retrying automatically ({retry + 1}/{IMPORT_AUTO_RETRIES})", int(time.time()), import_id),
                    )
                time.sleep(min(2 ** retry, 4))
        if media is None:
            raise last_error or RemoteImportError("The online video could not be imported")
        if cancelled():
            media.path.unlink(missing_ok=True)
            raise RemoteImportCancelled("The online video import was cancelled")
        metadata = probe_video(media.path)
        if not metadata["duration"] or not metadata["width"] or not metadata["height"]:
            media.path.unlink(missing_ok=True)
            raise RemoteImportError("The downloaded file could not be decoded as a video")
        video_id = uuid.uuid4().hex
        size = media.path.stat().st_size
        created_at = int(time.time())
        storage_key = storage().sync_video(media.path, job_account_id, video_id, media.mime_type)
        with db() as connection:
            connection.execute(
                """
                INSERT INTO videos
                  (id, workspace_id, name, path, storage_key, mime_type, size_bytes, duration, width, height, status, created_at, source_type, source_url, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, 'url', ?, ?)
                """,
                (
                    video_id,
                    job_workspace_id,
                    media.title,
                    str(media.path),
                    storage_key,
                    media.mime_type,
                    size,
                    metadata["duration"],
                    metadata["width"],
                    metadata["height"],
                    created_at,
                    media.source_url,
                    default_expiry(created_at),
                ),
            )
            queue_analysis(connection, video_id)
            queue_transcript(connection, video_id)
            connection.execute(
                """
                UPDATE import_jobs
                SET status = 'ready', progress = 100, detail = 'Video ready', video_id = ?, error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (video_id, int(time.time()), import_id),
            )
        run_video_jobs(video_id)
        emit_webhook_event(job_workspace_id, "video.ready", {"video_id": video_id, "source_type": "url"})
    except RemoteImportCancelled:
        for partial in UPLOADS.glob(f"{import_id}.*"):
            partial.unlink(missing_ok=True)
        with db() as connection:
            connection.execute(
                "UPDATE import_jobs SET status = 'cancelled', detail = 'Import cancelled', error = NULL, updated_at = ? WHERE id = ?",
                (int(time.time()), import_id),
            )
    except RemoteImportError as exc:
        with db() as connection:
            connection.execute(
                "UPDATE import_jobs SET status = 'failed', detail = 'Import stopped', error = ?, updated_at = ? WHERE id = ?",
                (str(exc), int(time.time()), import_id),
            )
        emit_webhook_event(job_workspace_id, "import.failed", {"import_id": import_id, "error": str(exc)})
    except Exception:
        logger.exception("Background import %s failed", import_id)
        with db() as connection:
            connection.execute(
                "UPDATE import_jobs SET status = 'failed', detail = 'Import stopped', error = 'The online video could not be imported', updated_at = ? WHERE id = ?",
                (int(time.time()), import_id),
            )
        emit_webhook_event(job_workspace_id, "import.failed", {"import_id": import_id, "error": "The online video could not be imported"})
    finally:
        cancelled_import_ids.discard(import_id)


def run_pending_import_jobs() -> None:
    with db() as connection:
        pending = connection.execute("SELECT id FROM import_jobs WHERE status = 'queued' ORDER BY created_at").fetchall()
    for job in pending:
        run_import_job(job["id"])


def run_pending_analysis_jobs() -> None:
    with db() as connection:
        missing_metadata = connection.execute(
            "SELECT id, path FROM videos WHERE source_type != 'demo' AND (duration IS NULL OR width IS NULL OR height IS NULL)"
        ).fetchall()
    for video in missing_metadata:
        metadata = probe_video(Path(video["path"]))
        with db() as connection:
            connection.execute(
                "UPDATE videos SET duration = ?, width = ?, height = ? WHERE id = ?",
                (metadata["duration"], metadata["width"], metadata["height"], video["id"]),
            )
    with db() as connection:
        pending = connection.execute("SELECT video_id FROM analysis_jobs WHERE status = 'queued' ORDER BY created_at").fetchall()
    for job in pending:
        run_analysis_job(job["video_id"])
    with db() as connection:
        pending_transcripts = connection.execute(
            "SELECT video_id FROM transcript_jobs WHERE status = 'queued' ORDER BY created_at"
        ).fetchall()
    for job in pending_transcripts:
        run_transcript_job(job["video_id"])


def video_record(video_id: str) -> Any:
    with db() as connection:
        record = connection.execute(
            "SELECT * FROM videos WHERE id = ? AND workspace_id = ?",
            (video_id, active_workspace_id()),
        ).fetchone()
    if not record:
        raise HTTPException(status_code=404, detail="Video not found")
    return record


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": inference_mode(),
        "database": "postgresql" if using_postgres() else "sqlite",
    }


@app.get("/api/ready")
def readiness() -> dict[str, str]:
    try:
        with db() as connection:
            connection.execute("SELECT 1").fetchone()
        storage().ensure_ready()
        if os.getenv("REDIS_URL", "").strip() and redis_client() is None:
            redis_client.cache_clear()
            if redis_client() is None:
                raise RuntimeError("Redis is unavailable")
    except Exception as exc:
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="A required service is unavailable") from exc
    return {"status": "ready", "database": "postgresql" if using_postgres() else "sqlite"}


@app.get("/api/metrics")
def metrics() -> dict:
    total = request_metrics["requests"]
    with db() as connection:
        queues = {
            "imports": connection.execute("SELECT COUNT(*) AS count FROM import_jobs WHERE status IN ('queued','processing')").fetchone()["count"],
            "analysis": connection.execute("SELECT COUNT(*) AS count FROM analysis_jobs WHERE status IN ('queued','processing')").fetchone()["count"],
            "transcripts": connection.execute("SELECT COUNT(*) AS count FROM transcript_jobs WHERE status IN ('queued','processing')").fetchone()["count"],
        }
    return {
        "requests": total,
        "average_request_ms": round(request_duration_ms / total, 2) if total else 0,
        "responses": {key.removeprefix("status_"): value for key, value in request_metrics.items() if key.startswith("status_")},
        "queues": queues,
    }


@app.get("/api/config")
def public_config() -> dict:
    transcription_provider = os.getenv("TRANSCRIPTION_PROVIDER", "local").strip().lower()
    local_transcription_available = (
        importlib.util.find_spec("faster_whisper") is not None
        and importlib.util.find_spec("pyannote") is not None
    )
    transcription_available = (
        local_transcription_available
        if transcription_provider == "local"
        else bool(os.getenv("OPENAI_API_KEY"))
    )
    return {
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "allowed_mime_types": sorted(ALLOWED_MIME),
        "chat_requests_per_hour": CHAT_REQUESTS_PER_HOUR,
        "uploads_per_hour": UPLOADS_PER_HOUR,
        "inference_mode": inference_mode(),
        "max_remote_duration_seconds": MAX_REMOTE_DURATION_SECONDS,
        "remote_import_timeout_seconds": REMOTE_IMPORT_TIMEOUT_SECONDS,
        "remote_platforms": sorted(platform_hosts()),
        "transcription_provider": transcription_provider,
        "transcription_available": transcription_available,
        "speaker_diarization_enabled": transcription_provider == "local" and os.getenv("LOCAL_SPEAKER_DIARIZATION", "true").lower() not in {"0", "false", "off"},
        "database_backend": "postgresql" if using_postgres() else "sqlite",
        "workspace": {
            "id": active_workspace_id(),
            "name": LOCAL_WORKSPACE_NAME,
            "mode": "supabase" if using_postgres() else "local-development",
        },
    }


@app.get("/api/workspace")
def current_workspace() -> dict:
    with db() as connection:
        workspace = connection.execute(
            """
            SELECT workspaces.id, workspaces.name, workspace_members.role,
                   COUNT(DISTINCT videos.id) AS video_count
            FROM workspaces
            JOIN workspace_members
              ON workspace_members.workspace_id = workspaces.id
             AND workspace_members.account_id = ?
            LEFT JOIN videos ON videos.workspace_id = workspaces.id
            WHERE workspaces.id = ?
            GROUP BY workspaces.id, workspace_members.role
            """,
            (active_account_id(), active_workspace_id()),
        ).fetchone()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {**dict(workspace), "mode": "supabase" if using_postgres() else "local-development"}


@app.get("/api/workspace/invitations")
def list_workspace_invitations() -> list[dict]:
    require_workspace_role("owner")
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id, email, role, created_at, expires_at, accepted_at
            FROM workspace_invitations
            WHERE workspace_id = ? ORDER BY created_at DESC
            """,
            (active_workspace_id(),),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/workspace/invitations", status_code=201)
def create_workspace_invitation(payload: InvitationCreateRequest) -> dict:
    require_workspace_role("owner")
    raw_token = "uvi_" + secrets.token_urlsafe(32)
    now = int(time.time())
    invitation_id = uuid.uuid4().hex
    with db() as connection:
        connection.execute(
            """
            INSERT INTO workspace_invitations
              (id, workspace_id, email, role, token_hash, invited_by, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invitation_id,
                active_workspace_id(),
                payload.email,
                payload.role,
                hashlib.sha256(raw_token.encode()).hexdigest(),
                active_account_id(),
                now,
                now + 7 * 86400,
            ),
        )
    return {
        "id": invitation_id,
        "email": payload.email,
        "role": payload.role,
        "expires_at": now + 7 * 86400,
        "token": raw_token,
        "notice": "Share this invitation token securely. It will not be shown again.",
    }


@app.post("/api/workspace/invitations/accept")
def accept_workspace_invitation(payload: InvitationAcceptRequest) -> dict:
    identity = request_identity.get()
    if not identity or not identity.email:
        raise HTTPException(status_code=401, detail="Sign in with the invited email address")
    now = int(time.time())
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    with db() as connection:
        invitation = connection.execute(
            """
            SELECT id, workspace_id, email, role, expires_at, accepted_at
            FROM workspace_invitations WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if not invitation or invitation["accepted_at"] is not None or invitation["expires_at"] <= now:
            raise HTTPException(status_code=404, detail="Invitation is invalid or expired")
        if invitation["email"].lower() != identity.email.lower():
            raise HTTPException(status_code=403, detail="This invitation belongs to a different email address")
        connection.execute(
            "INSERT OR IGNORE INTO workspace_members (workspace_id, account_id, role, created_at) VALUES (?, ?, ?, ?)",
            (invitation["workspace_id"], identity.account_id, invitation["role"], now),
        )
        connection.execute(
            "UPDATE workspace_invitations SET accepted_at = ? WHERE id = ?",
            (now, invitation["id"]),
        )
    return {"workspace_id": invitation["workspace_id"], "role": invitation["role"]}


@app.get("/api/developer/api-keys")
def list_api_keys() -> list[dict]:
    require_workspace_role("owner")
    with db() as connection:
        rows = connection.execute(
            "SELECT id, name, key_prefix, created_at, last_used_at, revoked_at FROM api_keys WHERE workspace_id = ? ORDER BY created_at DESC",
            (active_workspace_id(),),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/developer/api-keys", status_code=201)
def create_api_key(payload: ApiKeyCreateRequest) -> dict:
    require_workspace_role("owner")
    raw_key = "uv_" + secrets.token_urlsafe(32)
    record = {
        "id": uuid.uuid4().hex,
        "name": payload.name.strip(),
        "key_prefix": raw_key[:11],
        "created_at": int(time.time()),
    }
    with db() as connection:
        connection.execute(
            "INSERT INTO api_keys (id, workspace_id, name, key_prefix, key_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (record["id"], active_workspace_id(), record["name"], record["key_prefix"], hashlib.sha256(raw_key.encode()).hexdigest(), record["created_at"]),
        )
    return {**record, "key": raw_key, "notice": "Copy this key now. It will not be shown again."}


@app.delete("/api/developer/api-keys/{key_id}", status_code=204)
def revoke_api_key(key_id: str) -> Response:
    require_workspace_role("owner")
    with db() as connection:
        changed = connection.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL",
            (int(time.time()), key_id, active_workspace_id()),
        )
    if changed.rowcount == 0:
        raise HTTPException(status_code=404, detail="Active API key not found")
    return Response(status_code=204)


@app.get("/api/developer/webhooks")
def list_webhooks() -> list[dict]:
    require_workspace_role("owner")
    with db() as connection:
        rows = connection.execute(
            "SELECT id, url, events, created_at, disabled_at FROM webhook_subscriptions WHERE workspace_id = ? ORDER BY created_at DESC",
            (active_workspace_id(),),
        ).fetchall()
    return [{**dict(row), "events": json.loads(row["events"])} for row in rows]


@app.post("/api/developer/webhooks", status_code=201)
def create_webhook(payload: WebhookCreateRequest) -> dict:
    require_workspace_role("owner")
    url = str(payload.url)
    validate_public_https_url(url)
    webhook_id = uuid.uuid4().hex
    signing_secret = "whsec_" + secrets.token_urlsafe(32)
    created_at = int(time.time())
    with db() as connection:
        connection.execute(
            "INSERT INTO webhook_subscriptions (id, workspace_id, url, events, signing_secret, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (webhook_id, active_workspace_id(), url, json.dumps(payload.events), signing_secret, created_at),
        )
    return {"id": webhook_id, "url": url, "events": payload.events, "created_at": created_at, "signing_secret": signing_secret, "notice": "Copy the signing secret now. It will not be shown again."}


def emit_webhook_event(workspace_id: str, event: str, data: dict) -> int:
    with db() as connection:
        hooks = connection.execute(
            "SELECT id, url, events, signing_secret FROM webhook_subscriptions WHERE workspace_id = ? AND disabled_at IS NULL",
            (workspace_id,),
        ).fetchall()
    delivered = 0
    for hook in hooks:
        if event not in json.loads(hook["events"]):
            continue
        try:
            validate_public_https_url(hook["url"])
            body = json.dumps(
                {"id": uuid.uuid4().hex, "event": event, "created_at": int(time.time()), "data": data},
                separators=(",", ":"),
            ).encode()
            signature = hmac.new(hook["signing_secret"].encode(), body, hashlib.sha256).hexdigest()
            request = urllib.request.Request(
                hook["url"], data=body, method="POST",
                headers={"Content-Type": "application/json", "X-UnderstandVideo-Signature": f"sha256={signature}"},
            )
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        if 200 <= response.status < 300:
                            delivered += 1
                            break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(0.25 * (2 ** attempt))
        except Exception:
            logger.exception("Webhook %s delivery failed for %s", hook["id"], event)
    return delivered


@app.post("/api/developer/webhooks/{webhook_id}/test")
def test_webhook(webhook_id: str) -> dict:
    require_workspace_role("owner")
    with db() as connection:
        hook = connection.execute(
            "SELECT url, signing_secret FROM webhook_subscriptions WHERE id = ? AND workspace_id = ? AND disabled_at IS NULL",
            (webhook_id, active_workspace_id()),
        ).fetchone()
    if not hook:
        raise HTTPException(status_code=404, detail="Active webhook not found")
    validate_public_https_url(hook["url"])
    body = json.dumps({"id": uuid.uuid4().hex, "event": "webhook.test", "created_at": int(time.time())}).encode()
    signature = hmac.new(hook["signing_secret"].encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(hook["url"], data=body, method="POST", headers={"Content-Type": "application/json", "X-UnderstandVideo-Signature": f"sha256={signature}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The webhook endpoint did not accept the test event") from exc
    return {"delivered": 200 <= status < 300, "status_code": status}


@app.delete("/api/developer/webhooks/{webhook_id}", status_code=204)
def disable_webhook(webhook_id: str) -> Response:
    require_workspace_role("owner")
    with db() as connection:
        changed = connection.execute(
            "UPDATE webhook_subscriptions SET disabled_at = ? WHERE id = ? AND workspace_id = ? AND disabled_at IS NULL",
            (int(time.time()), webhook_id, active_workspace_id()),
        )
    if changed.rowcount == 0:
        raise HTTPException(status_code=404, detail="Active webhook not found")
    return Response(status_code=204)


@app.post("/api/videos/upload", status_code=201)
async def upload_video(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> dict:
    enforce_rate_limit("upload", limit=UPLOADS_PER_HOUR)
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="Unsupported video format")

    video_id = uuid.uuid4().hex
    target = storage().upload_path(video_id, safe_name(file.filename or "video.mp4"))
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    limit_mb = MAX_UPLOAD_BYTES / (1024 * 1024)
                    raise HTTPException(status_code=413, detail=f"Video exceeds the {limit_mb:g} MB limit")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    try:
        mime, metadata = validated_video_metadata(target, mime)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    source_type = "demo" if is_demo_video(target) else "upload"
    created_at = int(time.time())
    try:
        storage_key = None if source_type == "demo" else storage().sync_video(target, active_account_id(), video_id, mime)
    except StorageError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    with db() as connection:
        connection.execute(
            """
            INSERT INTO videos
              (id, workspace_id, name, path, storage_key, mime_type, size_bytes, duration, width, height, status, created_at, source_type, source_url, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (video_id, active_workspace_id(), file.filename or target.name, str(target), storage_key, mime, size, metadata["duration"], metadata["width"], metadata["height"], "ready", created_at, source_type, None, None if source_type == "demo" else default_expiry(created_at)),
        )
        if source_type != "demo":
            queue_analysis(connection, video_id)
            queue_transcript(connection, video_id)
    if source_type != "demo":
        background_tasks.add_task(run_video_jobs, video_id)
        background_tasks.add_task(emit_webhook_event, active_workspace_id(), "video.ready", {"video_id": video_id, "source_type": source_type})
    return {
        "id": video_id,
        "name": file.filename,
        "size_bytes": size,
        "status": "ready",
        "source_type": source_type,
        "source_url": None,
        "analysis_status": "ready" if source_type == "demo" else "queued",
        "analysis_progress": 100 if source_type == "demo" else 0,
        **metadata,
    }


@app.post("/api/videos/import-url", status_code=201)
async def import_video_url(request: Request, background_tasks: BackgroundTasks, payload: UrlImportRequest) -> dict:
    enforce_rate_limit("upload", limit=UPLOADS_PER_HOUR)
    video_id = uuid.uuid4().hex
    try:
        media = await asyncio.wait_for(
            asyncio.to_thread(
                import_remote_video,
                str(payload.url),
                UPLOADS,
                video_id,
                MAX_UPLOAD_BYTES,
                MAX_REMOTE_DURATION_SECONDS,
                REMOTE_IMPORT_TIMEOUT_SECONDS,
            ),
            timeout=REMOTE_IMPORT_TIMEOUT_SECONDS + 5,
        )
    except asyncio.TimeoutError as exc:
        for partial in UPLOADS.glob(f"{video_id}.*"):
            partial.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="The online video import timed out") from exc
    except RemoteImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    metadata = probe_video(media.path)
    size = media.path.stat().st_size
    created_at = int(time.time())
    try:
        storage_key = storage().sync_video(media.path, active_account_id(), video_id, media.mime_type)
    except StorageError as exc:
        media.path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    with db() as connection:
        connection.execute(
            """
            INSERT INTO videos
              (id, workspace_id, name, path, storage_key, mime_type, size_bytes, duration, width, height, status, created_at, source_type, source_url, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'url', ?, ?)
            """,
            (video_id, active_workspace_id(), media.title, str(media.path), storage_key, media.mime_type, size, metadata["duration"], metadata["width"], metadata["height"], "ready", created_at, media.source_url, default_expiry(created_at)),
        )
        queue_analysis(connection, video_id)
        queue_transcript(connection, video_id)
    background_tasks.add_task(run_video_jobs, video_id)
    background_tasks.add_task(emit_webhook_event, active_workspace_id(), "video.ready", {"video_id": video_id, "source_type": "url"})
    return {
        "id": video_id,
        "name": media.title,
        "size_bytes": size,
        "status": "ready",
        "source_type": "url",
        "source_url": media.source_url,
        "content_url": f"/api/videos/{video_id}/content",
        "analysis_status": "queued",
        "analysis_progress": 0,
        **metadata,
    }


@app.post("/api/imports", status_code=202)
def create_import_job(request: Request, background_tasks: BackgroundTasks, payload: UrlImportRequest) -> dict:
    enforce_rate_limit("upload", limit=UPLOADS_PER_HOUR)
    try:
        source = remote_source_details(str(payload.url))
    except RemoteImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    import_id = uuid.uuid4().hex
    now = int(time.time())
    with db() as connection:
        connection.execute(
            """
            INSERT INTO import_jobs
              (id, workspace_id, source_url, status, progress, detail, video_id, error, cancel_requested, platform, created_at, updated_at)
            VALUES (?, ?, ?, 'queued', 0, 'Waiting to start', NULL, NULL, 0, ?, ?, ?)
            """,
            (import_id, active_workspace_id(), str(payload.url), source["platform"], now, now),
        )
    notify_jobs()
    background_tasks.add_task(run_import_job, import_id)
    return {"id": import_id, "status": "queued", "progress": 0, "detail": "Waiting to start", **source}


@app.post("/api/imports/check")
def check_import_link(payload: UrlImportRequest) -> dict:
    try:
        return {"supported": True, **remote_source_details(str(payload.url))}
    except RemoteImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/imports")
def list_import_jobs(active: bool = False, limit: int = Query(default=10, ge=1, le=50)) -> list[dict]:
    with db() as connection:
        if active:
            records = connection.execute(
                """
                SELECT id, source_url, status, progress, detail, video_id, error, platform,
                       downloaded_bytes, total_bytes, speed_bps, eta_seconds, attempts, created_at, updated_at
                FROM import_jobs
                WHERE workspace_id = ? AND status IN ('queued', 'processing')
                ORDER BY created_at DESC LIMIT ?
                """,
                (active_workspace_id(), limit),
            ).fetchall()
        else:
            records = connection.execute(
                """
                SELECT id, source_url, status, progress, detail, video_id, error, platform,
                       downloaded_bytes, total_bytes, speed_bps, eta_seconds, attempts, created_at, updated_at
                FROM import_jobs WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (active_workspace_id(), limit),
            ).fetchall()
    return [dict(record) for record in records]


@app.get("/api/imports/{import_id}")
def import_job(import_id: str) -> dict:
    with db() as connection:
        job = connection.execute(
            """
            SELECT id, source_url, status, progress, detail, video_id, error, platform,
                   downloaded_bytes, total_bytes, speed_bps, eta_seconds, attempts, created_at, updated_at
            FROM import_jobs WHERE id = ? AND workspace_id = ?
            """,
            (import_id, active_workspace_id()),
        ).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Import not found")
        result = dict(job)
        if job["video_id"]:
            video = connection.execute(
                """
                SELECT videos.id, videos.name, videos.size_bytes, videos.duration, videos.width, videos.height,
                       videos.status, videos.created_at, videos.source_type, videos.source_url,
                       COALESCE(analysis_jobs.status, 'queued') AS analysis_status,
                       COALESCE(analysis_jobs.progress, 0) AS analysis_progress
                FROM videos LEFT JOIN analysis_jobs ON analysis_jobs.video_id = videos.id
                WHERE videos.id = ? AND videos.workspace_id = ?
                """,
                (job["video_id"], active_workspace_id()),
            ).fetchone()
            result["video"] = dict(video) if video else None
    return result


@app.post("/api/imports/{import_id}/cancel", status_code=202)
def cancel_import_job(import_id: str) -> dict:
    with db() as connection:
        job = connection.execute(
            "SELECT status FROM import_jobs WHERE id = ? AND workspace_id = ?",
            (import_id, active_workspace_id()),
        ).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Import not found")
        if job["status"] in {"ready", "failed", "cancelled"}:
            return {"id": import_id, "status": job["status"]}
        connection.execute(
            "UPDATE import_jobs SET cancel_requested = 1, detail = 'Cancelling import', updated_at = ? WHERE id = ?",
            (int(time.time()), import_id),
        )
    cancelled_import_ids.add(import_id)
    return {"id": import_id, "status": "cancelling"}


@app.post("/api/imports/{import_id}/retry", status_code=202)
def retry_import_job(import_id: str, background_tasks: BackgroundTasks) -> dict:
    with db() as connection:
        job = connection.execute(
            "SELECT status FROM import_jobs WHERE id = ? AND workspace_id = ?",
            (import_id, active_workspace_id()),
        ).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Import not found")
        if job["status"] not in {"failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Only stopped imports can be retried")
        connection.execute(
            """
            UPDATE import_jobs
            SET status = 'queued', progress = 0, detail = 'Waiting to retry', video_id = NULL,
                error = NULL, cancel_requested = 0, downloaded_bytes = NULL, total_bytes = NULL,
                speed_bps = NULL, eta_seconds = NULL, updated_at = ?
            WHERE id = ?
            """,
            (int(time.time()), import_id),
        )
    cancelled_import_ids.discard(import_id)
    notify_jobs()
    background_tasks.add_task(run_import_job, import_id)
    return {"id": import_id, "status": "queued", "progress": 0, "detail": "Waiting to retry"}


@app.post("/api/videos/{video_id}/reimport", status_code=202)
async def reimport_video_url(video_id: str, background_tasks: BackgroundTasks) -> dict:
    video = video_record(video_id)
    if video["source_type"] != "url" or not video["source_url"]:
        raise HTTPException(status_code=409, detail="Only videos added from a public link can be repaired")

    repair_id = f"{video_id}-repair-{uuid.uuid4().hex[:8]}"
    cancelled_video_ids.add(video_id)
    try:
        media = await asyncio.wait_for(
            asyncio.to_thread(
                import_remote_video,
                video["source_url"],
                UPLOADS,
                repair_id,
                MAX_UPLOAD_BYTES,
                MAX_REMOTE_DURATION_SECONDS,
                REMOTE_IMPORT_TIMEOUT_SECONDS,
            ),
            timeout=REMOTE_IMPORT_TIMEOUT_SECONDS + 5,
        )
    except asyncio.TimeoutError as exc:
        for partial in UPLOADS.glob(f"{repair_id}.*"):
            partial.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="The online video import timed out") from exc
    except RemoteImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        cancelled_video_ids.discard(video_id)

    metadata = probe_video(media.path)
    if not metadata["duration"]:
        media.path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="The repaired download still did not contain a playable video")

    old_path = Path(video["path"])
    size = media.path.stat().st_size
    try:
        storage_key = storage().sync_video(media.path, active_account_id(), video_id, media.mime_type)
    except StorageError as exc:
        media.path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    with db() as connection:
        connection.execute("DELETE FROM scenes WHERE video_id = ?", (video_id,))
        connection.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
        connection.execute(
            """
            UPDATE videos
            SET name = ?, path = ?, storage_key = ?, mime_type = ?, size_bytes = ?, duration = ?, width = ?, height = ?, status = 'ready'
            WHERE id = ? AND workspace_id = ?
            """,
            (
                media.title,
                str(media.path),
                storage_key,
                media.mime_type,
                size,
                metadata["duration"],
                metadata["width"],
                metadata["height"],
                video_id,
                active_workspace_id(),
            ),
        )
        queue_analysis(connection, video_id)
        queue_transcript(connection, video_id)
    try:
        storage().delete_video(old_path, video_id, video["storage_key"])
    except StorageError:
        logger.warning("Could not remove the replaced media for video %s", video_id)
    for stale_download in UPLOADS.glob(f"{video_id}.*"):
        stale_download.unlink(missing_ok=True)

    background_tasks.add_task(run_video_jobs, video_id)
    return {
        "id": video_id,
        "name": media.title,
        "size_bytes": size,
        "status": "ready",
        "source_type": "url",
        "source_url": media.source_url,
        "content_url": f"/api/videos/{video_id}/content",
        "analysis_status": "queued",
        "analysis_progress": 0,
        **metadata,
    }


@app.get("/api/videos")
def list_videos(q: str = Query(default="", max_length=200)) -> list[dict]:
    normalized_query = q.strip().lower()
    escaped_query = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped_query}%"
    with db() as connection:
        records = connection.execute(
            """
            SELECT
              videos.id, videos.name, videos.size_bytes, videos.duration, videos.width, videos.height,
              videos.status, videos.created_at, videos.source_type, videos.source_url,
              COALESCE(analysis_jobs.status, CASE WHEN videos.source_type = 'demo' THEN 'ready' ELSE 'queued' END) AS analysis_status,
              COALESCE(analysis_jobs.progress, CASE WHEN videos.source_type = 'demo' THEN 100 ELSE 0 END) AS analysis_progress,
              (SELECT GROUP_CONCAT(tag, '|||') FROM video_tags WHERE video_tags.video_id = videos.id) AS tag_list,
              (SELECT id FROM scenes WHERE scenes.video_id = videos.id ORDER BY position LIMIT 1) AS poster_scene_id
            FROM videos
            LEFT JOIN analysis_jobs ON analysis_jobs.video_id = videos.id
            WHERE videos.workspace_id = ?
              AND (
                ? = ''
                OR LOWER(videos.name) LIKE ? ESCAPE '\\'
                OR LOWER(COALESCE(videos.source_url, '')) LIKE ? ESCAPE '\\'
                OR EXISTS (
                  SELECT 1 FROM video_tags
                  WHERE video_tags.video_id = videos.id
                    AND LOWER(video_tags.tag) LIKE ? ESCAPE '\\'
                )
                OR EXISTS (
                  SELECT 1 FROM transcript_segments
                  WHERE transcript_segments.video_id = videos.id
                    AND LOWER(transcript_segments.text) LIKE ? ESCAPE '\\'
                )
                OR EXISTS (
                  SELECT 1 FROM annotations
                  WHERE annotations.video_id = videos.id
                    AND annotations.workspace_id = videos.workspace_id
                    AND LOWER(annotations.note) LIKE ? ESCAPE '\\'
                )
                OR EXISTS (
                  SELECT 1 FROM messages
                  WHERE messages.video_id = videos.id
                    AND LOWER(messages.content) LIKE ? ESCAPE '\\'
                )
              )
            ORDER BY videos.created_at DESC
            """,
            (active_workspace_id(), normalized_query, pattern, pattern, pattern, pattern, pattern, pattern),
        ).fetchall()
    videos = []
    for record in records:
        item = dict(record)
        scene_id = item.pop("poster_scene_id")
        tag_list = item.pop("tag_list", None)
        item["tags"] = tag_list.split("|||") if tag_list else []
        item["poster_url"] = f"/api/videos/{item['id']}/scenes/{scene_id}/thumbnail" if scene_id else None
        videos.append(item)
    return videos


@app.patch("/api/videos/{video_id}")
def rename_video(video_id: str, payload: RenameVideoRequest) -> dict:
    video_record(video_id)
    with db() as connection:
        connection.execute(
            "UPDATE videos SET name = ? WHERE id = ? AND workspace_id = ?",
            (payload.name, video_id, active_workspace_id()),
        )
    return {"id": video_id, "name": payload.name}


@app.patch("/api/videos/{video_id}/retention")
def set_video_retention(video_id: str, payload: RetentionRequest) -> dict:
    video_record(video_id)
    expires_at = int(time.time()) + payload.days * 86400 if payload.days else None
    with db() as connection:
        connection.execute(
            "UPDATE videos SET expires_at = ? WHERE id = ? AND workspace_id = ?",
            (expires_at, video_id, active_workspace_id()),
        )
    return {"video_id": video_id, "retention_days": payload.days, "expires_at": expires_at}


@app.get("/api/videos/{video_id}/review")
def get_video_review(video_id: str) -> dict:
    video_record(video_id)
    with db() as connection:
        review = connection.execute(
            "SELECT state, assignee, updated_by, updated_at FROM video_reviews WHERE video_id = ?",
            (video_id,),
        ).fetchone()
    return dict(review) if review else {"state": "draft", "assignee": None, "updated_by": active_account_id(), "updated_at": None}


@app.put("/api/videos/{video_id}/review")
def update_video_review(video_id: str, payload: ReviewUpdateRequest, background_tasks: BackgroundTasks) -> dict:
    video_record(video_id)
    assignee = payload.assignee.strip() if payload.assignee else None
    now = int(time.time())
    with db() as connection:
        connection.execute(
            """
            INSERT INTO video_reviews (video_id, state, assignee, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET state = excluded.state, assignee = excluded.assignee,
              updated_by = excluded.updated_by, updated_at = excluded.updated_at
            """,
            (video_id, payload.state, assignee, active_account_id(), now),
        )
    result = {"state": payload.state, "assignee": assignee, "updated_by": active_account_id(), "updated_at": now}
    background_tasks.add_task(emit_webhook_event, active_workspace_id(), "review.updated", {"video_id": video_id, **result})
    return result


@app.get("/api/videos/{video_id}/comments")
def list_video_comments(video_id: str) -> list[dict]:
    video_record(video_id)
    with db() as connection:
        rows = connection.execute(
            "SELECT id, account_id, body, timestamp, created_at FROM video_comments WHERE video_id = ? ORDER BY created_at",
            (video_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/videos/{video_id}/comments", status_code=201)
def create_video_comment(video_id: str, payload: CommentCreateRequest) -> dict:
    video = video_record(video_id)
    comment_id = uuid.uuid4().hex
    timestamp = min(payload.timestamp, float(video["duration"] or payload.timestamp)) if payload.timestamp is not None else None
    now = int(time.time() * 1000)
    with db() as connection:
        connection.execute(
            "INSERT INTO video_comments (id, video_id, account_id, body, timestamp, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (comment_id, video_id, active_account_id(), payload.body, timestamp, now),
        )
    return {"id": comment_id, "account_id": active_account_id(), "body": payload.body, "timestamp": timestamp, "created_at": now}


@app.delete("/api/videos/{video_id}", status_code=204)
def delete_video(video_id: str) -> Response:
    video = video_record(video_id)
    cancelled_video_ids.add(video_id)
    media_path = Path(video["path"])
    with db() as connection:
        connection.execute("DELETE FROM messages WHERE video_id = ?", (video_id,))
        connection.execute("DELETE FROM conversations WHERE video_id = ?", (video_id,))
        connection.execute("DELETE FROM scenes WHERE video_id = ?", (video_id,))
        connection.execute("DELETE FROM analysis_jobs WHERE video_id = ?", (video_id,))
        connection.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
        connection.execute("DELETE FROM transcript_jobs WHERE video_id = ?", (video_id,))
        connection.execute("DELETE FROM videos WHERE id = ?", (video_id,))

    try:
        storage().delete_video(media_path, video_id, video["storage_key"])
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(status_code=204)


@app.get("/api/videos/{video_id}/content")
def video_content(video_id: str) -> FileResponse:
    video = video_record(video_id)
    path = Path(video["path"])
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Video file is no longer available")
    return FileResponse(path, media_type=video["mime_type"], headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/videos/{video_id}/playback")
def video_playback(video_id: str) -> dict:
    video = video_record(video_id)
    if video["storage_key"]:
        try:
            url = storage().signed_url(video["storage_key"], expires_in=300)
        except StorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if url:
            return {"url": url, "expires_in": 300, "source": "private-object-storage"}
    return {"url": f"/api/videos/{video_id}/content", "expires_in": 300, "source": "local-private-media"}


@app.get("/api/videos/{video_id}/clip")
def video_clip(
    video_id: str,
    start: float = Query(default=0, ge=0),
    duration: float = Query(default=30, ge=1, le=120),
) -> FileResponse:
    video = video_record(video_id)
    video_duration = float(video["duration"] or 0)
    if video_duration and start >= video_duration:
        raise HTTPException(status_code=422, detail="Clip start is outside the video")
    duration = min(duration, max(1, video_duration - start)) if video_duration else duration
    clip_dir = UPLOADS / "derived" / video_id / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    target = clip_dir / f"{int(start * 1000)}-{int(duration * 1000)}.mp4"
    if not target.is_file():
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run(
                [ffmpeg, "-y", "-ss", str(start), "-i", video["path"], "-t", str(duration),
                 "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(target)],
                capture_output=True, timeout=180, check=True,
            )
        except (ImportError, OSError, subprocess.SubprocessError) as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail="The shareable clip could not be created") from exc
    filename = f"{safe_name(Path(video['name']).stem)}-{int(start)}s-clip.mp4"
    return FileResponse(target, media_type="video/mp4", filename=filename, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/videos/{video_id}/analysis")
def video_analysis(video_id: str) -> dict:
    video = video_record(video_id)
    with db() as connection:
        job = connection.execute(
            "SELECT status, progress, error, attempts, created_at, updated_at FROM analysis_jobs WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        scenes = connection.execute(
            """
            SELECT id, position, start_seconds, end_seconds, label, detection_method
            FROM scenes WHERE video_id = ? ORDER BY position
            """,
            (video_id,),
        ).fetchall()
    if video["source_type"] == "demo" and not job:
        job_payload = {"status": "ready", "progress": 100, "error": None, "attempts": 0}
    else:
        job_payload = dict(job) if job else {"status": "queued", "progress": 0, "error": None, "attempts": 0}
    scene_payload = []
    for scene in scenes:
        item = dict(scene)
        item["thumbnail_url"] = f"/api/videos/{video_id}/scenes/{item['id']}/thumbnail"
        scene_payload.append(item)
    return {"video_id": video_id, "job": job_payload, "scenes": scene_payload}


@app.get("/api/videos/{video_id}/scenes/{scene_id}/thumbnail")
def scene_thumbnail(video_id: str, scene_id: str) -> FileResponse:
    video_record(video_id)
    with db() as connection:
        scene = connection.execute(
            "SELECT thumbnail_path FROM scenes WHERE id = ? AND video_id = ?",
            (scene_id, video_id),
        ).fetchone()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene thumbnail not found")
    path = Path(scene["thumbnail_path"])
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Scene thumbnail is no longer available")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/videos/{video_id}/analysis/retry", status_code=202)
def retry_video_analysis(video_id: str, background_tasks: BackgroundTasks) -> dict:
    video = video_record(video_id)
    if video["source_type"] == "demo":
        return {"video_id": video_id, "status": "ready", "progress": 100}
    with db() as connection:
        queue_analysis(connection, video_id)
    background_tasks.add_task(run_analysis_job, video_id)
    return {"video_id": video_id, "status": "queued", "progress": 0}


@app.get("/api/videos/{video_id}/transcript")
def video_transcript(video_id: str, q: str | None = Query(default=None, max_length=200)) -> dict:
    video = video_record(video_id)
    with db() as connection:
        job = connection.execute(
            "SELECT status, error, attempts, created_at, updated_at FROM transcript_jobs WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        parameters: list = [video_id]
        where = "transcript_segments.video_id = ?"
        query = (q or "").strip()
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where += " AND text LIKE ? ESCAPE '\\'"
            parameters.append(f"%{escaped}%")
        segments = connection.execute(
            f"""
            SELECT transcript_segments.id, position, start_seconds, end_seconds, text, speaker,
                   EXISTS(SELECT 1 FROM transcript_highlights WHERE segment_id = transcript_segments.id) AS highlighted
            FROM transcript_segments WHERE {where}
            ORDER BY position LIMIT 200
            """,
            parameters,
        ).fetchall()
        chapters = connection.execute(
            "SELECT id, position, start_seconds, end_seconds, title, summary FROM transcript_chapters WHERE video_id = ? ORDER BY position",
            (video_id,),
        ).fetchall()
    if video["source_type"] == "demo" and not job:
        job_payload = {"status": "not_applicable", "error": "The bundled visual demo has no transcript job", "attempts": 0}
    else:
        job_payload = dict(job) if job else {"status": "queued", "error": None, "attempts": 0}
    return {"video_id": video_id, "query": query, "job": job_payload, "chapters": [dict(chapter) for chapter in chapters], "segments": [dict(segment) for segment in segments]}


@app.post("/api/videos/{video_id}/transcript/highlights/{segment_id}", status_code=201)
def highlight_transcript_segment(video_id: str, segment_id: str) -> dict:
    video_record(video_id)
    with db() as connection:
        segment = connection.execute(
            "SELECT id, start_seconds, end_seconds, text, speaker FROM transcript_segments WHERE id = ? AND video_id = ?",
            (segment_id, video_id),
        ).fetchone()
        if not segment:
            raise HTTPException(status_code=404, detail="Transcript line not found")
        connection.execute(
            "INSERT OR IGNORE INTO transcript_highlights (id, video_id, segment_id, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, video_id, segment_id, active_account_id(), int(time.time())),
        )
    return {**dict(segment), "highlighted": True}


@app.delete("/api/videos/{video_id}/transcript/highlights/{segment_id}", status_code=204)
def remove_transcript_highlight(video_id: str, segment_id: str) -> Response:
    video_record(video_id)
    with db() as connection:
        connection.execute("DELETE FROM transcript_highlights WHERE video_id = ? AND segment_id = ?", (video_id, segment_id))
    return Response(status_code=204)


@app.post("/api/videos/{video_id}/transcript/retry", status_code=202)
def retry_video_transcript(video_id: str, background_tasks: BackgroundTasks) -> dict:
    video = video_record(video_id)
    if video["source_type"] == "demo":
        return {"video_id": video_id, "status": "not_applicable"}
    with db() as connection:
        queue_transcript(connection, video_id)
    background_tasks.add_task(run_transcript_job, video_id)
    return {"video_id": video_id, "status": "queued"}


@app.get("/api/videos/{video_id}/tags")
def list_video_tags(video_id: str) -> list[str]:
    video_record(video_id)
    with db() as connection:
        records = connection.execute(
            "SELECT tag FROM video_tags WHERE video_id = ? ORDER BY tag",
            (video_id,),
        ).fetchall()
    return [record["tag"] for record in records]


@app.post("/api/videos/{video_id}/tags", status_code=201)
def create_video_tag(video_id: str, payload: TagCreateRequest) -> dict:
    video_record(video_id)
    with db() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO video_tags (video_id, tag, created_at) VALUES (?, ?, ?)",
            (video_id, payload.tag, int(time.time() * 1000)),
        )
    return {"tag": payload.tag}


@app.delete("/api/videos/{video_id}/tags/{tag}", status_code=204)
def delete_video_tag(video_id: str, tag: str) -> Response:
    video_record(video_id)
    normalized = re.sub(r"\s+", " ", tag.strip().lower())
    with db() as connection:
        deleted = connection.execute(
            "DELETE FROM video_tags WHERE video_id = ? AND tag = ?",
            (video_id, normalized),
        )
    if deleted.rowcount == 0:
        raise HTTPException(status_code=404, detail="Tag not found")
    return Response(status_code=204)


@app.get("/api/videos/{video_id}/annotations")
def list_annotations(video_id: str) -> list[dict]:
    video_record(video_id)
    with db() as connection:
        records = connection.execute(
            """
            SELECT id, timestamp, note, created_at
            FROM annotations
            WHERE video_id = ? AND workspace_id = ?
            ORDER BY timestamp, created_at
            """,
            (video_id, active_workspace_id()),
        ).fetchall()
    return [dict(record) for record in records]


@app.post("/api/videos/{video_id}/annotations", status_code=201)
def create_annotation(video_id: str, payload: AnnotationCreateRequest) -> dict:
    video = video_record(video_id)
    timestamp = min(payload.timestamp, float(video["duration"] or payload.timestamp))
    annotation_id = uuid.uuid4().hex
    created_at = int(time.time() * 1000)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO annotations (id, video_id, workspace_id, account_id, timestamp, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (annotation_id, video_id, active_workspace_id(), active_account_id(), timestamp, payload.note, created_at),
        )
    return {"id": annotation_id, "timestamp": timestamp, "note": payload.note, "created_at": created_at}


@app.delete("/api/videos/{video_id}/annotations/{annotation_id}", status_code=204)
def delete_annotation(video_id: str, annotation_id: str) -> Response:
    video_record(video_id)
    with db() as connection:
        deleted = connection.execute(
            "DELETE FROM annotations WHERE id = ? AND video_id = ? AND workspace_id = ?",
            (annotation_id, video_id, active_workspace_id()),
        )
    if deleted.rowcount == 0:
        raise HTTPException(status_code=404, detail="Annotation not found")
    return Response(status_code=204)


@app.get("/api/videos/{video_id}/export")
def export_video_notes(
    video_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|json|html)$"),
    template: str = Query(default="brief", pattern="^(brief|research|review|support-ticket|content-brief)$"),
    conversation_id: str | None = Query(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"),
) -> Response:
    video = video_record(video_id)
    with db() as connection:
        annotations = [dict(row) for row in connection.execute(
            "SELECT timestamp, note, created_at FROM annotations WHERE video_id = ? AND workspace_id = ? ORDER BY timestamp",
            (video_id, active_workspace_id()),
        ).fetchall()]
        transcript = [dict(row) for row in connection.execute(
            "SELECT start_seconds, end_seconds, text, speaker FROM transcript_segments WHERE video_id = ? ORDER BY position",
            (video_id,),
        ).fetchall()]
        chapters = [dict(row) for row in connection.execute(
            "SELECT start_seconds, end_seconds, title, summary FROM transcript_chapters WHERE video_id = ? ORDER BY position",
            (video_id,),
        ).fetchall()]
        highlights = [dict(row) for row in connection.execute(
            """SELECT transcript_segments.start_seconds, transcript_segments.text, transcript_segments.speaker
               FROM transcript_highlights JOIN transcript_segments ON transcript_segments.id = transcript_highlights.segment_id
               WHERE transcript_highlights.video_id = ? ORDER BY transcript_segments.position""",
            (video_id,),
        ).fetchall()]
        conversations = [dict(row) for row in connection.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE video_id = ? ORDER BY updated_at DESC",
            (video_id,),
        ).fetchall()]
        if conversation_id:
            conversations = [item for item in conversations if item["id"] == conversation_id]
            if not conversations:
                raise HTTPException(status_code=404, detail="Conversation not found")
        for conversation in conversations:
            conversation["messages"] = [dict(row) for row in connection.execute(
                "SELECT role, content, created_at FROM messages WHERE video_id = ? AND conversation_id = ? ORDER BY created_at, rowid",
                (video_id, conversation["id"]),
            ).fetchall()]
    payload = {
        "video": {key: video[key] for key in ("id", "name", "duration", "source_type", "source_url", "created_at")},
        "annotations": annotations,
        "transcript": transcript,
        "chapters": chapters,
        "highlights": highlights,
        "conversations": conversations,
        "template": template,
    }
    filename = safe_name(Path(video["name"]).stem) or "video-notes"
    if format == "json":
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}-notes.json"'},
        )
    if format == "html":
        chapter_html = "".join(
            f"<li><time>{int(item['start_seconds'] // 60):02d}:{int(item['start_seconds'] % 60):02d}</time><strong>{html.escape(item['title'])}</strong><p>{html.escape(item['summary'])}</p></li>"
            for item in chapters
        ) or "<li>No chapters available.</li>"
        highlight_html = "".join(
            f"<blockquote><time>{int(item['start_seconds'] // 60):02d}:{int(item['start_seconds'] % 60):02d}</time> {html.escape(item['text'])}</blockquote>"
            for item in highlights
        ) or "<p>No highlighted quotes.</p>"
        document = f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>{html.escape(video['name'])}</title><style>body{{font:16px/1.55 system-ui;color:#111;max-width:820px;margin:64px auto;padding:0 28px}}h1{{font-size:40px;letter-spacing:-.04em}}h2{{margin-top:48px;border-top:1px solid #ccc;padding-top:20px}}li{{margin:20px 0}}time{{font:12px ui-monospace;margin-right:12px;color:#666}}strong{{display:inline}}blockquote{{border-left:2px solid #111;margin:20px 0;padding-left:18px}}</style></head><body><small>{template.upper()} REPORT</small><h1>{html.escape(video['name'])}</h1><p>{len(chapters)} chapters · {len(highlights)} highlights · {len(annotations)} notes</p><h2>Chapters</h2><ol>{chapter_html}</ol><h2>Highlighted quotes</h2>{highlight_html}</body></html>"""
        return Response(document, media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}-{template}.html"'})
    lines = [f"# {video['name']}", "", f"Duration: {video['duration'] or 'unknown'} seconds", "", "## Annotations", ""]
    lines.extend(f"- **{int(item['timestamp'] // 60):02d}:{int(item['timestamp'] % 60):02d}** — {item['note']}" for item in annotations)
    if not annotations:
        lines.append("_No annotations yet._")
    lines.extend(["", "## Transcript", ""])
    lines.extend(
        f"- **{int(item['start_seconds'] // 60):02d}:{int(item['start_seconds'] % 60):02d}** "
        + ((item["speaker"] + ": ") if item.get("speaker") else "")
        + item["text"]
        for item in transcript
    )
    if not transcript:
        lines.append("_No transcript available._")
    for conversation in conversations:
        lines.extend(["", f"## {conversation['title']}", ""])
        lines.extend(f"**{message['role'].title()}:** {message['content']}" for message in conversation["messages"])
    return Response(
        "\n".join(lines) + "\n",
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}-notes.md"'},
    )


@app.post("/api/videos/{video_id}/shares", status_code=201)
def create_share(video_id: str, payload: ShareCreateRequest) -> dict:
    video = video_record(video_id)
    timestamp = min(payload.timestamp, float(video["duration"] or payload.timestamp))
    token = uuid.uuid4().hex
    now = int(time.time())
    expires_at = now + payload.expires_hours * 3600
    with db() as connection:
        connection.execute(
            "INSERT INTO share_links (token, video_id, workspace_id, timestamp, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token, video_id, active_workspace_id(), timestamp, expires_at, now),
        )
    return {"token": token, "timestamp": timestamp, "expires_at": expires_at, "share_path": f"/?share={token}"}


def shared_video(token: str) -> Any:
    with db() as connection:
        record = connection.execute(
            """
            SELECT share_links.token, share_links.timestamp, share_links.expires_at,
                   videos.id AS video_id, videos.name, videos.duration, videos.mime_type, videos.path, videos.storage_key
            FROM share_links JOIN videos ON videos.id = share_links.video_id
            WHERE share_links.token = ?
            """,
            (token,),
        ).fetchone()
    if not record:
        raise HTTPException(status_code=404, detail="Shared moment not found")
    if record["expires_at"] <= int(time.time()):
        raise HTTPException(status_code=410, detail="This shared moment has expired")
    return record


@app.get("/api/shares/{token}")
def share_details(token: str) -> dict:
    record = shared_video(token)
    return {
        "token": token,
        "video_id": record["video_id"],
        "name": record["name"],
        "duration": record["duration"],
        "timestamp": record["timestamp"],
        "expires_at": record["expires_at"],
        "content_url": f"/api/shares/{token}/content",
    }


@app.get("/api/shares/{token}/content")
def share_content(token: str) -> Response:
    record = shared_video(token)
    if record["storage_key"]:
        try:
            signed = storage().signed_url(record["storage_key"], expires_in=300)
        except StorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if signed:
            return RedirectResponse(signed, status_code=307, headers={"Cache-Control": "private, no-store"})
    path = Path(record["path"])
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Shared media is no longer available")
    return FileResponse(path, media_type=record["mime_type"], headers={"Cache-Control": "private, max-age=300"})


@app.post("/api/compare")
def compare_videos(payload: CompareVideosRequest) -> dict:
    videos = [video_record(video_id) for video_id in payload.video_ids]
    results = []
    with db() as connection:
        for video in videos:
            scenes = [dict(row) for row in connection.execute(
                "SELECT position, start_seconds, end_seconds, label, detection_method FROM scenes WHERE video_id = ? ORDER BY position",
                (video["id"],),
            ).fetchall()]
            transcript_count = connection.execute(
                "SELECT COUNT(*) AS count FROM transcript_segments WHERE video_id = ?",
                (video["id"],),
            ).fetchone()["count"]
            transcript_text = " ".join(
                row["text"] for row in connection.execute(
                    "SELECT text FROM transcript_segments WHERE video_id = ? ORDER BY position", (video["id"],)
                ).fetchall()
            )
            results.append({
                "id": video["id"],
                "name": video["name"],
                "duration": video["duration"],
                "source_type": video["source_type"],
                "scene_count": len(scenes),
                "transcript_segment_count": transcript_count,
                "transcript_text": transcript_text,
                "scenes": scenes,
            })
    first_duration = float(results[0]["duration"] or 0)
    second_duration = float(results[1]["duration"] or 0)
    duration_delta = round(second_duration - first_duration, 2)
    max_scenes = max(len(results[0]["scenes"]), len(results[1]["scenes"]))
    paired_moments = []
    for index in range(max_scenes):
        paired_moments.append({
            "position": index,
            "first": results[0]["scenes"][index] if index < len(results[0]["scenes"]) else None,
            "second": results[1]["scenes"][index] if index < len(results[1]["scenes"]) else None,
        })
    if duration_delta == 0:
        comparison_summary = f"Both videos are the same duration; their timelines contain {results[0]['scene_count']} and {results[1]['scene_count']} saved moments."
    else:
        comparison_summary = (
            f"{results[1]['name']} is {abs(duration_delta):g} seconds "
            f"{'longer' if duration_delta > 0 else 'shorter'} than {results[0]['name']}; "
            f"their timelines contain {results[0]['scene_count']} and {results[1]['scene_count']} saved moments."
        )
    stop_words = {"the", "and", "that", "this", "with", "from", "have", "were", "what", "when", "your", "about", "into", "then", "they", "them"}
    term_sets = [
        {word for word in re.findall(r"[a-z0-9']{4,}", item["transcript_text"].lower()) if word not in stop_words}
        for item in results
    ]
    shared_terms = sorted(term_sets[0] & term_sets[1])[:12]
    union = term_sets[0] | term_sets[1]
    transcript_similarity = round(len(term_sets[0] & term_sets[1]) / len(union), 3) if union else None
    for item in results:
        item.pop("transcript_text", None)
    semantic_status = (
        f"Transcript comparison found {len(shared_terms)} shared key terms."
        if transcript_similarity is not None
        else "Generate transcripts for both videos to add topic similarity; visual semantic comparison still needs a vision model."
    )
    return {
        "videos": results,
        "duration_delta_seconds": duration_delta,
        "summary": comparison_summary,
        "paired_moments": paired_moments,
        "transcript_similarity": transcript_similarity,
        "shared_terms": shared_terms,
        "semantic_status": semantic_status,
    }


@app.get("/api/videos/{video_id}/summary")
def summary(video_id: str) -> dict:
    video = video_record(video_id)
    duration = video["duration"] or 12
    is_demo = is_demo_video(Path(video["path"]))
    if is_demo:
        summary_text = "A trail runner moves through a misty alpine valley as warm sunrise light breaks across the mountains."
        tags = ["trail running", "mountains", "sunrise"]
        labels = ["Valley reveal", "Waterfall", "Runner advances", "Summit light"]
        generated = True
    else:
        summary_text = (
            "The video is uploaded and its timeline is ready. A visual summary will appear after a vision "
            "model or local Video-ChatGPT worker is configured."
        )
        tags = []
        labels = ["Opening", "Early context", "Key moment", "Conclusion"]
        generated = False
    return {
        "video_id": video_id,
        "summary": summary_text,
        "tags": tags,
        "scenes": [
            {"timestamp": 0, "label": labels[0]},
            {"timestamp": round(duration * 0.25, 1), "label": labels[1]},
            {"timestamp": round(duration * 0.58, 1), "label": labels[2]},
            {"timestamp": round(duration * 0.86, 1), "label": labels[3]},
        ],
        "inference_mode": inference_mode(),
        "generated": generated,
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
    remaining = enforce_rate_limit("chat", limit=CHAT_REQUESTS_PER_HOUR)
    video = video_record(payload.video_id)
    conversation_id = payload.conversation_id or uuid.uuid4().hex
    with db() as connection:
        existing_conversation = connection.execute(
            "SELECT video_id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if existing_conversation and existing_conversation["video_id"] != payload.video_id:
            raise HTTPException(status_code=409, detail="Conversation belongs to another video")
        now = int(time.time() * 1000)
        if not existing_conversation:
            connection.execute(
                "INSERT INTO conversations (id, video_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, payload.video_id, "New conversation", now, now),
            )
        history_rows = connection.execute(
            """
            SELECT role, content FROM messages
            WHERE video_id = ? AND conversation_id = ?
            ORDER BY created_at DESC, rowid DESC LIMIT 8
            """,
            (payload.video_id, conversation_id),
        ).fetchall()
        transcript_rows = connection.execute(
            """
            SELECT start_seconds, end_seconds, text FROM transcript_segments
            WHERE video_id = ? ORDER BY position LIMIT 80
            """,
            (payload.video_id,),
        ).fetchall()
    history = [dict(row) for row in reversed(history_rows)]
    transcript = [dict(row) for row in transcript_rows]
    answer = await asyncio.to_thread(
        answer_video_question,
        Path(video["path"]),
        payload.message,
        history,
        video["duration"] or 12,
        grounded_answer,
        transcript,
    )
    now = int(time.time() * 1000)
    with db() as connection:
        connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, payload.video_id, conversation_id, "user", payload.message, now))
        connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, payload.video_id, conversation_id, "assistant", answer, now))
        connection.execute(
            """
            UPDATE conversations
            SET title = CASE WHEN title = 'New conversation' THEN ? ELSE title END, updated_at = ?
            WHERE id = ? AND video_id = ?
            """,
            (payload.message[:120], now, conversation_id, payload.video_id),
        )

    async def events() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conversation_id, 'remaining': remaining})}\n\n"
        for token in answer.split(" "):
            if await request.is_disconnected():
                return
            yield f"data: {json.dumps({'type': 'token', 'text': token + ' '})}\n\n"
            await asyncio.sleep(0.035)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/videos/{video_id}/conversations")
def conversations(video_id: str) -> list[dict]:
    video_record(video_id)
    with db() as connection:
        records = connection.execute(
            """
            SELECT conversations.id, conversations.title, conversations.created_at, conversations.updated_at,
                   COUNT(messages.id) AS message_count
            FROM conversations
            LEFT JOIN messages
              ON messages.video_id = conversations.video_id
             AND messages.conversation_id = conversations.id
            WHERE conversations.video_id = ?
            GROUP BY conversations.id
            ORDER BY conversations.updated_at DESC, conversations.created_at DESC
            """,
            (video_id,),
        ).fetchall()
    return [dict(record) for record in records]


@app.post("/api/videos/{video_id}/conversations", status_code=201)
def create_conversation(video_id: str, payload: ConversationCreateRequest) -> dict:
    video_record(video_id)
    conversation_id = uuid.uuid4().hex
    now = int(time.time() * 1000)
    with db() as connection:
        connection.execute(
            "INSERT INTO conversations (id, video_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, video_id, payload.title, now, now),
        )
    return {"id": conversation_id, "title": payload.title, "created_at": now, "updated_at": now, "message_count": 0}


@app.patch("/api/videos/{video_id}/conversations/{conversation_id}")
def rename_conversation(video_id: str, conversation_id: str, payload: ConversationRenameRequest) -> dict:
    video_record(video_id)
    with db() as connection:
        updated = connection.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND video_id = ?",
            (payload.title, int(time.time() * 1000), conversation_id, video_id),
        )
    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": conversation_id, "title": payload.title}


@app.delete("/api/videos/{video_id}/conversations/{conversation_id}", status_code=204)
def delete_conversation(video_id: str, conversation_id: str) -> Response:
    video_record(video_id)
    with db() as connection:
        exists = connection.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND video_id = ?",
            (conversation_id, video_id),
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Conversation not found")
        connection.execute(
            "DELETE FROM messages WHERE video_id = ? AND conversation_id = ?",
            (video_id, conversation_id),
        )
        connection.execute(
            "DELETE FROM conversations WHERE id = ? AND video_id = ?",
            (conversation_id, video_id),
        )
    return Response(status_code=204)


@app.get("/api/videos/{video_id}/messages")
def messages(video_id: str, conversation_id: str | None = Query(default=None, max_length=64)) -> list[dict]:
    video_record(video_id)
    with db() as connection:
        if conversation_id:
            records = connection.execute(
                """
                SELECT id, conversation_id, role, content, created_at FROM messages
                WHERE video_id = ? AND conversation_id = ? ORDER BY created_at, rowid
                """,
                (video_id, conversation_id),
            ).fetchall()
        else:
            records = connection.execute(
                "SELECT id, conversation_id, role, content, created_at FROM messages WHERE video_id = ? ORDER BY created_at, rowid",
                (video_id,),
            ).fetchall()
    return [dict(record) for record in records]


frontend_directory = os.getenv("SERVE_FRONTEND_DIR", "").strip()
if frontend_directory:
    frontend_path = Path(frontend_directory).expanduser().resolve()
    if not frontend_path.is_dir():
        raise RuntimeError(f"SERVE_FRONTEND_DIR does not exist: {frontend_path}")
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
