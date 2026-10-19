"""Fail deployment early when required production security settings are unsafe."""

from __future__ import annotations

import os
from urllib.parse import urlparse


def require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required production setting: {name}")
    return value


def main() -> None:
    if os.getenv("MALWARE_SCAN_MODE") != "required":
        raise SystemExit("MALWARE_SCAN_MODE must be required in production")
    retention = require("DEFAULT_RETENTION_DAYS")
    if retention not in {"30", "90", "365"}:
        raise SystemExit("DEFAULT_RETENTION_DAYS must be 30, 90, or 365 in production")
    if int(require("MAX_UPLOAD_BYTES")) > 500 * 1024 * 1024:
        raise SystemExit("MAX_UPLOAD_BYTES must not exceed 500 MB")
    origins = [item.strip() for item in require("CORS_ORIGINS").split(",")]
    if any(urlparse(origin).scheme != "https" for origin in origins):
        raise SystemExit("Every production CORS origin must use HTTPS")
    worker_token = require("VIDEO_WORKER_TOKEN")
    if len(worker_token) < 32:
        raise SystemExit("VIDEO_WORKER_TOKEN must contain at least 32 characters")
    require("DATABASE_URL")
    require("REDIS_URL")
    require("OBJECT_STORAGE_BUCKET")
    require("OBJECT_STORAGE_ENDPOINT")
    require("OIDC_ISSUER_URL")
    require("OIDC_AUDIENCE")
    print("Production security configuration is valid")


if __name__ == "__main__":
    main()
