import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
import math
from pathlib import Path

from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.state import (
    ConversationState,
    MessageRole,
    StructuredMessage,
    VerificationGrant,
)
from webchat.persistence.base import (
    ConversationStore,
    PersistenceError,
    RevisionConflictError,
    UnsupportedSchemaError,
)
from webchat.persistence.sqlite import SQLiteConversationStore


class SQLiteConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "chat.sqlite3"
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        self.store = SQLiteConversationStore(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_messages(
        self,
        turn_id: str,
        *,
        user_message_id: str | None = None,
    ) -> tuple[StructuredMessage, ...]:
        return (
            StructuredMessage(
                message_id=user_message_id or f"{turn_id}-user",
                client_turn_id=turn_id,
                role=MessageRole.USER,
                created_at=self.now,
                text="Show me an X3",
            ),
            StructuredMessage(
                message_id=f"{turn_id}-assistant",
                client_turn_id=turn_id,
                role=MessageRole.ASSISTANT,
                created_at=self.now + timedelta(seconds=1),
                text="I found an X3.",
            ),
        )

    def make_pending_state(self) -> ConversationState:
        action = PendingAction.from_mapping(
            action_id="action-1",
            action_type=PendingActionType.TEST_DRIVE_BOOKING,
            request_type=PendingRequestType.TEST_DRIVE_BOOKING,
            request_payload={"slot_id": "td-slot-1"},
            state=PendingActionState.AWAITING_CONFIRMATION,
            idempotency_key="idem-stable-1",
            created_at=self.now,
            expires_at=self.now + timedelta(minutes=10),
        )
        return ConversationState(
            pending_action=action,
            verification_grants=(
                VerificationGrant.issue("booking-1", now=self.now),
            ),
        )

    def test_store_implements_conversation_store_protocol(self) -> None:
        self.assertIsInstance(self.store, ConversationStore)

    def test_database_uses_wal_and_rejects_non_finite_timeout(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode, "wal")

        for timeout in (math.nan, math.inf, -math.inf):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                    SQLiteConversationStore(
                        self.database_path,
                        timeout_seconds=timeout,
                    )

    def test_restart_restores_messages_state_and_pending_action(self) -> None:
        created = self.store.load_or_create("conversation-1", now=self.now)
        state = self.make_pending_state()
        committed = self.store.commit(
            "conversation-1",
            expected_revision=created.revision,
            messages=self.make_messages("turn-1"),
            state=state,
            now=self.now + timedelta(seconds=2),
        )

        reopened = SQLiteConversationStore(self.database_path)
        restored = reopened.load(
            "conversation-1",
            now=self.now + timedelta(minutes=1),
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.revision, 1)
        self.assertEqual(restored.state, state)
        self.assertEqual(restored.messages, self.make_messages("turn-1"))
        self.assertEqual(
            restored.state.pending_action.idempotency_key,
            "idem-stable-1",
        )
        self.assertFalse(committed.duplicate)

    def test_revision_conflict_does_not_overwrite_newer_state(self) -> None:
        created = self.store.load_or_create("conversation-1", now=self.now)
        first_state = ConversationState()
        self.store.commit(
            "conversation-1",
            expected_revision=created.revision,
            messages=self.make_messages("turn-1"),
            state=first_state,
            now=self.now,
        )

        with self.assertRaisesRegex(RevisionConflictError, "expected 0.*actual 1"):
            self.store.commit(
                "conversation-1",
                expected_revision=0,
                messages=self.make_messages("turn-2"),
                state=self.make_pending_state(),
                now=self.now + timedelta(seconds=2),
            )

        restored = self.store.load("conversation-1", now=self.now)
        self.assertEqual(restored.revision, 1)
        self.assertEqual(restored.state, first_state)

    def test_duplicate_client_turn_returns_original_messages_without_new_revision(self) -> None:
        created = self.store.load_or_create("conversation-1", now=self.now)
        messages = self.make_messages("turn-1")
        first = self.store.commit(
            "conversation-1",
            expected_revision=created.revision,
            messages=messages,
            state=ConversationState(),
            now=self.now,
        )

        duplicate = self.store.commit(
            "conversation-1",
            expected_revision=0,
            messages=messages,
            state=self.make_pending_state(),
            now=self.now + timedelta(seconds=5),
        )

        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.committed_messages, messages)
        self.assertEqual(duplicate.record.revision, first.record.revision)
        self.assertEqual(duplicate.record.state, ConversationState())
        self.assertEqual(len(duplicate.record.messages), 2)

    def test_duplicate_client_turn_refreshes_inactivity_without_new_revision(self) -> None:
        created = self.store.load_or_create("conversation-1", now=self.now)
        messages = self.make_messages("turn-1")
        self.store.commit(
            "conversation-1",
            expected_revision=created.revision,
            messages=messages,
            state=ConversationState(),
            now=self.now,
        )

        duplicate = self.store.commit(
            "conversation-1",
            expected_revision=0,
            messages=messages,
            state=ConversationState(),
            now=self.now + timedelta(days=6),
        )

        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.record.revision, 1)
        self.assertEqual(duplicate.record.expires_at, self.now + timedelta(days=13))
        self.assertIsNotNone(
            self.store.load("conversation-1", now=self.now + timedelta(days=8))
        )

    def test_failed_commit_rolls_back_turn_messages_state_and_revision(self) -> None:
        created = self.store.load_or_create("conversation-1", now=self.now)
        self.store.commit(
            "conversation-1",
            expected_revision=created.revision,
            messages=self.make_messages("turn-1"),
            state=ConversationState(),
            now=self.now,
        )

        with self.assertRaises(PersistenceError):
            self.store.commit(
                "conversation-1",
                expected_revision=1,
                messages=self.make_messages(
                    "turn-2",
                    user_message_id="turn-1-user",
                ),
                state=self.make_pending_state(),
                now=self.now + timedelta(seconds=5),
            )

        after_failure = self.store.load("conversation-1", now=self.now)
        self.assertEqual(after_failure.revision, 1)
        self.assertEqual(after_failure.state, ConversationState())
        self.assertEqual(len(after_failure.messages), 2)

        retry = self.store.commit(
            "conversation-1",
            expected_revision=1,
            messages=self.make_messages("turn-2"),
            state=self.make_pending_state(),
            now=self.now + timedelta(seconds=6),
        )
        self.assertEqual(retry.record.revision, 2)

    def test_expired_verification_grant_restores_but_does_not_authorize(self) -> None:
        created = self.store.load_or_create("conversation-1", now=self.now)
        self.store.commit(
            "conversation-1",
            expected_revision=created.revision,
            messages=self.make_messages("turn-1"),
            state=self.make_pending_state(),
            now=self.now,
        )

        restored = self.store.load(
            "conversation-1",
            now=self.now + timedelta(minutes=16),
        )

        self.assertFalse(
            restored.state.has_active_grant(
                "booking-1",
                now=self.now + timedelta(minutes=16),
            )
        )

    def test_new_database_is_migrated_and_future_database_schema_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 1)

        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA user_version = 99")
            connection.commit()

        with self.assertRaisesRegex(UnsupportedSchemaError, "database schema version 99"):
            SQLiteConversationStore(self.database_path)

    def test_unknown_persisted_state_schema_is_rejected_on_load(self) -> None:
        self.store.load_or_create("conversation-1", now=self.now)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE conversations SET state_schema_version = 99 "
                "WHERE conversation_id = ?",
                ("conversation-1",),
            )
            connection.commit()

        with self.assertRaisesRegex(UnsupportedSchemaError, "state schema version 99"):
            self.store.load("conversation-1", now=self.now)

    def test_unknown_persisted_message_schema_is_rejected_on_load(self) -> None:
        created = self.store.load_or_create("conversation-1", now=self.now)
        self.store.commit(
            "conversation-1",
            expected_revision=created.revision,
            messages=self.make_messages("turn-1"),
            state=ConversationState(),
            now=self.now,
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE messages SET message_schema_version = 99 "
                "WHERE conversation_id = ?",
                ("conversation-1",),
            )
            connection.commit()

        with self.assertRaisesRegex(UnsupportedSchemaError, "message schema version 99"):
            self.store.load("conversation-1", now=self.now)

    def test_inactivity_retention_is_configurable_and_purged_explicitly(self) -> None:
        store = SQLiteConversationStore(
            self.database_path,
            retention=timedelta(hours=2),
        )
        store.load_or_create("expired", now=self.now)
        store.load_or_create("active", now=self.now + timedelta(hours=1))

        purged = store.purge_expired(now=self.now + timedelta(hours=2, minutes=1))

        self.assertEqual(purged, 1)
        self.assertIsNone(store.load("expired", now=self.now + timedelta(hours=2, minutes=1)))
        self.assertIsNotNone(store.load("active", now=self.now + timedelta(hours=2, minutes=1)))

    def test_load_or_create_replaces_an_expired_conversation(self) -> None:
        store = SQLiteConversationStore(
            self.database_path,
            retention=timedelta(minutes=5),
        )
        original = store.load_or_create("conversation-1", now=self.now)

        replacement = store.load_or_create(
            "conversation-1",
            now=self.now + timedelta(minutes=6),
        )

        self.assertEqual(original.revision, 0)
        self.assertEqual(replacement.revision, 0)
        self.assertEqual(replacement.created_at, self.now + timedelta(minutes=6))

    def test_load_or_create_refreshes_inactivity_expiry_without_state_revision(self) -> None:
        store = SQLiteConversationStore(
            self.database_path,
            retention=timedelta(hours=2),
        )
        original = store.load_or_create("conversation-1", now=self.now)

        restored = store.load_or_create(
            "conversation-1",
            now=self.now + timedelta(hours=1),
        )

        self.assertEqual(restored.revision, original.revision)
        self.assertEqual(restored.created_at, original.created_at)
        self.assertEqual(restored.last_activity_at, self.now + timedelta(hours=1))
        self.assertEqual(restored.expires_at, self.now + timedelta(hours=3))

    def test_purge_compares_canonical_instants_not_timezone_text(self) -> None:
        store = SQLiteConversationStore(
            self.database_path,
            retention=timedelta(hours=2),
        )
        offset_now = self.now.astimezone(timezone(timedelta(hours=2)))
        store.load_or_create("conversation-1", now=offset_now)

        purged = store.purge_expired(now=self.now + timedelta(hours=2, seconds=1))

        self.assertEqual(purged, 1)

    def test_commit_requires_exactly_one_user_message_at_start_of_turn(self) -> None:
        self.store.load_or_create("conversation-1", now=self.now)
        assistant_only = (
            StructuredMessage(
                message_id="assistant-only",
                client_turn_id="turn-1",
                role=MessageRole.ASSISTANT,
                created_at=self.now,
                text="Hello",
            ),
        )

        with self.assertRaisesRegex(ValueError, "one leading user message"):
            self.store.commit(
                "conversation-1",
                expected_revision=0,
                messages=assistant_only,
                state=ConversationState(),
                now=self.now,
            )

    def test_commit_rejects_messages_out_of_timestamp_order(self) -> None:
        self.store.load_or_create("conversation-1", now=self.now)
        messages = list(self.make_messages("turn-1"))
        messages[1] = StructuredMessage(
            message_id="turn-1-assistant",
            client_turn_id="turn-1",
            role=MessageRole.ASSISTANT,
            created_at=self.now - timedelta(seconds=1),
            text="I found an X3.",
        )

        with self.assertRaisesRegex(ValueError, "timestamp order"):
            self.store.commit(
                "conversation-1",
                expected_revision=0,
                messages=tuple(messages),
                state=ConversationState(),
                now=self.now,
            )

    def test_delete_cascades_messages_and_allows_clean_recreation(self) -> None:
        created = self.store.load_or_create("conversation-1", now=self.now)
        self.store.commit(
            "conversation-1",
            expected_revision=created.revision,
            messages=self.make_messages("turn-1"),
            state=ConversationState(),
            now=self.now,
        )

        self.assertTrue(self.store.delete("conversation-1"))
        self.assertIsNone(self.store.load("conversation-1", now=self.now))
        recreated = self.store.load_or_create("conversation-1", now=self.now)
        self.assertEqual(recreated.messages, ())

    def test_commit_requires_one_client_turn_and_unique_message_ids(self) -> None:
        self.store.load_or_create("conversation-1", now=self.now)
        mismatched = (
            self.make_messages("turn-1")[0],
            self.make_messages("turn-2")[1],
        )
        with self.assertRaisesRegex(ValueError, "one client_turn_id"):
            self.store.commit(
                "conversation-1",
                expected_revision=0,
                messages=mismatched,
                state=ConversationState(),
                now=self.now,
            )


if __name__ == "__main__":
    unittest.main()
