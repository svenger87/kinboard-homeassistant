"""calendar.kinboard_family — the household's calendar, in Home Assistant.

Unlike the sensors, this entity is **not** served from the coordinator's
polled summary. A CalendarEntity is asked for arbitrary windows — Home
Assistant requests whatever the user is looking at, which may be next month —
and the summary only ever describes now. So `async_get_events` calls the
server directly for the window it was given.

The one value that *is* taken from the coordinator is `event`, the current or
next appointment, because that is exactly what `sensor.kinboard_next_family_event`
already polls for. Fetching it twice would double the request rate for the same
answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import KinboardConfigEntry
from .api import KinboardError
from .const import CALENDAR_FAMILY, SENSOR_NEXT_FAMILY_EVENT
from .entity import KinboardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KinboardConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([KinboardFamilyCalendar(entry.runtime_data)])


def _parse(value: Any) -> datetime | None:
    """Parse a timestamp from the API, or give up quietly.

    Returns None rather than raising: one malformed row must not take the whole
    calendar down. Home Assistant renders the events it understands and the bad
    one is simply absent, which is far better than an entity that fails to load.
    """
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    return dt_util.as_local(parsed) if parsed else None


def _to_calendar_event(row: dict[str, Any]) -> CalendarEvent | None:
    """Convert one API row, or None if it cannot be represented."""
    start = _parse(row.get("start_at"))
    end = _parse(row.get("end_at"))
    if start is None or end is None:
        return None

    # Home Assistant models an all-day event as dates, not datetimes. Passing
    # datetimes for one makes it render at midnight with a duration, which is
    # not what an all-day event means.
    if row.get("all_day"):
        return CalendarEvent(
            start=start.date(),
            end=end.date(),
            summary=row.get("title") or "",
            description=row.get("description"),
            location=row.get("location"),
            uid=row.get("id"),
        )

    return CalendarEvent(
        start=start,
        end=end,
        summary=row.get("title") or "",
        description=row.get("description"),
        location=row.get("location"),
        uid=row.get("id"),
    )


class KinboardFamilyCalendar(KinboardEntity, CalendarEntity):
    """The consolidated family calendar."""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, CALENDAR_FAMILY)

    @property
    def available(self) -> bool:
        # KinboardEntity gates on the key being present in the summary, which
        # this entity does not use. Availability is simply whether the last
        # poll succeeded.
        return self.coordinator.last_update_success

    @property
    def event(self) -> CalendarEvent | None:
        """The current or next appointment.

        Taken from the summary the coordinator already polls, rather than a
        second request for the same answer.
        """
        payload = (self.coordinator.data or {}).get(SENSOR_NEXT_FAMILY_EVENT)
        if not isinstance(payload, dict):
            return None
        return _to_calendar_event(
            {
                "id": payload.get("id"),
                "title": payload.get("title"),
                "start_at": payload.get("start_at"),
                # The summary carries no end; a calendar entity needs one, so
                # assume an hour. It is only used to decide whether the event
                # is "now" or "next" — the real end comes from async_get_events.
                "end_at": _fallback_end(payload.get("start_at")),
                "location": payload.get("location"),
            }
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events overlapping the window Home Assistant asked for."""
        try:
            rows = await self.coordinator.client.async_get_calendar_events(
                start_date, end_date
            )
        except KinboardError:
            # Returning [] would render as "nothing on" for that window, which
            # is indistinguishable from an empty calendar. Raising lets Home
            # Assistant report the failure as a failure.
            raise

        events = [_to_calendar_event(row) for row in rows]
        return [e for e in events if e is not None]


def _fallback_end(start: Any) -> str | None:
    """An hour after `start`, for the summary's end-less next event."""
    parsed = _parse(start)
    if parsed is None:
        return None
    return (parsed + timedelta(hours=1)).isoformat()
