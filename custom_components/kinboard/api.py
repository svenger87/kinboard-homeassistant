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


class KinboardRequestError(KinboardConnectionError):
    """Kinboard understood the request and refused it (HTTP 400).

    Carries the server's own explanation. "400 Bad Request" alone told nobody
    that the server wanted a field under a different name, which is exactly
    how add_pocket_money and dismiss_attention failed for their first months.

    A subclass of the connection error only so that every caller which already
    handled a 400 as one keeps doing so unchanged; the service handler is the
    one place that catches it separately.
    """


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
                if response.status == 400:
                    reason = None
                    try:
                        body = await response.json(content_type=None)
                        if isinstance(body, dict):
                            reason = body.get("error")
                    except (ValueError, aiohttp.ClientError):
                        pass
                    raise KinboardRequestError(
                        str(reason) if reason else f"{method} {path} rejected with HTTP 400"
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
        payload = await self._request("GET", "/family/summary")
        # The endpoint wraps the sensor values: {summary, generated_at, today,
        # tomorrow}. Entities read `shopping_items` and friends straight off
        # coordinator.data, so the envelope is unwrapped here rather than in
        # every entity. Returning the envelope left every sensor unavailable —
        # found on the first real setup, because nothing below this line has an
        # opinion about the shape.
        if isinstance(payload, dict) and isinstance(payload.get("summary"), dict):
            return payload["summary"]
        return payload if isinstance(payload, dict) else {}

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

    async def async_get_events(self, after_id: int | None = None) -> dict[str, Any]:
        """Fetch one page of domain events after a cursor.

        The cursor is the monotonic domain_events id (RFC-001 section 6.2), not
        a timestamp — timestamps collide under concurrency and cannot be
        compared reliably.

        Returns the envelope rather than only the rows, because `has_more`
        carries something the caller cannot reconstruct. The server caps a page
        at 100 events and sets the flag to mean "come back immediately rather
        than waiting for your next poll"; ignoring it meant a consumer that had
        been offline for a few hours caught up at 100 events per minute.
        """
        params = {"after": after_id} if after_id is not None else None
        payload = await self._request("GET", "/events", params=params)
        if not isinstance(payload, dict):
            return {"events": [], "has_more": False}
        return {
            "events": payload.get("events") or [],
            "has_more": bool(payload.get("has_more")),
        }

    # -- lists ------------------------------------------------------------

    async def async_get_list(self, list_id: str) -> list[dict[str, Any]]:
        """The items on a list, already in Home Assistant's shape."""
        payload = await self._request("GET", f"/lists/{list_id}")
        return payload.get("items", []) if isinstance(payload, dict) else []

    async def async_add_list_item(
        self, list_id: str, summary: str, due: str | None, idempotency_key: str
    ) -> dict[str, Any] | None:
        body: dict[str, Any] = {"summary": summary}
        if due is not None:
            body["due"] = due
        return await self._request(
            "POST", f"/lists/{list_id}", json=body, idempotency_key=idempotency_key
        )

    async def async_update_list_item(
        self, list_id: str, item_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        # No idempotency key: this addresses one row by id, so repeating it is
        # already harmless. The server does not ask for one.
        return await self._request("PATCH", f"/lists/{list_id}/{item_id}", json=patch)

    async def async_delete_list_item(self, list_id: str, item_id: str) -> None:
        await self._request("DELETE", f"/lists/{list_id}/{item_id}")

    # -- writes -----------------------------------------------------------

    async def async_call_service(
        self, service: str, data: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any] | None:
        """Invoke one of the services named in RFC-001 section 5.2."""
        return await self._request(
            "POST", f"/services/{service}", json=data,
            idempotency_key=idempotency_key,
        )
