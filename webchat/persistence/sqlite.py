"""SQLite conversation store with atomic turns and optimistic revisions."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import math
from pathlib import Path
import sqlite3
from typing import Iterator

from webchat.domain.common import require_aware, require_non_empty
from webchat.harness.state import (
    CONVERSATION_STATE_SCHEMA_VERSION,
    STRUCTURED_MESSAGE_SCHEMA_VERSION,
    ConversationState,
    MessageRole,
    StructuredMessage,
)

from .base import (
    CommitResult,
    ConversationNotFoundError,
    ConversationRecord,
    PersistenceError,
    RevisionConflictError,
    UnsupportedSchemaError,
)


DATABASE_SCHEMA_VERSION = 1
DEFAULT_CONVERSATION_RETENTION = timedelta(days=7)


def _datetime_to_text(value: datetime, field_name: str) -> str:
    require_aware(value, field_name)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime_from_text(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise PersistenceError(f"stored {field_name} is not an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PersistenceError(f"stored {field_name} is not an ISO datetime string") from exc
    try:
        return require_aware(parsed, field_name)
    except ValueError as exc:
        raise PersistenceError(f"stored {field_name} is not timezone-aware") from exc


class SQLiteConversationStore:
    def __init__(
        self,
        database_path: str | Path,
        *,
        retention: timedelta = DEFAULT_CONVERSATION_RETENTION,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(database_path, (str, Path)):
            raise ValueError("database_path must be a path")
        self._database_path = Path(database_path)
        if not isinstance(retention, timedelta) or retention <= timedelta(0):
            raise ValueError("retention must be a positive timedelta")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        self._retention = retention
        self._timeout_seconds = float(timeout_seconds)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=self._timeout_seconds,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                f"PRAGMA busy_timeout = {int(self._timeout_seconds * 1000)}"
            )
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def _managed_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        try:
            with self._managed_connection() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > DATABASE_SCHEMA_VERSION:
                    raise UnsupportedSchemaError(
                        f"unsupported database schema version {version}; "
                        f"maximum is {DATABASE_SCHEMA_VERSION}"
                    )
                if version == 0:
                    self._migrate_from_zero(connection)
                elif version != DATABASE_SCHEMA_VERSION:
                    raise UnsupportedSchemaError(
                        f"unsupported database schema version {version}"
                    )
                connection.execute("PRAGMA journal_mode = WAL")
        except UnsupportedSchemaError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("failed to initialize conversation database") from exc

    @staticmethod
    def _migrate_from_zero(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL CHECK (revision >= 0),
                state_schema_version INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE turns (
                conversation_id TEXT NOT NULL,
                client_turn_id TEXT NOT NULL,
                committed_revision INTEGER NOT NULL CHECK (committed_revision > 0),
                created_at TEXT NOT NULL,
                PRIMARY KEY (conversation_id, client_turn_id),
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id) ON DELETE CASCADE
            );

            CREATE TABLE messages (
                conversation_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                client_turn_id TEXT NOT NULL,
                message_ordinal INTEGER NOT NULL CHECK (message_ordinal >= 0),
                message_schema_version INTEGER NOT NULL,
                message_json TEXT NOT NULL,
                PRIMARY KEY (conversation_id, message_id),
                UNIQUE (conversation_id, message_ordinal),
                FOREIGN KEY (conversation_id, client_turn_id)
                    REFERENCES turns(conversation_id, client_turn_id) ON DELETE CASCADE
            );

            CREATE INDEX conversations_expiry_idx ON conversations(expires_at);
            CREATE INDEX messages_turn_idx
                ON messages(conversation_id, client_turn_id, message_ordinal);

            PRAGMA user_version = 1;
            """
        )

    def load_or_create(
        self,
        conversation_id: str,
        *,
        now: datetime,
    ) -> ConversationRecord:
        clean_id = require_non_empty(conversation_id, "conversation_id")
        require_aware(now, "now")
        expires_at = now + self._retention
        try:
            with self._managed_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT expires_at FROM conversations WHERE conversation_id = ?",
                    (clean_id,),
                ).fetchone()
                if row is not None and _datetime_from_text(row["expires_at"], "expires_at") <= now:
                    connection.execute(
                        "DELETE FROM conversations WHERE conversation_id = ?",
                        (clean_id,),
                    )
                    row = None
                if row is None:
                    state = ConversationState()
                    connection.execute(
                        """
                        INSERT INTO conversations (
                            conversation_id, revision, state_schema_version,
                            state_json, created_at, last_activity_at, expires_at
                        ) VALUES (?, 0, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean_id,
                            state.schema_version,
                            state.to_json(),
                            _datetime_to_text(now, "now"),
                            _datetime_to_text(now, "now"),
                            _datetime_to_text(expires_at, "expires_at"),
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE conversations
                        SET last_activity_at = ?, expires_at = ?
                        WHERE conversation_id = ?
                        """,
                        (
                            _datetime_to_text(now, "now"),
                            _datetime_to_text(expires_at, "expires_at"),
                            clean_id,
                        ),
                    )
                record = self._read_record(connection, clean_id, now=now)
                if record is None:
                    raise PersistenceError("conversation disappeared during creation")
                return record
        except PersistenceError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("failed to load or create conversation") from exc

    def load(
        self,
        conversation_id: str,
        *,
        now: datetime,
    ) -> ConversationRecord | None:
        clean_id = require_non_empty(conversation_id, "conversation_id")
        require_aware(now, "now")
        try:
            with self._managed_connection() as connection:
                return self._read_record(connection, clean_id, now=now)
        except PersistenceError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("failed to load conversation") from exc

    def commit(
        self,
        conversation_id: str,
        *,
        expected_revision: int,
        messages: tuple[StructuredMessage, ...],
        state: ConversationState,
        now: datetime,
    ) -> CommitResult:
        clean_id = require_non_empty(conversation_id, "conversation_id")
        require_aware(now, "now")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if not isinstance(messages, tuple) or not messages:
            raise ValueError("messages must be a non-empty tuple")
        if not all(isinstance(message, StructuredMessage) for message in messages):
            raise ValueError("messages must contain StructuredMessage values")
        if not isinstance(state, ConversationState):
            raise ValueError("state must be a ConversationState")
        client_turn_ids = {message.client_turn_id for message in messages}
        if len(client_turn_ids) != 1:
            raise ValueError("messages in one commit must share one client_turn_id")
        if (
            messages[0].role is not MessageRole.USER
            or sum(message.role is MessageRole.USER for message in messages) != 1
        ):
            raise ValueError("messages must contain one leading user message")
        if any(
            earlier.created_at > later.created_at
            for earlier, later in zip(messages, messages[1:])
        ):
            raise ValueError("messages must be in timestamp order")
        message_ids = [message.message_id for message in messages]
        if len(set(message_ids)) != len(message_ids):
            raise ValueError("message IDs must be unique within a commit")
        client_turn_id = next(iter(client_turn_ids))
        serialized_messages = tuple(message.to_json() for message in messages)
        serialized_state = state.to_json()
        expires_at = now + self._retention

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision, expires_at FROM conversations WHERE conversation_id = ?",
                (clean_id,),
            ).fetchone()
            if row is None or _datetime_from_text(row["expires_at"], "expires_at") <= now:
                raise ConversationNotFoundError(
                    f"conversation {clean_id!r} does not exist or has expired"
                )
            duplicate_row = connection.execute(
                """
                SELECT committed_revision FROM turns
                WHERE conversation_id = ? AND client_turn_id = ?
                """,
                (clean_id, client_turn_id),
            ).fetchone()
            if duplicate_row is not None:
                connection.execute(
                    """
                    UPDATE conversations
                    SET last_activity_at = ?, expires_at = ?
                    WHERE conversation_id = ?
                    """,
                    (
                        _datetime_to_text(now, "now"),
                        _datetime_to_text(expires_at, "expires_at"),
                        clean_id,
                    ),
                )
                committed_messages = self._read_turn_messages(
                    connection,
                    clean_id,
                    client_turn_id,
                )
                record = self._read_record(connection, clean_id, now=now)
                connection.commit()
                if record is None:
                    raise PersistenceError("duplicate turn belongs to an expired conversation")
                return CommitResult(
                    record=record,
                    committed_messages=committed_messages,
                    duplicate=True,
                )
            actual_revision = row["revision"]
            if actual_revision != expected_revision:
                raise RevisionConflictError(
                    f"conversation revision conflict: expected {expected_revision}, "
                    f"actual {actual_revision}"
                )
            new_revision = actual_revision + 1
            connection.execute(
                """
                INSERT INTO turns (
                    conversation_id, client_turn_id, committed_revision, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    clean_id,
                    client_turn_id,
                    new_revision,
                    _datetime_to_text(now, "now"),
                ),
            )
            ordinal_row = connection.execute(
                """
                SELECT COALESCE(MAX(message_ordinal), -1) + 1 AS next_ordinal
                FROM messages WHERE conversation_id = ?
                """,
                (clean_id,),
            ).fetchone()
            next_ordinal = ordinal_row["next_ordinal"]
            serialized_pairs = zip(messages, serialized_messages, strict=True)
            for offset, (message, payload) in enumerate(serialized_pairs):
                connection.execute(
                    """
                    INSERT INTO messages (
                        conversation_id, message_id, client_turn_id,
                        message_ordinal, message_schema_version,
                        message_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_id,
                        message.message_id,
                        client_turn_id,
                        next_ordinal + offset,
                        message.schema_version,
                        payload,
                    ),
                )
            connection.execute(
                """
                UPDATE conversations
                SET revision = ?, state_schema_version = ?, state_json = ?,
                    last_activity_at = ?, expires_at = ?
                WHERE conversation_id = ?
                """,
                (
                    new_revision,
                    state.schema_version,
                    serialized_state,
                    _datetime_to_text(now, "now"),
                    _datetime_to_text(expires_at, "expires_at"),
                    clean_id,
                ),
            )
            record = self._read_record(connection, clean_id, now=now)
            if record is None:
                raise PersistenceError("conversation disappeared during commit")
            connection.commit()
            return CommitResult(
                record=record,
                committed_messages=messages,
                duplicate=False,
            )
        except (ConversationNotFoundError, RevisionConflictError, UnsupportedSchemaError):
            connection.rollback()
            raise
        except PersistenceError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PersistenceError("failed to commit conversation turn") from exc
        finally:
            connection.close()

    def delete(self, conversation_id: str) -> bool:
        clean_id = require_non_empty(conversation_id, "conversation_id")
        try:
            with self._managed_connection() as connection:
                cursor = connection.execute(
                    "DELETE FROM conversations WHERE conversation_id = ?",
                    (clean_id,),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise PersistenceError("failed to delete conversation") from exc

    def purge_expired(self, *, now: datetime) -> int:
        require_aware(now, "now")
        try:
            with self._managed_connection() as connection:
                cursor = connection.execute(
                    "DELETE FROM conversations WHERE expires_at <= ?",
                    (_datetime_to_text(now, "now"),),
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise PersistenceError("failed to purge expired conversations") from exc

    def _read_record(
        self,
        connection: sqlite3.Connection,
        conversation_id: str,
        *,
        now: datetime,
    ) -> ConversationRecord | None:
        row = connection.execute(
            """
            SELECT conversation_id, revision, state_schema_version, state_json,
                   created_at, last_activity_at, expires_at
            FROM conversations WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        expires_at = _datetime_from_text(row["expires_at"], "expires_at")
        if expires_at <= now:
            return None
        state_version = row["state_schema_version"]
        if state_version != CONVERSATION_STATE_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"unsupported state schema version {state_version}; "
                f"expected {CONVERSATION_STATE_SCHEMA_VERSION}"
            )
        try:
            state = ConversationState.from_json(row["state_json"])
        except ValueError as exc:
            raise PersistenceError("stored conversation state is invalid") from exc
        messages = self._read_messages(connection, conversation_id)
        return ConversationRecord(
            conversation_id=row["conversation_id"],
            revision=row["revision"],
            state=state,
            messages=messages,
            created_at=_datetime_from_text(row["created_at"], "created_at"),
            last_activity_at=_datetime_from_text(
                row["last_activity_at"],
                "last_activity_at",
            ),
            expires_at=expires_at,
        )

    def _read_messages(
        self,
        connection: sqlite3.Connection,
        conversation_id: str,
    ) -> tuple[StructuredMessage, ...]:
        rows = connection.execute(
            """
            SELECT message_schema_version, message_json
            FROM messages
            WHERE conversation_id = ?
            ORDER BY message_ordinal
            """,
            (conversation_id,),
        ).fetchall()
        return self._parse_message_rows(rows)

    def _read_turn_messages(
        self,
        connection: sqlite3.Connection,
        conversation_id: str,
        client_turn_id: str,
    ) -> tuple[StructuredMessage, ...]:
        rows = connection.execute(
            """
            SELECT message_schema_version, message_json
            FROM messages
            WHERE conversation_id = ? AND client_turn_id = ?
            ORDER BY message_ordinal
            """,
            (conversation_id, client_turn_id),
        ).fetchall()
        return self._parse_message_rows(rows)

    @staticmethod
    def _parse_message_rows(
        rows: list[sqlite3.Row],
    ) -> tuple[StructuredMessage, ...]:
        messages: list[StructuredMessage] = []
        for row in rows:
            version = row["message_schema_version"]
            if version != STRUCTURED_MESSAGE_SCHEMA_VERSION:
                raise UnsupportedSchemaError(
                    f"unsupported message schema version {version}; "
                    f"expected {STRUCTURED_MESSAGE_SCHEMA_VERSION}"
                )
            try:
                messages.append(StructuredMessage.from_json(row["message_json"]))
            except ValueError as exc:
                raise PersistenceError("stored structured message is invalid") from exc
        return tuple(messages)
