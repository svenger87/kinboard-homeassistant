"""The wire format, covered once, here.

Every case below is a distinction the rest of the integration depends on and
cannot make for itself: which HTTP status means "your token is wrong" versus
"your Kinboard is too old", and what shape `async_get_summary` hands upwards.
"""

from __future__ import annotations

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.kinboard.api import (
    KinboardAuthError,
    KinboardClient,
    KinboardConnectionError,
    KinboardRequestError,
    KinboardVersionError,
)

BASE = "http://kinboard.test"
API = f"{BASE}/api/integration/v1"

# The client only decodes a body it was told is JSON, and the mocker does not
# infer the header from `json=`. Real Kinboard sends it; this makes the mock
# agree with reality rather than loosening the client to accept anything.
JSON = {"Content-Type": "application/json"}


def _client(hass) -> KinboardClient:
    return KinboardClient(async_get_clientsession(hass), BASE, "kbi_test")


async def test_summary_unwraps_the_envelope(hass, aioclient_mock):
    """The endpoint wraps its values; entities read them unwrapped.

    Returning the envelope left every sensor unavailable on the first real
    install, because nothing below this line has an opinion about the shape.
    """
    aioclient_mock.get(
        f"{API}/family/summary",
        json={
            "summary": {"shopping_items": 3},
            "generated_at": "2026-08-09T06:00:00+02:00",
        },
        headers=JSON,
    )
    assert await _client(hass).async_get_summary() == {"shopping_items": 3}


async def test_summary_tolerates_a_bare_payload(hass, aioclient_mock):
    """An unwrapped body still works, so a server change cannot blank the UI."""
    aioclient_mock.get(f"{API}/family/summary", json={"shopping_items": 1}, headers=JSON)
    assert await _client(hass).async_get_summary() == {"shopping_items": 1}


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_token_is_an_auth_error(hass, aioclient_mock, status):
    """Both statuses mean the same thing to a user: fix the token.

    They are distinct on the wire — 401 unauthenticated, 403 out of scope — but
    the remedy is identical, and the config flow needs one branch, not two.
    """
    aioclient_mock.get(f"{API}/info", status=status)
    with pytest.raises(KinboardAuthError):
        await _client(hass).async_get_info()


async def test_404_means_too_old_not_missing(hass, aioclient_mock):
    """A 404 on the versioned base path is a version problem.

    Reporting it as "not found" sends the user hunting for a configuration
    mistake that does not exist.
    """
    aioclient_mock.get(f"{API}/info", status=404)
    with pytest.raises(KinboardVersionError):
        await _client(hass).async_get_info()


async def test_server_error_is_a_connection_error(hass, aioclient_mock):
    aioclient_mock.get(f"{API}/info", status=500)
    with pytest.raises(KinboardConnectionError):
        await _client(hass).async_get_info()


async def test_a_400_carries_the_servers_reason(hass, aioclient_mock):
    """"400 Bad Request" hid for months that Kinboard wanted other field names.
    Its own `error` text says which, so that is what is raised."""
    aioclient_mock.post(
        f"{API}/services/add_pocket_money",
        status=400,
        json={"error": "`person` and a non-zero `amount` are required", "code": "invalid_request"},
        headers=JSON,
    )
    with pytest.raises(KinboardRequestError, match="`person` and a non-zero `amount`"):
        await _client(hass).async_call_service(
            "add_pocket_money", {"person_id": "p", "amount": 1}, idempotency_key="k"
        )


async def test_a_400_without_a_body_is_still_a_request_error(hass, aioclient_mock):
    aioclient_mock.post(f"{API}/services/dismiss_attention", status=400)
    with pytest.raises(KinboardRequestError, match="HTTP 400"):
        await _client(hass).async_call_service("dismiss_attention", {}, idempotency_key="k")


async def test_writes_carry_an_idempotency_key(hass, aioclient_mock):
    """Automations retry. A retried "add milk" must not add milk twice."""
    aioclient_mock.post(f"{API}/lists/shopping", json={"id": "abc"}, headers=JSON)
    await _client(hass).async_add_list_item(
        "shopping", "Milch", None, idempotency_key="ha-fixed-key"
    )
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Idempotency-Key"] == "ha-fixed-key"


async def test_add_omits_due_when_there_is_none(hass, aioclient_mock):
    """On create, an absent due date is absent — not an explicit null.

    Contrast with the update path, which always sends the key. Creating is
    "make this", updating is "set these fields", and only the second needs to
    express "clear it".
    """
    aioclient_mock.post(f"{API}/lists/shopping", json={}, headers=JSON)
    await _client(hass).async_add_list_item("shopping", "Milch", None, idempotency_key="k")
    assert aioclient_mock.mock_calls[0][2] == {"summary": "Milch"}


async def test_list_and_event_readers_survive_a_junk_body(hass, aioclient_mock):
    """A malformed body yields an empty list, never an exception.

    These feed a poll loop; raising would mark entities unavailable over a
    server hiccup that the next poll fixes.
    """
    aioclient_mock.get(f"{API}/lists/shopping", json=["not", "a", "dict"], headers=JSON)
    assert await _client(hass).async_get_list("shopping") == []
