"""Validated process configuration for the chat HTTP application."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from webchat.adapters.northstar.config import NorthstarConfig
from webchat.providers.openai import DEFAULT_OPENAI_BASE_URL


LOCAL_ENVIRONMENTS = frozenset({"development", "local", "test"})


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} must be configured server-side")
    return value.strip()


def _positive_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def _positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _http_origin(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{name} must be an HTTP(S) origin without credentials or a path")
    return value.rstrip("/")


def _http_base_url(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{name} must be an HTTP(S) base URL without credentials")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: str
    provider: str
    response_model: str
    planner_model: str
    openai_api_key: str = field(repr=False)
    northstar: NorthstarConfig = field(repr=False)
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    database_path: Path = Path("data/webchat.sqlite3")
    allowed_origin: str = "http://localhost:4173"
    retention_days: int = 7
    provider_timeout_seconds: float = 30.0
    database_timeout_seconds: float = 5.0
    max_body_bytes: int = 16_384
    max_message_chars: int = 4_000

    def __post_init__(self) -> None:
        if self.environment not in LOCAL_ENVIRONMENTS | {"production"}:
            raise ValueError("CHAT_ENVIRONMENT must be local, development, test, or production")
        if self.provider != "openai":
            raise ValueError("CHAT_PROVIDER must name a configured planning provider")
        for name in ("response_model", "planner_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.openai_api_key, str) or not self.openai_api_key.strip():
            raise ValueError("openai_api_key must be configured server-side")
        if not isinstance(self.northstar, NorthstarConfig):
            raise ValueError("northstar must be a NorthstarConfig")
        if not isinstance(self.database_path, Path):
            raise ValueError("database_path must be a Path")
        for name in ("retention_days", "max_body_bytes", "max_message_chars"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("provider_timeout_seconds", "database_timeout_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")
            object.__setattr__(self, name, float(value))
        object.__setattr__(
            self, "openai_base_url", _http_base_url(self.openai_base_url, "OPENAI_BASE_URL")
        )
        object.__setattr__(self, "allowed_origin", _http_origin(self.allowed_origin, "CHAT_ALLOWED_ORIGIN"))
        if self.environment == "production" and not self.allowed_origin.startswith("https://"):
            raise ValueError("CHAT_ALLOWED_ORIGIN must use HTTPS in production")

    @property
    def cookie_secure(self) -> bool:
        return self.environment not in LOCAL_ENVIRONMENTS

    @property
    def retention_seconds(self) -> int:
        return self.retention_days * 24 * 60 * 60

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AppConfig":
        values = os.environ if environ is None else environ
        provider = values.get("CHAT_PROVIDER", "openai").strip().lower()
        if provider != "openai":
            raise ValueError("CHAT_PROVIDER must name a configured planning provider")
        environment = values.get("CHAT_ENVIRONMENT", "local").strip().lower()
        retention_days = _positive_int(values, "CHAT_RETENTION_DAYS", 7)
        provider_timeout = _positive_float(values, "CHAT_PROVIDER_TIMEOUT_SECONDS", 30.0)
        database_timeout = _positive_float(values, "CHAT_DATABASE_TIMEOUT_SECONDS", 5.0)
        max_body = _positive_int(values, "CHAT_MAX_BODY_BYTES", 16_384)
        max_message = _positive_int(values, "CHAT_MAX_MESSAGE_CHARS", 4_000)
        northstar = NorthstarConfig(
            base_url=values.get("NORTHSTAR_BASE_URL", "http://localhost:4010"),
            api_key=_required(values, "NORTHSTAR_API_KEY"),
            connect_timeout_seconds=_positive_float(
                values, "NORTHSTAR_CONNECT_TIMEOUT_SECONDS", 2.0
            ),
            read_timeout_seconds=_positive_float(
                values, "NORTHSTAR_READ_TIMEOUT_SECONDS", 5.0
            ),
            write_timeout_seconds=_positive_float(
                values, "NORTHSTAR_WRITE_TIMEOUT_SECONDS", 5.0
            ),
            pool_timeout_seconds=_positive_float(
                values, "NORTHSTAR_POOL_TIMEOUT_SECONDS", 2.0
            ),
        )
        response_model = _required(values, "CHAT_MODEL")
        planner_model = values.get("CHAT_PLANNER_MODEL", response_model).strip()
        if not planner_model:
            raise ValueError("CHAT_PLANNER_MODEL must be a non-empty string")
        return cls(
            environment=environment,
            provider=provider,
            response_model=response_model,
            planner_model=planner_model,
            openai_api_key=_required(values, "OPENAI_API_KEY"),
            openai_base_url=_http_base_url(
                values.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
                "OPENAI_BASE_URL",
            ),
            northstar=northstar,
            database_path=Path(values.get("CHAT_DATABASE_PATH", "data/webchat.sqlite3")),
            allowed_origin=_http_origin(
                values.get("CHAT_ALLOWED_ORIGIN", "http://localhost:4173"),
                "CHAT_ALLOWED_ORIGIN",
            ),
            retention_days=retention_days,
            provider_timeout_seconds=provider_timeout,
            database_timeout_seconds=database_timeout,
            max_body_bytes=max_body,
            max_message_chars=max_message,
        )
