import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app import ChatServices
from server.config import AppConfig
from webchat.adapters.northstar import NorthstarConfig
from webchat.harness.render import DeclarativeRenderer
from webchat.harness.turn import TurnResult
from webchat.persistence import SQLiteConversationStore


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def app_config(database_path: Path, **changes: Any) -> AppConfig:
    values = {
        "environment": "test",
        "provider": "openai",
        "model": "fixture-conversation-model",
        "openai_api_key": "provider-secret",
        "northstar": NorthstarConfig(
            "http://dealership-platform:4010", "dealer-secret"
        ),
        "database_path": database_path,
        "allowed_origin": "http://localhost:4173",
        "retention_days": 7,
        "provider_timeout_seconds": 20.0,
        "database_timeout_seconds": 5.0,
        "max_body_bytes": 2_048,
        "max_message_chars": 200,
    }
    values.update(changes)
    return AppConfig(**values)


class FakeRuntime:
    def __init__(self) -> None:
        self.requests = []
        self.failure: Exception | None = None
        self.delay_seconds = 0.0
        self.active = 0
        self.max_active = 0
        self.stream_deltas: tuple[str, ...] = ()
        self.renderer = DeclarativeRenderer(id_factory=lambda: "server-action")

    async def handle(self, request, *, on_text_delta=None):
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if self.failure is not None:
                raise self.failure
            if on_text_delta is not None:
                for delta in self.stream_deltas:
                    await on_text_delta(delta)
            state = request.state
            if request.page_observation is not None:
                state = replace(state, context=request.page_observation)
            return TurnResult(
                state=state,
                blocks=(self.renderer.text("".join(self.stream_deltas) or "A safe response."),),
                model_calls=1,
                input_tokens=10,
                output_tokens=5,
                provider_latency_ms=3.5,
            )
        finally:
            self.active -= 1


def services(database_path: Path, runtime: FakeRuntime | None = None) -> tuple[ChatServices, FakeRuntime]:
    selected_runtime = runtime or FakeRuntime()
    return (
        ChatServices(
            runtime=selected_runtime,
            store=SQLiteConversationStore(database_path),
        ),
        selected_runtime,
    )
