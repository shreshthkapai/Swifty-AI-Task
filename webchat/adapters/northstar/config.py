"""Validated server-side configuration for the Northstar integration."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class NorthstarConfig:
    """Northstar connection, locale, retry, and stable-cache settings."""

    base_url: str
    api_key: str = field(repr=False)
    currency: str = "GBP"
    timezone: str = "Europe/London"
    country: str = "United Kingdom"
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 5.0
    write_timeout_seconds: float = 5.0
    pool_timeout_seconds: float = 2.0
    retry_delays_seconds: tuple[float, ...] = (0.1, 0.25)
    location_cache_ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str):
            raise ValueError("base_url must be an HTTP URL")
        normalized_url = self.base_url.rstrip("/")
        parsed = urlsplit(normalized_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("base_url must be an HTTP origin without credentials or a path")
        object.__setattr__(self, "base_url", normalized_url)

        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("api_key must be a non-empty server-side secret")
        object.__setattr__(self, "api_key", self.api_key.strip())

        for field in (
            "currency",
            "timezone",
            "country",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
            object.__setattr__(self, field, value.strip())

        for field in (
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "write_timeout_seconds",
            "pool_timeout_seconds",
            "location_cache_ttl_seconds",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{field} must be positive")
            object.__setattr__(self, field, float(value))

        if not isinstance(self.retry_delays_seconds, tuple):
            raise ValueError("retry_delays_seconds must be a tuple")
        delays = tuple(float(delay) for delay in self.retry_delays_seconds)
        if any(delay < 0 for delay in delays):
            raise ValueError("retry delays must be non-negative")
        object.__setattr__(self, "retry_delays_seconds", delays)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "NorthstarConfig":
        """Build production configuration without exposing secrets to callers."""
        values = os.environ if environ is None else environ
        api_key = values.get("NORTHSTAR_API_KEY")
        if api_key is None or not api_key.strip():
            raise ValueError("NORTHSTAR_API_KEY must be configured server-side")
        return cls(
            base_url=values.get("NORTHSTAR_BASE_URL", "http://localhost:4010"),
            api_key=api_key,
        )
