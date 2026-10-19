import os
from unittest import TestCase
from unittest.mock import MagicMock, patch

from server import job_queue


class JobQueueTests(TestCase):
    def tearDown(self) -> None:
        job_queue.redis_client.cache_clear()

    def test_notify_is_optional_without_redis(self) -> None:
        with patch.dict(os.environ, {"REDIS_URL": ""}, clear=False):
            job_queue.redis_client.cache_clear()
            job_queue.notify_jobs()
            self.assertIsNone(job_queue.redis_client())

    def test_worker_lease_uses_unique_redis_lock(self) -> None:
        client = MagicMock()
        client.set.return_value = True
        with patch("server.job_queue.redis_client", return_value=client):
            with job_queue.worker_sweep_lease(60) as acquired:
                self.assertTrue(acquired)
        client.set.assert_called_once()
        client.eval.assert_called_once()

    def test_notify_caps_pending_wake_messages(self) -> None:
        client = MagicMock()
        with patch("server.job_queue.redis_client", return_value=client):
            job_queue.notify_jobs()
        client.lpush.assert_called_once_with(job_queue.WAKE_KEY, "1")
        client.ltrim.assert_called_once_with(job_queue.WAKE_KEY, 0, 99)
