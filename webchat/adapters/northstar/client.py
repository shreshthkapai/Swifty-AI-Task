"""Single-attempt asynchronous HTTP boundary for Northstar."""

from collections.abc import Mapping
from typing import Any

import httpx

from .config import NorthstarConfig
from .errors import invalid_request, invalid_response, map_platform_error, temporary_failure


class NorthstarClient:
    """Own an injected HTTP client and perform one strict request attempt."""

    __slots__ = ("_config", "_http", "_timeout")

    def __init__(self, config: NorthstarConfig, http: httpx.AsyncClient) -> None:
        if not isinstance(config, NorthstarConfig):
            raise TypeError("config must be a NorthstarConfig")
        if not isinstance(http, httpx.AsyncClient):
            raise TypeError("http must be an httpx.AsyncClient")
        if str(http.base_url).rstrip("/") != config.base_url:
            raise ValueError("http base URL must match Northstar configuration")
        self._config = config
        self._http = http
        self._timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.write_timeout_seconds,
            pool=config.pool_timeout_seconds,
        )

    @property
    def config(self) -> NorthstarConfig:
        return self._config

    async def __aenter__(self) -> "NorthstarClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        json: Mapping[str, Any] | None = None,
        protected: bool = False,
        idempotency_key: str | None = None,
        resource: str | None = None,
        field_map: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform exactly one Northstar request and return a JSON object."""
        if not isinstance(path, str) or not path.startswith("/api/") or "://" in path:
            raise invalid_request(resource=resource)
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 100
        ):
            raise invalid_request(resource=resource)

        headers = {"Accept": "application/json"}
        if protected:
            headers["X-API-Key"] = self._config.api_key
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = await self._http.request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise temporary_failure(resource=resource) from error

        try:
            payload = response.json()
        except ValueError as error:
            raise invalid_response(resource=resource) from error
        if not isinstance(payload, dict):
            raise invalid_response(resource=resource)
        if response.is_success:
            return payload
        raise map_platform_error(
            response.status_code,
            payload,
            resource=resource,
            field_map=field_map,
        )
