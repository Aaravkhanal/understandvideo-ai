from __future__ import annotations

import unittest

from server.database import _postgres_sql


class PostgresCompatibilityTests(unittest.TestCase):
    def test_placeholders_ignore_and_sqlite_functions_are_translated(self) -> None:
        self.assertEqual(
            _postgres_sql("INSERT OR IGNORE INTO video_tags (video_id, tag) VALUES (?, ?)"),
            "INSERT INTO video_tags (video_id, tag) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        )
        self.assertIn(
            "GREATEST(progress, 3)",
            _postgres_sql("UPDATE import_jobs SET progress = MAX(progress, 3) WHERE id = ?"),
        )
        self.assertIn(
            "STRING_AGG(tag, '|||')",
            _postgres_sql("SELECT GROUP_CONCAT(tag, '|||') FROM video_tags WHERE video_id = ?"),
        )

    def test_question_marks_inside_literals_are_not_parameters(self) -> None:
        translated = _postgres_sql("SELECT '?' AS literal FROM videos WHERE id = ?")
        self.assertEqual(translated, "SELECT '?' AS literal FROM videos WHERE id = %s")
