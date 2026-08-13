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
from webchat.harness.planning import PlanningEngine, TurnRequest
from webchat.harness.runtime import HarnessRuntime, TurnResult
from webchat.persistence import ConversationStore, SQLiteConversationStore
from webchat.providers.openai import OpenAIPlanningProvider, OpenAIProviderConfig

from .config import AppConfig
from .observability import (
    JsonEventLogger,
    ObservedDealerAdapter,
    ObservedPlanningProvider,
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
    openai = OpenAIPlanningProvider(
        provider_http,
        OpenAIProviderConfig(
            api_key=config.openai_api_key,
            model=config.model,
            base_url=config.openai_base_url,
            timeout_seconds=config.provider_timeout_seconds,
        ),
    )
    selected_logger = logger or JsonEventLogger()
    dealer = ObservedDealerAdapter(
        northstar,
        logger=selected_logger,
        retry_reset=reset_operation_retry_count,
        retry_count=operation_retry_count,
    )
    provider = ObservedPlanningProvider(openai, logger=selected_logger)
    runtime = HarnessRuntime(dealer=dealer, planning=PlanningEngine(provider))
    return ChatServices(
        runtime=runtime,
        store=store,
        close_callbacks=(provider_http.aclose, northstar.aclose),
    )
