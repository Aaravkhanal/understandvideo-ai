from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from server import main, worker
from server.analysis import extract_scene_thumbnails
from server.remote_import import (
    RemoteImportError,
    RemoteMedia,
    choose_platform_format,
    download_platform_video,
    validate_public_https_url,
)
from server.transcription import TranscriptSegment


class ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import cv2
        import numpy as np

        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.original_database = main.DATABASE
        self.original_uploads = main.UPLOADS
        self.original_max_upload = main.MAX_UPLOAD_BYTES
        main.DATABASE = root / "test.db"
        main.UPLOADS = root / "uploads"
        main.requests_by_identity.clear()
        main.cancelled_video_ids.clear()
        main.cancelled_import_ids.clear()
        main.initialize()
        self.video_fixture = root / "fixture.avi"
        writer = cv2.VideoWriter(str(self.video_fixture), cv2.VideoWriter_fourcc(*"MJPG"), 5, (32, 32))
        self.assertTrue(writer.isOpened())
        try:
            for value in (0, 30, 60, 90, 120, 150):
                writer.write(np.full((32, 32, 3), value, dtype=np.uint8))
        finally:
            writer.release()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        main.DATABASE = self.original_database
        main.UPLOADS = self.original_uploads
        main.MAX_UPLOAD_BYTES = self.original_max_upload
        self.temporary_directory.cleanup()

    def create_video(self, name: str = "sample.mp4") -> str:
        response = self.client.post(
            "/api/videos/upload",
            files={"file": (name, self.video_fixture.read_bytes(), "video/x-msvideo")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_public_config_matches_runtime_limits(self) -> None:
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["max_upload_bytes"], main.MAX_UPLOAD_BYTES)
        self.assertEqual(payload["chat_requests_per_hour"], main.CHAT_REQUESTS_PER_HOUR)
        self.assertEqual(payload["remote_import_timeout_seconds"], main.REMOTE_IMPORT_TIMEOUT_SECONDS)
        self.assertIn("video/mp4", payload["allowed_mime_types"])
        self.assertEqual(payload["workspace"]["id"], main.ACTIVE_WORKSPACE_ID)

    def test_registered_webhook_receives_signed_product_events(self) -> None:
        with main.db() as connection:
            connection.execute(
                "INSERT INTO webhook_subscriptions (id, workspace_id, url, events, signing_secret, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("hook-1", main.ACTIVE_WORKSPACE_ID, "https://hooks.example.test/video", '["video.ready"]', "whsec_test", 1),
            )
        response = Mock(status=204)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("server.main.validate_public_https_url"), patch("server.main.urllib.request.urlopen", return_value=response) as open_url:
            delivered = main.emit_webhook_event(main.ACTIVE_WORKSPACE_ID, "video.ready", {"video_id": "video-1"})
        self.assertEqual(delivered, 1)
        request = open_url.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertTrue(request.headers["X-understandvideo-signature"].startswith("sha256="))
        self.assertIn(b'"video_id":"video-1"', request.data)

    def test_default_retention_is_applied_to_new_uploads(self) -> None:
        video_id = self.create_video("retained.avi")
        with main.db() as connection:
            video = connection.execute(
                "SELECT created_at, expires_at FROM videos WHERE id = ?", (video_id,)
            ).fetchone()
        self.assertEqual(
            video["expires_at"],
            video["created_at"] + main.DEFAULT_RETENTION_DAYS * 86400,
        )

    def test_local_video_playback_uses_an_authenticated_content_route(self) -> None:
        video_id = self.create_video("playback.avi")
        response = self.client.get(f"/api/videos/{video_id}/playback")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["url"], f"/api/videos/{video_id}/content")
        self.assertEqual(response.json()["source"], "local-private-media")

    def test_remote_clamav_scans_by_stream_not_worker_path(self) -> None:
        completed = Mock(returncode=0)
        with patch("server.main.shutil.which", return_value="/usr/bin/clamdscan"), patch(
            "server.main.subprocess.run", return_value=completed
        ) as run:
            main.scan_for_malware(self.video_fixture)

        self.assertIn("--stream", run.call_args.args[0])

    def test_workspace_boundary_hides_unowned_videos(self) -> None:
        video_id = self.create_video()
        with main.db() as connection:
            connection.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?)",
                ("other-account", "other@example.test", "Other user", 1),
            )
            connection.execute(
                "INSERT INTO workspaces VALUES (?, ?, ?, ?)",
                ("other-workspace", "Other workspace", "other-account", 1),
            )
            connection.execute(
                "INSERT INTO workspace_members VALUES (?, ?, ?, ?)",
                ("other-workspace", "other-account", "owner", 1),
            )
            connection.execute(
                "UPDATE videos SET workspace_id = 'other-workspace' WHERE id = ?",
                (video_id,),
            )

        listed = self.client.get("/api/videos")
        self.assertNotIn(video_id, [video["id"] for video in listed.json()])
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/content").status_code, 404)

    def test_grounded_answer_worker_rejects_media_outside_storage(self) -> None:
        original_root = worker.MEDIA_ROOT
        worker.MEDIA_ROOT = main.UPLOADS.resolve()
        try:
            client = TestClient(worker.app)
            response = client.post(
                "/answer",
                json={
                    "video_path": str(Path(self.temporary_directory.name).parent / "outside.mp4"),
                    "question": "What happens?",
                    "duration": 10,
                },
            )
            self.assertEqual(response.status_code, 404)
            client.close()
        finally:
            worker.MEDIA_ROOT = original_root

    def test_annotations_exports_and_exact_moment_shares(self) -> None:
        video_id = self.create_video()
        with main.db() as connection:
            connection.execute("UPDATE videos SET duration = 10 WHERE id = ?", (video_id,))
        annotation = self.client.post(
            f"/api/videos/{video_id}/annotations",
            json={"timestamp": 3.5, "note": "Review this transition"},
        )
        self.assertEqual(annotation.status_code, 201, annotation.text)
        listed = self.client.get(f"/api/videos/{video_id}/annotations")
        self.assertEqual(listed.json()[0]["note"], "Review this transition")

        exported = self.client.get(f"/api/videos/{video_id}/export")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("Review this transition", exported.text)
        self.assertIn("attachment", exported.headers["content-disposition"])

        shared = self.client.post(
            f"/api/videos/{video_id}/shares",
            json={"timestamp": 3.5, "expires_hours": 24},
        )
        self.assertEqual(shared.status_code, 201, shared.text)
        token = shared.json()["token"]
        details = self.client.get(f"/api/shares/{token}")
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.json()["timestamp"], 3.5)
        content = self.client.get(details.json()["content_url"])
        self.assertEqual(content.content, self.video_fixture.read_bytes())

    def test_two_workspace_videos_can_be_compared(self) -> None:
        first_id = self.create_video("first.mp4")
        second_id = self.create_video("second.mp4")
        with main.db() as connection:
            connection.execute("UPDATE videos SET duration = 10 WHERE id = ?", (first_id,))
            connection.execute("UPDATE videos SET duration = 14 WHERE id = ?", (second_id,))
        response = self.client.post("/api/compare", json={"video_ids": [first_id, second_id]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["duration_delta_seconds"], 4)
        self.assertEqual([video["name"] for video in response.json()["videos"]], ["first.mp4", "second.mp4"])

    def test_upload_rejects_unsupported_content_type(self) -> None:
        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(response.status_code, 415)

    def test_upload_rejects_a_fake_video_with_allowed_mime(self) -> None:
        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("fake.mp4", b"this-is-not-video", "video/mp4")},
        )
        self.assertEqual(response.status_code, 415)
        self.assertIn("file contents", response.json()["detail"])

    def test_upload_limit_error_uses_configured_size(self) -> None:
        main.MAX_UPLOAD_BYTES = 1024 * 1024
        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("large.mp4", b"x" * (1024 * 1024 + 1), "video/mp4")},
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("1 MB", response.json()["detail"])

    def test_blank_chat_message_is_rejected(self) -> None:
        video_id = self.create_video()
        response = self.client.post(
            "/api/chat/stream",
            json={"video_id": video_id, "message": "   "},
        )
        self.assertEqual(response.status_code, 422)

    def test_demo_identity_uses_content_not_filename(self) -> None:
        video_id = self.create_video("trail-runner-demo.mp4")
        response = self.client.get(f"/api/videos/{video_id}/summary")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["generated"])

    def test_chat_history_is_scoped_to_conversation(self) -> None:
        video_id = self.create_video()
        with main.db() as connection:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                ("a", video_id, "conversation-a", "user", "keep this", 1),
            )
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                ("b", video_id, "conversation-b", "user", "do not mix this", 2),
            )
            connection.execute(
                "INSERT INTO transcript_segments (id, video_id, position, start_seconds, end_seconds, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("segment", video_id, 0, 2.0, 4.0, "timestamped evidence", 1),
            )

        captured_history: list[dict] = []
        captured_transcript: list[dict] = []

        def fake_answer(path, question, history, duration, fallback, transcript):
            captured_history.extend(history)
            captured_transcript.extend(transcript)
            return "Grounded answer [[00:00]]"

        with patch("server.main.answer_video_question", side_effect=fake_answer):
            response = self.client.post(
                "/api/chat/stream",
                json={
                    "video_id": video_id,
                    "message": "What happens?",
                    "conversation_id": "conversation-a",
                },
            )

        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(events[0]["conversation_id"], "conversation-a")
        self.assertEqual(captured_history, [{"role": "user", "content": "keep this"}])
        self.assertEqual(captured_transcript[0]["text"], "timestamped evidence")

    def test_conversations_can_be_created_reopened_and_renamed(self) -> None:
        video_id = self.create_video()
        created = self.client.post(
            f"/api/videos/{video_id}/conversations",
            json={"title": "New conversation"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        conversation_id = created.json()["id"]

        with patch("server.main.answer_video_question", return_value="Answer [[00:00]]"):
            chat = self.client.post(
                "/api/chat/stream",
                json={"video_id": video_id, "conversation_id": conversation_id, "message": "Find the opening"},
            )
        self.assertEqual(chat.status_code, 200, chat.text)

        listed = self.client.get(f"/api/videos/{video_id}/conversations")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["title"], "Find the opening")
        self.assertEqual(listed.json()[0]["message_count"], 2)

        history = self.client.get(
            f"/api/videos/{video_id}/messages",
            params={"conversation_id": conversation_id},
        )
        self.assertEqual([message["role"] for message in history.json()], ["user", "assistant"])

        renamed = self.client.patch(
            f"/api/videos/{video_id}/conversations/{conversation_id}",
            json={"title": "Opening notes"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["title"], "Opening notes")

        deleted = self.client.delete(f"/api/videos/{video_id}/conversations/{conversation_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/conversations").json(), [])
        self.assertEqual(
            self.client.get(f"/api/videos/{video_id}/messages", params={"conversation_id": conversation_id}).json(),
            [],
        )

    def test_transcript_segments_are_persisted_and_searchable(self) -> None:
        video_id = self.create_video()
        with main.db() as connection:
            main.queue_transcript(connection, video_id)
        segments = [
            TranscriptSegment(1.25, 3.5, "Welcome to the research review."),
            TranscriptSegment(5.0, 8.0, "The waterfall appears behind the runner."),
        ]
        with patch("server.main.transcribe_video", return_value=segments):
            main.run_transcript_job(video_id)

        transcript = self.client.get(f"/api/videos/{video_id}/transcript")
        self.assertEqual(transcript.status_code, 200, transcript.text)
        self.assertEqual(transcript.json()["job"]["status"], "ready")
        self.assertEqual(len(transcript.json()["segments"]), 2)

        search = self.client.get(f"/api/videos/{video_id}/transcript", params={"q": "waterfall"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(len(search.json()["segments"]), 1)
        self.assertEqual(search.json()["segments"][0]["start_seconds"], 5.0)

    def test_library_search_matches_transcripts_notes_and_messages(self) -> None:
        first_id = self.create_video("research-one.avi")
        second_id = self.create_video("research-two.avi")
        with main.db() as connection:
            connection.execute(
                "INSERT INTO transcript_segments (id, video_id, position, start_seconds, end_seconds, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("search-segment", first_id, 0, 0.0, 1.0, "A rare waterfall appears", 1),
            )
            connection.execute(
                "INSERT INTO annotations VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("search-note", first_id, main.ACTIVE_WORKSPACE_ID, main.LOCAL_ACCOUNT_ID, 0.5, "client approval", 1),
            )
            connection.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
                ("search-conversation", first_id, "Research", 1, 1),
            )
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                ("search-message", first_id, "search-conversation", "user", "orange jacket", 1),
            )

        for query in ("waterfall", "approval", "orange jacket"):
            results = self.client.get("/api/videos", params={"q": query})
            self.assertEqual(results.status_code, 200, results.text)
            self.assertEqual([video["id"] for video in results.json()], [first_id])
        title_results = self.client.get("/api/videos", params={"q": "research-two"})
        self.assertEqual([video["id"] for video in title_results.json()], [second_id])

        created_tag = self.client.post(f"/api/videos/{first_id}/tags", json={"tag": "Food Review"})
        self.assertEqual(created_tag.status_code, 201, created_tag.text)
        self.assertEqual(self.client.get(f"/api/videos/{first_id}/tags").json(), ["food review"])
        tag_results = self.client.get("/api/videos", params={"q": "food review"})
        self.assertEqual([video["id"] for video in tag_results.json()], [first_id])
        self.assertEqual(tag_results.json()[0]["tags"], ["food review"])
        removed_tag = self.client.delete(f"/api/videos/{first_id}/tags/food%20review")
        self.assertEqual(removed_tag.status_code, 204, removed_tag.text)
        self.assertEqual(self.client.get(f"/api/videos/{first_id}/tags").json(), [])

    def test_background_import_job_can_complete_and_be_cancelled(self) -> None:
        imported_path = main.UPLOADS / "background-import.avi"
        imported_path.parent.mkdir(parents=True, exist_ok=True)
        imported_path.write_bytes(self.video_fixture.read_bytes())
        media = RemoteMedia(
            imported_path,
            "Background video",
            "https://www.youtube.com/watch?v=background",
            "video/x-msvideo",
        )

        def fake_import(*args):
            args[-2](48)
            return media

        with patch("server.main.import_remote_video", side_effect=fake_import), patch("server.main.run_video_jobs"):
            created = self.client.post("/api/imports", json={"url": media.source_url})
        self.assertEqual(created.status_code, 202, created.text)
        completed = self.client.get(f"/api/imports/{created.json()['id']}")
        self.assertEqual(completed.json()["status"], "ready")
        self.assertEqual(completed.json()["progress"], 100)
        self.assertEqual(completed.json()["video"]["name"], "Background video")

        with patch("server.main.run_import_job"):
            queued = self.client.post(
                "/api/imports",
                json={"url": "https://www.youtube.com/watch?v=cancel-me"},
            )
        import_id = queued.json()["id"]
        cancelled = self.client.post(f"/api/imports/{import_id}/cancel")
        self.assertEqual(cancelled.status_code, 202, cancelled.text)
        main.run_import_job(import_id)
        self.assertEqual(self.client.get(f"/api/imports/{import_id}").json()["status"], "cancelled")

    def test_remote_import_is_persisted_and_streamable(self) -> None:
        imported_path = main.UPLOADS / "remote.mp4"
        imported_path.parent.mkdir(parents=True, exist_ok=True)
        imported_path.write_bytes(b"remote-video-bytes")
        media = RemoteMedia(
            path=imported_path,
            title="Public video",
            source_url="https://www.youtube.com/watch?v=public",
            mime_type="video/mp4",
        )
        with patch("server.main.import_remote_video", return_value=media):
            response = self.client.post(
                "/api/videos/import-url",
                json={"url": media.source_url},
            )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["source_type"], "url")
        content = self.client.get(payload["content_url"])
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.content, b"remote-video-bytes")
        partial = self.client.get(payload["content_url"], headers={"Range": "bytes=0-5"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"remote")

    def test_remote_format_selection_stays_within_limit_and_keeps_video(self) -> None:
        metadata = {
            "duration": 3343,
            "formats": [
                {"format_id": "134", "ext": "mp4", "vcodec": "avc1.4d401e", "acodec": "none", "height": 360, "filesize": 89_000_000},
                {"format_id": "133", "ext": "mp4", "vcodec": "avc1.4d400d", "acodec": "none", "height": 240, "filesize": 46_000_000},
                {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "filesize": 36_000_000},
                {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 128, "filesize": 41_000_000},
            ],
        }

        selected = choose_platform_format(metadata, 100 * 1024 * 1024)

        self.assertEqual(selected, "133+140")

    def test_remote_format_selection_rejects_audio_only_results(self) -> None:
        metadata = {
            "duration": 60,
            "formats": [
                {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "filesize": 1_000_000},
            ],
        }
        with self.assertRaisesRegex(RemoteImportError, "browser-compatible"):
            choose_platform_format(metadata, 100 * 1024 * 1024)

    def test_broken_url_import_can_be_repaired(self) -> None:
        video_id = self.create_video("broken.webm")
        with main.db() as connection:
            connection.execute(
                "UPDATE videos SET source_type = 'url', source_url = ?, duration = NULL WHERE id = ?",
                ("https://www.youtube.com/watch?v=public", video_id),
            )
            old_path = Path(connection.execute("SELECT path FROM videos WHERE id = ?", (video_id,)).fetchone()["path"])
        stale_partial = main.UPLOADS / f"{video_id}.f398.mp4.part"
        stale_partial.write_bytes(b"incomplete")

        repaired_path = main.UPLOADS / "repaired.mp4"
        repaired_path.write_bytes(b"playable-video")
        media = RemoteMedia(repaired_path, "Repaired video", "https://www.youtube.com/watch?v=public", "video/mp4")
        with patch("server.main.import_remote_video", return_value=media), patch(
            "server.main.probe_video", return_value={"duration": 30.0, "width": 640, "height": 360}
        ):
            response = self.client.post(f"/api/videos/{video_id}/reimport")

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["duration"], 30.0)
        self.assertFalse(old_path.exists())
        self.assertFalse(stale_partial.exists())
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/content").content, b"playable-video")

    def test_analysis_job_persists_real_scene_thumbnails(self) -> None:
        video_id = "analysis-video"
        source = main.ROOT.parent / "public" / "demo" / "trail-runner-demo.webm"
        with main.db() as connection:
            connection.execute(
                """
                INSERT INTO videos
                  (id, name, path, mime_type, size_bytes, duration, width, height, status, created_at, source_type, source_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (video_id, "analysis.webm", str(source), "video/webm", source.stat().st_size, 12, 1280, 720, "ready", 1, "upload", None),
            )
            main.queue_analysis(connection, video_id)

        main.run_analysis_job(video_id)

        response = self.client.get(f"/api/videos/{video_id}/analysis")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["job"]["status"], "ready")
        self.assertEqual(len(payload["scenes"]), 4)
        thumbnail = self.client.get(payload["scenes"][0]["thumbnail_url"])
        self.assertEqual(thumbnail.status_code, 200)
        self.assertEqual(thumbnail.headers["content-type"], "image/jpeg")

    def test_shot_detection_finds_a_strong_visual_cut(self) -> None:
        import cv2
        import numpy as np

        video_path = main.UPLOADS / "cut-test.avi"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 64))
        self.assertTrue(writer.isOpened())
        try:
            for _ in range(20):
                writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
            for _ in range(20):
                writer.write(np.full((64, 64, 3), 255, dtype=np.uint8))
        finally:
            writer.release()

        scenes = extract_scene_thumbnails(video_path, main.UPLOADS, "cut-video", count=3)
        detected = [scene for scene in scenes if scene.detection_method == "histogram-cut-v1"]
        self.assertTrue(detected)
        self.assertAlmostEqual(detected[0].start_seconds, 2.0, delta=0.2)

    def test_rename_and_delete_remove_video_and_related_data(self) -> None:
        video_id = self.create_video()
        with main.db() as connection:
            path = Path(connection.execute("SELECT path FROM videos WHERE id = ?", (video_id,)).fetchone()["path"])
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                ("delete-message", video_id, "delete-conversation", "user", "remove me", 1),
            )

        renamed = self.client.patch(f"/api/videos/{video_id}", json={"name": "Research clip"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "Research clip")

        deleted = self.client.delete(f"/api/videos/{video_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/content").status_code, 404)
        with main.db() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM analysis_jobs WHERE video_id = ?", (video_id,)).fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM transcript_jobs WHERE video_id = ?", (video_id,)).fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM messages WHERE video_id = ?", (video_id,)).fetchone())

    def test_transcript_highlights_retention_and_review_workflow(self) -> None:
        video_id = self.create_video()
        with main.db() as connection:
            connection.execute(
                "INSERT INTO transcript_segments (id, video_id, position, start_seconds, end_seconds, text, speaker, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("quote-segment", video_id, 0, 2.0, 5.0, "A useful quoted line", "Speaker 1", 1),
            )
            connection.execute(
                "INSERT INTO transcript_chapters VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("chapter", video_id, 0, 2.0, 5.0, "Useful quoted line", "A useful quoted line", 1),
            )

        highlighted = self.client.post(f"/api/videos/{video_id}/transcript/highlights/quote-segment")
        self.assertEqual(highlighted.status_code, 201, highlighted.text)
        transcript = self.client.get(f"/api/videos/{video_id}/transcript").json()
        self.assertEqual(transcript["chapters"][0]["title"], "Useful quoted line")
        self.assertEqual(transcript["segments"][0]["speaker"], "Speaker 1")
        self.assertEqual(transcript["segments"][0]["highlighted"], 1)

        retention = self.client.patch(f"/api/videos/{video_id}/retention", json={"days": 30})
        self.assertEqual(retention.status_code, 200, retention.text)
        self.assertIsNotNone(retention.json()["expires_at"])

        review = self.client.put(
            f"/api/videos/{video_id}/review",
            json={"state": "in_review", "assignee": "Aarav"},
        )
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/review").json()["assignee"], "Aarav")

        comment = self.client.post(
            f"/api/videos/{video_id}/comments", json={"body": "Please review this moment", "timestamp": 2.5}
        )
        self.assertEqual(comment.status_code, 201, comment.text)
        self.assertEqual(len(self.client.get(f"/api/videos/{video_id}/comments").json()), 1)

        report = self.client.get(f"/api/videos/{video_id}/export?format=html&template=review")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertIn("A useful quoted line", report.text)

        api_key = self.client.post("/api/developer/api-keys", json={"name": "Automation"})
        self.assertEqual(api_key.status_code, 201, api_key.text)
        self.assertTrue(api_key.json()["key"].startswith("uv_"))
        listed = self.client.get("/api/developer/api-keys").json()
        self.assertNotIn("key", listed[0])
        self.assertEqual(self.client.delete(f"/api/developer/api-keys/{api_key.json()['id']}").status_code, 204)

    def test_private_network_url_is_rejected(self) -> None:
        with patch(
            "server.remote_import.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
        ):
            with self.assertRaisesRegex(RemoteImportError, "Private or local"):
                validate_public_https_url("https://localhost/video.mp4")

    def test_unknown_page_host_is_not_sent_to_platform_extractor(self) -> None:
        with patch("server.remote_import.public_addresses", return_value=["93.184.216.34"]):
            with self.assertRaisesRegex(RemoteImportError, "not on a supported"):
                download_platform_video(
                    "https://example.com/watch/123",
                    main.UPLOADS,
                    "remote-id",
                    1024,
                    60,
                )


if __name__ == "__main__":
    unittest.main()
