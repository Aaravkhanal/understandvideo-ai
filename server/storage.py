from __future__ import annotations

import shutil
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

    def delete_video(self, media_path: Path, video_id: str) -> None:
        root = self.root.resolve()
        try:
            resolved_media = media_path.resolve()
            if not resolved_media.is_relative_to(root):
                raise StorageError("Refusing to delete media outside the configured storage root")
            resolved_media.unlink(missing_ok=True)
        finally:
            shutil.rmtree(self.root / "derived" / video_id, ignore_errors=True)
