"""Startup-only composition of concrete chat service dependencies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

import httpx

from webchat.adapters.northstar import NorthstarAdapter, NorthstarClient
from webchat.adapters.northstar.adapter import (
    operation_retry_count,
    reset_operation_retry_count,
)
from webchat.adapters.northstar.validation import customer_phone_is_valid
from webchat.harness.policy import PolicyEngine
from webchat.harness.planning import TurnRequest
from webchat.harness.runtime import TurnResult
from webchat.harness.conversation_runtime import ConversationRuntime
from webchat.persistence import ConversationStore, SQLiteConversationStore
from webchat.providers.openai import (
    OpenAIConversationProvider,
    OpenAIProviderConfig,
)

from .config import AppConfig
from .observability import (
    JsonEventLogger,
    ObservedConversationProvider,
    ObservedDealerAdapter,
)


@dataclass(slots=True)
class ChatServices:
    runtime: "TurnRuntime"
    store: ConversationStore
    close_callbacks: tuple[Callable[[], Awaitable[None]], ...] = ()

    async def aclose(self) -> None:
        for close in reversed(self.close_callbacks):
            await close()


class TurnRuntime(Protocol):
    async def handle(self, request: TurnRequest) -> TurnResult: ...


def build_services(
    config: AppConfig,
    *,
    logger: JsonEventLogger | None = None,
) -> ChatServices:
    """Compose concrete integrations at process startup only."""
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteConversationStore(
        config.database_path,
        retention=timedelta(days=config.retention_days),
        timeout_seconds=config.database_timeout_seconds,
    )
    dealer_http = httpx.AsyncClient(base_url=config.northstar.base_url)
    northstar = NorthstarAdapter(NorthstarClient(config.northstar, dealer_http))
    provider_http = httpx.AsyncClient()
    provider_config = OpenAIProviderConfig(
        api_key=config.openai_api_key,
        model=config.model,
        base_url=config.openai_base_url,
        timeout_seconds=config.provider_timeout_seconds,
    )
    openai = OpenAIConversationProvider(provider_http, provider_config)
    selected_logger = logger or JsonEventLogger()
    dealer = ObservedDealerAdapter(
        northstar,
        logger=selected_logger,
        retry_reset=reset_operation_retry_count,
        retry_count=operation_retry_count,
    )
    provider = ObservedConversationProvider(openai, logger=selected_logger)
    runtime = ConversationRuntime(
        dealer=dealer,
        conversation=provider,
        policy=PolicyEngine(phone_validator=customer_phone_is_valid),
    )
    return ChatServices(
        runtime=runtime,
        store=store,
        close_callbacks=(provider_http.aclose, northstar.aclose),
    )
