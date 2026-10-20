from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def using_postgres() -> bool:
    return database_url().startswith(("postgresql://", "postgres://"))


def _postgres_sql(sql: str) -> str:
    insert_or_ignore = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql, flags=re.IGNORECASE))
    sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, flags=re.IGNORECASE)
    if insert_or_ignore:
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    sql = sql.replace("lower(hex(randomblob(16)))", "replace(gen_random_uuid()::text, '-', '')")
    sql = re.sub(r"\bMAX\(([^(),]+),\s*([^()]+)\)", r"GREATEST(\1, \2)", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bMIN\(([^(),]+),\s*([^()]+)\)", r"LEAST(\1, \2)", sql, flags=re.IGNORECASE)
    sql = re.sub(r"GROUP_CONCAT\(([^,]+),\s*('(?:[^']|'')*')\)", r"STRING_AGG(\1, \2)", sql, flags=re.IGNORECASE)
    sql = re.sub(r",\s*rowid\b", "", sql, flags=re.IGNORECASE)

    out: list[str] = []
    quoted = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'":
            out.append(char)
            if quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                out.append("'")
                index += 2
                continue
            quoted = not quoted
        elif char == "?" and not quoted:
            out.append("%s")
        else:
            out.append(char)
        index += 1
    return "".join(out)


class PostgresConnection:
    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, sql: str, parameters: tuple[Any, ...] | list[Any] = ()):
        if sql.strip().upper().startswith("PRAGMA TABLE_INFO("):
            table = sql.strip()[len("PRAGMA table_info(") :].rstrip(")")
            sql = """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
            """
            parameters = (table,)
        return self._connection.execute(_postgres_sql(sql), parameters)

    def executemany(self, sql: str, parameters):
        return self._connection.cursor().executemany(_postgres_sql(sql), parameters, returning=False)

    def executescript(self, sql: str) -> None:
        for statement in (part.strip() for part in sql.split(";")):
            if statement:
                self._connection.execute(_postgres_sql(statement))

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


@contextmanager
def connect(database_path: Path) -> Iterator[Any]:
    if using_postgres():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - only possible in a misbuilt deployment
            raise RuntimeError("DATABASE_URL requires the psycopg package") from exc
        raw = psycopg.connect(
            database_url(),
            row_factory=dict_row,
            connect_timeout=10,
            prepare_threshold=None,
        )
        connection: Any = PostgresConnection(raw)
    else:
        raw = sqlite3.connect(database_path)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        connection = raw
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
