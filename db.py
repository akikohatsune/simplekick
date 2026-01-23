import sqlite3
import threading
import time


class Database:
    def __init__(self, path: str = "blacklist.db") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._lock = threading.Lock()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                added_by INTEGER,
                reason TEXT,
                added_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS temp_exempt (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                granted_by INTEGER,
                reason TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        self._conn.commit()

    def is_blacklisted(self, guild_id: int, user_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM blacklist WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            return cur.fetchone() is not None

    def add_blacklist(
        self, guild_id: int, user_id: int, added_by: int | None, reason: str | None
    ) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO blacklist (guild_id, user_id, added_by, reason, added_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, added_by, reason, now),
            )
            self._conn.commit()

    def remove_blacklist(self, guild_id: int, user_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM blacklist WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_blacklist(self, guild_id: int, limit: int = 50) -> list[tuple[int, str | None, int, int | None]]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT user_id, reason, added_at, added_by
                FROM blacklist
                WHERE guild_id = ?
                ORDER BY added_at DESC
                LIMIT ?
                """,
                (guild_id, limit),
            )
            return cur.fetchall()

    def add_temp_exempt(
        self,
        guild_id: int,
        user_id: int,
        expires_at: int,
        granted_by: int | None,
        reason: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO temp_exempt (guild_id, user_id, expires_at, granted_by, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, expires_at, granted_by, reason),
            )
            self._conn.commit()

    def remove_temp_exempt(self, guild_id: int, user_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM temp_exempt WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def is_temp_exempt(self, guild_id: int, user_id: int) -> bool:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "SELECT expires_at FROM temp_exempt WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return False
            expires_at = row[0]
            if expires_at <= now:
                self._conn.execute(
                    "DELETE FROM temp_exempt WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
                self._conn.commit()
                return False
            return True
