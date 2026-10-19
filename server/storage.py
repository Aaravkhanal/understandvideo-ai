from __future__ import annotations

import shutil
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class LocalStorage:
    """Local development storage behind the same boundary a cloud adapter can implement."""

    root: Path

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def upload_path(self, video_id: str, filename: str) -> Path:
        self.ensure_ready()
        return self.root / f"{video_id}-{filename}"

    def derived_root(self) -> Path:
        self.ensure_ready()
        return self.root

    def sync_video(self, media_path: Path, account_id: str, video_id: str, mime_type: str) -> str | None:
        return None

    def signed_url(self, object_key: str, expires_in: int = 300) -> str | None:
        return None

    def delete_video(self, media_path: Path, video_id: str, object_key: str | None = None) -> None:
        root = self.root.resolve()
        try:
            resolved_media = media_path.resolve()
            if not resolved_media.is_relative_to(root):
                raise StorageError("Refusing to delete media outside the configured storage root")
            resolved_media.unlink(missing_ok=True)
        finally:
            shutil.rmtree(self.root / "derived" / video_id, ignore_errors=True)


@dataclass(frozen=True)
class SupabaseStorage(LocalStorage):
    """Keep a processing copy locally while mirroring originals to a private bucket."""

    endpoint: str
    bucket: str
    service_role_key: str

    def _request(self, method: str, object_key: str, data: bytes | None = None, mime_type: str = "application/octet-stream") -> None:
        encoded_key = "/".join(urllib.parse.quote(part, safe="") for part in object_key.split("/"))
        url = f"{self.endpoint.rstrip('/')}/object/{urllib.parse.quote(self.bucket, safe='')}/{encoded_key}"
        headers = {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
            "Content-Type": mime_type,
        }
        if method == "POST":
            headers["x-upsert"] = "true"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60):
                return
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise StorageError(f"Supabase Storage rejected the {method.lower()} request: {detail}") from exc
        except OSError as exc:
            raise StorageError("Supabase Storage could not be reached") from exc

    def sync_video(self, media_path: Path, account_id: str, video_id: str, mime_type: str) -> str:
        object_key = f"{account_id}/{video_id}/{media_path.name}"
        self._request("POST", object_key, media_path.read_bytes(), mime_type)
        return object_key

    def signed_url(self, object_key: str, expires_in: int = 300) -> str:
        encoded_key = "/".join(urllib.parse.quote(part, safe="") for part in object_key.split("/"))
        url = f"{self.endpoint.rstrip('/')}/object/sign/{urllib.parse.quote(self.bucket, safe='')}/{encoded_key}"
        body = json.dumps({"expiresIn": max(30, min(3600, expires_in))}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.service_role_key}",
                "apikey": self.service_role_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise StorageError("Supabase Storage could not create a playback link") from exc
        signed_path = payload.get("signedURL") or payload.get("signedUrl")
        if not signed_path:
            raise StorageError("Supabase Storage returned no playback link")
        if signed_path.startswith("http"):
            return signed_path
        return f"{self.endpoint.rstrip('/')}{signed_path if signed_path.startswith('/') else '/' + signed_path}"

    def delete_video(self, media_path: Path, video_id: str, object_key: str | None = None) -> None:
        if object_key:
            self._request("DELETE", object_key)
        super().delete_video(media_path, video_id, object_key)
