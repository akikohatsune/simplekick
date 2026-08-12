import sqlite3
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceMutePoll:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    target_id: int
    created_by: int
    duration_days: int
    reason: str | None
    created_at: int
    ends_at: int
    eligible_voters: int
    required_votes: int
    status: str
    vote_count: int


@dataclass(frozen=True)
class ActiveVoiceMute:
    guild_id: int
    user_id: int
    poll_id: int | None
    muted_by: int
    reason: str | None
    started_at: int
    expires_at: int


@dataclass(frozen=True)
class MuteVoteResult:
    status: str
    vote_count: int
    required_votes: int


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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_mute_polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                target_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                duration_days INTEGER NOT NULL,
                reason TEXT,
                created_at INTEGER NOT NULL,
                ends_at INTEGER NOT NULL,
                eligible_voters INTEGER NOT NULL,
                required_votes INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                closed_at INTEGER
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_mute_votes (
                poll_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                voted_at INTEGER NOT NULL,
                PRIMARY KEY (poll_id, user_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_voice_mutes (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                poll_id INTEGER,
                muted_by INTEGER NOT NULL,
                reason TEXT,
                started_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_mute_polls_open
            ON voice_mute_polls (guild_id, target_id, status)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_active_voice_mutes_expiry
            ON active_voice_mutes (expires_at)
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

    @staticmethod
    def _poll_from_row(row: tuple) -> VoiceMutePoll:
        return VoiceMutePoll(*row)

    @staticmethod
    def _mute_from_row(row: tuple) -> ActiveVoiceMute:
        return ActiveVoiceMute(*row)

    def create_voice_mute_poll(
        self,
        guild_id: int,
        channel_id: int,
        target_id: int,
        created_by: int,
        duration_days: int,
        reason: str | None,
        ends_at: int,
        eligible_voters: int,
        required_votes: int,
    ) -> tuple[int | None, str | None]:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """
                UPDATE voice_mute_polls
                SET status = 'expired', closed_at = ?
                WHERE guild_id = ? AND target_id = ? AND status = 'open' AND ends_at <= ?
                """,
                (now, guild_id, target_id, now),
            )
            active = self._conn.execute(
                """
                SELECT 1 FROM active_voice_mutes
                WHERE guild_id = ? AND user_id = ? AND expires_at > ?
                """,
                (guild_id, target_id, now),
            ).fetchone()
            if active:
                self._conn.commit()
                return None, "already_muted"
            open_poll = self._conn.execute(
                """
                SELECT 1 FROM voice_mute_polls
                WHERE guild_id = ? AND target_id = ? AND status = 'open'
                """,
                (guild_id, target_id),
            ).fetchone()
            if open_poll:
                self._conn.commit()
                return None, "poll_open"
            cur = self._conn.execute(
                """
                INSERT INTO voice_mute_polls (
                    guild_id, channel_id, target_id, created_by, duration_days,
                    reason, created_at, ends_at, eligible_voters, required_votes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    target_id,
                    created_by,
                    duration_days,
                    reason,
                    now,
                    ends_at,
                    eligible_voters,
                    required_votes,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid), None

    def set_voice_mute_poll_message(self, poll_id: int, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE voice_mute_polls SET message_id = ? WHERE id = ?",
                (message_id, poll_id),
            )
            self._conn.commit()

    def get_voice_mute_poll(self, poll_id: int) -> VoiceMutePoll | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT p.id, p.guild_id, p.channel_id, p.message_id, p.target_id,
                       p.created_by, p.duration_days, p.reason, p.created_at,
                       p.ends_at, p.eligible_voters, p.required_votes, p.status,
                       COUNT(v.user_id)
                FROM voice_mute_polls AS p
                LEFT JOIN voice_mute_votes AS v ON v.poll_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (poll_id,),
            ).fetchone()
            return self._poll_from_row(row) if row else None

    def list_open_voice_mute_polls(self, now: int | None = None) -> list[VoiceMutePoll]:
        current = int(time.time()) if now is None else now
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT p.id, p.guild_id, p.channel_id, p.message_id, p.target_id,
                       p.created_by, p.duration_days, p.reason, p.created_at,
                       p.ends_at, p.eligible_voters, p.required_votes, p.status,
                       COUNT(v.user_id)
                FROM voice_mute_polls AS p
                LEFT JOIN voice_mute_votes AS v ON v.poll_id = p.id
                WHERE p.status = 'open' AND p.ends_at > ?
                GROUP BY p.id
                """,
                (current,),
            ).fetchall()
            return [self._poll_from_row(row) for row in rows]

    def cast_voice_mute_vote(self, poll_id: int, user_id: int) -> MuteVoteResult:
        now = int(time.time())
        with self._lock:
            row = self._conn.execute(
                """
                SELECT guild_id, target_id, created_by, duration_days, reason,
                       ends_at, required_votes, status
                FROM voice_mute_polls
                WHERE id = ?
                """,
                (poll_id,),
            ).fetchone()
            if not row:
                return MuteVoteResult("missing", 0, 0)

            guild_id, target_id, created_by, duration_days, reason, ends_at, required, status = row
            count = self._conn.execute(
                "SELECT COUNT(*) FROM voice_mute_votes WHERE poll_id = ?",
                (poll_id,),
            ).fetchone()[0]
            if status != "open":
                return MuteVoteResult(f"closed_{status}", count, required)
            if ends_at <= now:
                self._conn.execute(
                    "UPDATE voice_mute_polls SET status = 'expired', closed_at = ? WHERE id = ?",
                    (now, poll_id),
                )
                self._conn.commit()
                return MuteVoteResult("expired", count, required)

            try:
                self._conn.execute(
                    "INSERT INTO voice_mute_votes (poll_id, user_id, voted_at) VALUES (?, ?, ?)",
                    (poll_id, user_id, now),
                )
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return MuteVoteResult("already_voted", count, required)

            count += 1
            if count >= required:
                expires_at = now + (duration_days * 86400)
                self._conn.execute(
                    "UPDATE voice_mute_polls SET status = 'passed', closed_at = ? WHERE id = ?",
                    (now, poll_id),
                )
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO active_voice_mutes (
                        guild_id, user_id, poll_id, muted_by, reason, started_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, target_id, poll_id, created_by, reason, now, expires_at),
                )
                result_status = "passed"
            else:
                result_status = "accepted"
            self._conn.commit()
            return MuteVoteResult(result_status, count, required)

    def expire_voice_mute_polls(self, now: int | None = None) -> list[int]:
        current = int(time.time()) if now is None else now
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM voice_mute_polls WHERE status = 'open' AND ends_at <= ?",
                (current,),
            ).fetchall()
            poll_ids = [row[0] for row in rows]
            if poll_ids:
                self._conn.executemany(
                    "UPDATE voice_mute_polls SET status = 'expired', closed_at = ? WHERE id = ?",
                    [(current, poll_id) for poll_id in poll_ids],
                )
                self._conn.commit()
            return poll_ids

    def cancel_voice_mute_poll(self, poll_id: int) -> bool:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE voice_mute_polls SET status = 'cancelled', closed_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (now, poll_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def cancel_voice_mute_polls_for_target(self, guild_id: int, user_id: int) -> list[int]:
        now = int(time.time())
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id FROM voice_mute_polls
                WHERE guild_id = ? AND target_id = ? AND status = 'open'
                """,
                (guild_id, user_id),
            ).fetchall()
            poll_ids = [row[0] for row in rows]
            if poll_ids:
                self._conn.executemany(
                    "UPDATE voice_mute_polls SET status = 'cancelled', closed_at = ? WHERE id = ?",
                    [(now, poll_id) for poll_id in poll_ids],
                )
                self._conn.commit()
            return poll_ids

    def get_active_voice_mute(self, guild_id: int, user_id: int) -> ActiveVoiceMute | None:
        now = int(time.time())
        with self._lock:
            row = self._conn.execute(
                """
                SELECT guild_id, user_id, poll_id, muted_by, reason, started_at, expires_at
                FROM active_voice_mutes
                WHERE guild_id = ? AND user_id = ? AND expires_at > ?
                """,
                (guild_id, user_id, now),
            ).fetchone()
            return self._mute_from_row(row) if row else None

    def list_expired_voice_mutes(self, now: int | None = None) -> list[ActiveVoiceMute]:
        current = int(time.time()) if now is None else now
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT guild_id, user_id, poll_id, muted_by, reason, started_at, expires_at
                FROM active_voice_mutes
                WHERE expires_at <= ?
                """,
                (current,),
            ).fetchall()
            return [self._mute_from_row(row) for row in rows]

    def list_active_voice_mutes(self, now: int | None = None) -> list[ActiveVoiceMute]:
        current = int(time.time()) if now is None else now
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT guild_id, user_id, poll_id, muted_by, reason, started_at, expires_at
                FROM active_voice_mutes
                WHERE expires_at > ?
                """,
                (current,),
            ).fetchall()
            return [self._mute_from_row(row) for row in rows]

    def remove_voice_mute(self, guild_id: int, user_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM active_voice_mutes WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def remove_expired_voice_mute(
        self, guild_id: int, user_id: int, expected_expires_at: int
    ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                DELETE FROM active_voice_mutes
                WHERE guild_id = ? AND user_id = ? AND expires_at = ?
                """,
                (guild_id, user_id, expected_expires_at),
            )
            self._conn.commit()
            return cur.rowcount > 0
