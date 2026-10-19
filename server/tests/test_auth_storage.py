from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import main
from server.storage import SupabaseStorage


class AuthBoundaryTests(unittest.TestCase):
    def test_required_auth_rejects_anonymous_private_routes(self) -> None:
        with patch.dict(os.environ, {"AUTH_MODE": "required"}):
            with TestClient(main.app) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                response = client.get("/api/videos")
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"], "Sign in is required")


class SupabaseStorageTests(unittest.TestCase):
    def test_original_is_mirrored_under_the_account_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "video.mp4"
            media.write_bytes(b"video-bytes")
            adapter = SupabaseStorage(root, "https://project.supabase.co/storage/v1", "private", "server-secret")
            with patch("server.storage.urllib.request.urlopen") as open_url:
                open_url.return_value.__enter__.return_value = object()
                key = adapter.sync_video(media, "user-123", "video-456", "video/mp4")
            self.assertEqual(key, "user-123/video-456/video.mp4")
            request = open_url.call_args.args[0]
            self.assertIn("/object/private/user-123/video-456/video.mp4", request.full_url)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.data, b"video-bytes")
            self.assertNotIn("server-secret", request.full_url)
