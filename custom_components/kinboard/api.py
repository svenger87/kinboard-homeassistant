"""Thin client for the Kinboard Integration API.

Deliberately thin: it knows how to authenticate, how to raise the two errors
the config flow needs to distinguish, and nothing about Home Assistant. That
keeps it testable without a running HA instance, and it is the piece the Bridge
will need to reimplement in another language, so the surface stays small.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp
from yarl import URL

from .const import API_BASE_PATH

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


class KinboardError(Exception):
    """Base class for every failure this client raises."""


class KinboardAuthError(KinboardError):
    """The token was rejected, or lacks the scope for this call.

    Separate from KinboardConnectionError because the config flow must react
    differently: a bad token needs reauth, an unreachable host needs a retry.
    """


class KinboardConnectionError(KinboardError):
    """Kinboard could not be reached, or answered in a way we cannot parse."""


class KinboardVersionError(KinboardError):
    """Kinboard is reachable but too old to serve the Integration API."""


class KinboardClient:
    """Talks to one Kinboard instance with one integration token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str,
    ) -> None:
        self._session = session
        self._base = URL(base_url.rstrip("/"))
        self._token = token

    @property
    def base_url(self) -> str:
        return str(self._base)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        url = self._base.with_path(f"{API_BASE_PATH}{path}")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        # Every write carries an idempotency key. HA automations retry, and a
        # retried "add milk" must not add milk twice (RFC-001 section 3).
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        try:
            async with self._session.request(
                method, url, headers=headers, json=json, params=params,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    raise KinboardAuthError(
                        f"{method} {path} rejected with HTTP {response.status}"
                    )
                if response.status == 404:
                    # A 404 on the versioned base path means this Kinboard
                    # predates the Integration API, not that the item is
                    # missing. Distinguishing the two is what stops the user
                    # chasing a phantom configuration problem.
                    raise KinboardVersionError(
                        f"{path} not found — this Kinboard may be older than the "
                        "Integration API"
                    )
                response.raise_for_status()
                if response.content_type == "application/json":
                    return await response.json()
                return None
        except aiohttp.ClientResponseError as err:
            raise KinboardConnectionError(f"{method} {path} failed: {err}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise KinboardConnectionError(f"{method} {path} unreachable: {err}") from err

    # -- reads ------------------------------------------------------------

    async def async_get_info(self) -> dict[str, Any]:
        """Identify the instance and the token.

        Returns the Kinboard version, the family, and the scopes this token
        actually carries — which is what lets the config flow tell someone up
        front that their token cannot create tasks, rather than letting the
        service call fail later.
        """
        return await self._request("GET", "/info")

    async def async_get_summary(self) -> dict[str, Any]:
        """One call backing every sensor.

        Deliberately a single endpoint rather than one per entity: HA polls on
        a fixed interval, and eight requests where one would do is eight times
        the load on a self-hosted stack that may be a Raspberry Pi.
        """
        return await self._request("GET", "/family/summary")

    async def async_get_calendar_events(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Calendar events overlapping a window.

        Both bounds are sent explicitly because the server requires them — it
        refuses to guess, on the grounds that "today" and "everything" are both
        plausible defaults and differ enormously in cost.

        Not served from the summary: a CalendarEntity is asked for whatever
        window the user is looking at, which the summary never describes.
        """
        payload = await self._request(
            "GET",
            "/calendar/events",
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        return payload.get("events", []) if isinstance(payload, dict) else []

    async def async_get_events(self, after_id: int | None = None) -> list[dict[str, Any]]:
        """Fetch domain events after a cursor.

        The cursor is the monotonic domain_events id (RFC-001 section 6.2), not
        a timestamp — timestamps collide under concurrency and cannot be
        compared reliably.
        """
        params = {"after": after_id} if after_id is not None else None
        payload = await self._request("GET", "/events", params=params)
        return payload.get("events", []) if isinstance(payload, dict) else []

    # -- writes -----------------------------------------------------------

    async def async_call_service(
        self, service: str, data: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any] | None:
        """Invoke one of the services named in RFC-001 section 5.2."""
        return await self._request(
            "POST", f"/services/{service}", json=data,
            idempotency_key=idempotency_key,
        )
