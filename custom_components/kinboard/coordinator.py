"""Polling for entity state, plus the event stream.

Two different jobs with two different failure modes, so they are kept apart:

* Entity state is a poll. If one poll fails, the next one fixes it and nothing
  is lost — HA marks entities unavailable in the meantime.
* Events are a log with a cursor. A missed event is gone. So the cursor only
  advances after the events have been dispatched onto the bus, and it is
  persisted so a Home Assistant restart resumes instead of replaying.

RFC-001 section 7 requires that a restart of *either* system loses nothing.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KinboardAuthError, KinboardClient, KinboardError, KinboardVersionError
from .const import (
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    KNOWN_EVENTS,
    STORAGE_KEY_CURSOR,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# How many pages one poll will drain before leaving the rest for the next one.
# At the server's cap of 100 events per page this is 5,000 events per minute,
# enough to clear a long outage quickly, while still bounding a single cycle so
# a server that always answered `has_more` could not loop forever.
MAX_EVENT_PAGES_PER_CYCLE = 50

# How many consecutive auth rejections before believing the token is really
# dead. One is not enough: see the note in _async_update_data.
AUTH_FAILURES_BEFORE_REAUTH = 2

# Where that count lives.
#
# NOT on the coordinator. A failure during the first refresh fails setup, and
# Home Assistant then retries setup with a brand new coordinator — so a counter
# held here would reset to zero every time and a genuinely revoked token would
# never reach reauth at all. It would retry forever instead, which is a quieter
# but worse failure than the one being fixed. Keyed on the entry so it survives
# both the coordinator and a reload.
AUTH_FAILURE_COUNTS = "auth_failure_counts"


class KinboardCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keeps entity state fresh and forwards Kinboard events onto the bus."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: KinboardClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.client = client
        self.entry = entry
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_CURSOR}.{entry.entry_id}")
        self._cursor: int | None = None
        self._cursor_loaded = False
        # The DoorbellWatcher for this entry, set by async_setup_entry. Hung
        # here because the coordinator is the entry's runtime_data.
        self.doorbells: Any = None
        # Points, creatures and rewards (GET /rewards), refreshed with every
        # poll. None until read, or while the last read failed. Kept apart
        # from `data`, which is the summary as the server sent it.
        self.rewards: dict[str, Any] | None = None
        # None until the first answer; False when this Kinboard has no
        # /rewards (1.13.0-rc.14 or older): the feature is off, quietly, and
        # not asked for again until the integration is reloaded.
        self.rewards_supported: bool | None = None
        self._rewards_failing = False

    async def _load_cursor(self) -> None:
        if self._cursor_loaded:
            return
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._cursor = stored.get("after_id")
        self._cursor_loaded = True

    async def _save_cursor(self, after_id: int) -> None:
        self._cursor = after_id
        await self._store.async_save({"after_id": after_id})

    async def _async_update_data(self) -> dict[str, Any]:
        await self._load_cursor()
        try:
            summary = await self.client.async_get_summary()
        except KinboardAuthError as err:
            # Reauth is a one-way door: Home Assistant stops polling entirely
            # and waits for somebody to re-enter a token. That is right for a
            # revoked token and badly wrong for a passing one.
            #
            # It happened: Kinboard could not reach its database for a few
            # seconds during a restart, answered 401 because "no such token"
            # and "cannot check" shared a code path, and the integration went
            # dark until a human noticed. Kinboard answers 503 now, but this
            # side should not depend on the other side being careful.
            #
            # So a rejection has to persist. Two failed cycles is two minutes
            # of a token being refused, which no restart lasts and no genuinely
            # revoked token survives.
            counts = self.hass.data.setdefault(DOMAIN, {}).setdefault(AUTH_FAILURE_COUNTS, {})
            counts[self.entry.entry_id] = counts.get(self.entry.entry_id, 0) + 1
            if counts[self.entry.entry_id] >= AUTH_FAILURES_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(f"authentication rejected, retrying: {err}") from err
        except KinboardError as err:
            raise UpdateFailed(str(err)) from err

        # Got a good answer, so any earlier rejection was transient.
        self.hass.data.get(DOMAIN, {}).get(AUTH_FAILURE_COUNTS, {}).pop(
            self.entry.entry_id, None
        )

        # Event delivery rides the same interval for now. When the WebSocket
        # transport lands this moves to a push subscription; the cursor
        # semantics are identical either way, which is why they live here and
        # not in the transport.
        await self._pump_events()
        await self._refresh_rewards()
        return summary

    async def _refresh_rewards(self) -> None:
        """Read points, creatures and rewards; never fails the poll.

        The summary is what every other entity needs, so a Kinboard without
        rewards, or one whose rewards cannot be read right now, must not make
        it fail. Too old: off, said once at info level, as the doorbells do.
        Anything else: the reward entities go unavailable until the next poll.
        """
        if self.rewards_supported is False:
            return
        try:
            self.rewards = await self.client.async_get_rewards()
        except KinboardVersionError:
            _LOGGER.info(
                "This Kinboard has no points and rewards (needs a release newer than "
                "1.13.0-rc.14); the points, creature and reward request sensors are off"
            )
            self.rewards_supported = False
            self.rewards = None
            return
        except KinboardError as err:
            if not self._rewards_failing:
                _LOGGER.warning("Could not read Kinboard's points and rewards: %s", err)
                self._rewards_failing = True
            else:
                _LOGGER.debug("Could not read Kinboard's points and rewards: %s", err)
            self.rewards = None
            return
        if not isinstance(self.rewards, dict):
            self.rewards = None
            return
        if self._rewards_failing:
            _LOGGER.info("Kinboard's points and rewards are readable again")
        self._rewards_failing = False
        self.rewards_supported = True

    async def _pump_events(self) -> None:
        for _ in range(MAX_EVENT_PAGES_PER_CYCLE):
            try:
                page = await self.client.async_get_events(after_id=self._cursor)
            except KinboardError as err:
                # Not fatal: entity state is still good, and the cursor has not
                # moved, so nothing is lost. Next cycle retries from the same point.
                _LOGGER.debug("Event fetch failed, will retry from cursor %s: %s", self._cursor, err)
                return

            events = page.get("events") or []
            if not events:
                return

            if not await self._dispatch(events):
                # Nothing moved the cursor, so asking again would fetch the
                # same page forever. Stop rather than spin.
                return

            if not page.get("has_more"):
                return

    async def _dispatch(self, events: list[dict[str, Any]]) -> bool:
        """Fire one page onto the bus. Returns whether the cursor moved."""
        highest = self._cursor

        for event in events:
            event_type = event.get("event_type")
            event_id = event.get("event_id")

            # Belt and braces against re-delivery. The server filters on
            # `id > after`, so this should not trigger — but "no duplicate
            # events" is a promise to every automation a household has written,
            # and it should not rest solely on the other side staying correct.
            # A doubled event means the lights flash twice or a task is created
            # twice, and nobody can diagnose that from the outside.
            if (
                isinstance(event_id, int)
                and self._cursor is not None
                and event_id <= self._cursor
            ):
                _LOGGER.debug("Skipping already-delivered event %s", event_id)
                continue

            if event_type not in KNOWN_EVENTS:
                # Forward-compatible: a newer Kinboard may emit events this
                # version has never heard of. Ignoring them is correct; caring
                # about them would make every Kinboard upgrade a breaking one.
                _LOGGER.debug("Ignoring unknown event type %s", event_type)
            else:
                self.hass.bus.async_fire(event_type, event.get("payload") or {})

            if isinstance(event_id, int) and (highest is None or event_id > highest):
                highest = event_id

        # Advance only after dispatch. Crashing between fire and save replays
        # an event, which consumers can dedupe on event_id; crashing the other
        # way round would drop it silently, which they cannot.
        if highest is not None and highest != self._cursor:
            await self._save_cursor(highest)
            return True
        return False
