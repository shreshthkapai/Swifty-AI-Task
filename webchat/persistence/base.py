"""Dealer- and database-independent conversation storage contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from webchat.harness.state import ConversationState, StructuredMessage


class PersistenceError(RuntimeError):
    """Base failure for conversation persistence operations."""


class ConversationNotFoundError(PersistenceError):
    """Raised when a commit targets a missing or expired conversation."""


class RevisionConflictError(PersistenceError):
    """Raised when optimistic concurrency detects a stale state revision."""


class UnsupportedSchemaError(PersistenceError):
    """Raised when stored data was written by an unsupported schema."""


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    conversation_id: str
    revision: int
    state: ConversationState
    messages: tuple[StructuredMessage, ...]
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CommitResult:
    record: ConversationRecord
    committed_messages: tuple[StructuredMessage, ...]
    duplicate: bool = False


@runtime_checkable
class ConversationStore(Protocol):
    def load_or_create(
        self,
        conversation_id: str,
        *,
        now: datetime,
    ) -> ConversationRecord: ...

    def load(
        self,
        conversation_id: str,
        *,
        now: datetime,
    ) -> ConversationRecord | None: ...

    def commit(
        self,
        conversation_id: str,
        *,
        expected_revision: int,
        messages: tuple[StructuredMessage, ...],
        state: ConversationState,
        now: datetime,
    ) -> CommitResult: ...

    def delete(self, conversation_id: str) -> bool: ...

    def purge_expired(self, *, now: datetime) -> int: ...
