"""When a doorbell rings, put its camera on Kinboard's wall displays.

Which doorbell belongs to which camera is set in Kinboard, on the camera
(Settings → Cameras), and served by GET /cameras as `doorbell_entity_id`. This
module reads that, listens to exactly those entities, and calls show_camera
when one of them rings. Nothing to configure on the Home Assistant side.

What counts as a ring is the whole of the risk here, in both directions: a
missed ring is the feature not working, and a phantom one — Home Assistant
restarting, a Zigbee doorbell dropping off and coming back — takes over every
screen in the house for a minute with nobody at the door. So the rule is
narrow and lives in one pure function, `is_ring`, which the tests pin down
transition by transition.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
    valid_entity_id,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import (
    KinboardAuthError,
    KinboardClient,
    KinboardError,
    KinboardRateLimitError,
    KinboardRequestError,
    KinboardVersionError,
)
from .const import (
    DOMAIN,
    DOORBELL_DEBOUNCE_SECONDS,
    DOORBELL_DOMAINS,
    DOORBELL_PRESS_MAX_AGE_SECONDS,
    DOORBELL_REFRESH_INTERVAL_SECONDS,
    ISSUE_SHOW_CAMERA_FORBIDDEN,
)

_LOGGER = logging.getLogger(__name__)

_NOT_A_VALUE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


def _monotonic() -> float:
    """The debounce clock. A function of its own so a test can move it."""
    return time.monotonic()


def is_ring(old: State | None, new: State | None) -> bool:
    """Whether this state change is somebody pressing the bell.

    Never on the first state an entity reports (`old` is None): that is Home
    Assistant starting, or an entity restoring what it had before a restart,
    and in both cases nobody pressed anything just now. Never into or out of
    `unavailable`: a doorbell coming back online reports the state it had
    before it dropped off, which is not a new press.

    binary_sensor: off → on, nothing else. unknown → on is a sensor
    initialising, not a ring.

    event, button, input_button: the state is the time of the last press, and
    a ring is that time changing to a new, recent one. "Recent" (within 30
    seconds of now) is the safety net: these entities set the timestamp to
    Home Assistant's own clock at the moment of the press, so a real ring is
    always seconds old, while anything replaying an old value — a restore, an
    integration reloading — carries an old one.

    The one place this departs from "never out of unknown": an event entity
    that has never fired is `unknown`, and its first ring is unknown → now.
    Ignoring that would make a newly installed doorbell fail on its first
    press and work on the second, which is exactly the report nobody can
    reproduce. The freshness check is what makes allowing it safe.
    """
    if old is None or new is None:
        return False
    if old.state == STATE_UNAVAILABLE or new.state in _NOT_A_VALUE:
        return False

    domain = new.domain
    if domain == "binary_sensor":
        return old.state == STATE_OFF and new.state == STATE_ON

    if domain in ("event", "button", "input_button"):
        if new.state == old.state:
            return False
        pressed = dt_util.parse_datetime(new.state)
        if pressed is None:
            return False
        if pressed.tzinfo is None:
            pressed = pressed.replace(tzinfo=dt_util.UTC)
        age = (dt_util.utcnow() - pressed).total_seconds()
        # A little slack the other way, for a timestamp a few ms ahead.
        return -5 <= age <= DOORBELL_PRESS_MAX_AGE_SECONDS

    return False


def build_mapping(cameras: list[dict[str, Any]]) -> dict[str, str]:
    """Doorbell entity id → the camera it shows.

    One camera per bell. Kinboard shows one camera at a time per family, so a
    bell named on two cameras would show the first and then the second over
    it; the first in Kinboard's own order (Settings → Cameras) wins instead.
    """
    mapping: dict[str, str] = {}
    for camera in cameras:
        bell = camera.get("doorbell_entity_id")
        if not bell or bell in mapping:
            continue
        if not valid_entity_id(bell) or bell.split(".", 1)[0] not in DOORBELL_DOMAINS:
            _LOGGER.warning(
                "Kinboard camera %s names %s as its doorbell, which is not a binary "
                "sensor, event, button or input button; it will not be watched",
                camera.get("name"), bell,
            )
            continue
        mapping[bell] = camera["id"]
    return mapping


class DoorbellWatcher:
    """Watches the doorbells one Kinboard family has assigned to cameras."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: KinboardClient) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.mapping: dict[str, str] = {}
        # None until the first fetch answers; False when this Kinboard has no
        # /cameras (older than 1.13.0-rc.6).
        self.supported: bool | None = None
        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._last_rung: dict[str, float] = {}
        self._fetch_failing = False
        self._forbidden_logged = False
        self._too_old_logged = False

    @property
    def _issue_id(self) -> str:
        return f"{ISSUE_SHOW_CAMERA_FORBIDDEN}_{self.entry.entry_id}"

    async def async_start(self) -> None:
        await self.async_refresh()
        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._async_scheduled_refresh,
            timedelta(seconds=DOORBELL_REFRESH_INTERVAL_SECONDS),
            name=f"{DOMAIN} doorbell mapping",
        )

    @callback
    def async_stop(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        self._unsubscribe()

    async def _async_scheduled_refresh(self, _now: Any) -> None:
        await self.async_refresh()

    async def async_refresh(self) -> None:
        """Re-read which doorbell shows which camera, and listen accordingly."""
        try:
            cameras = await self.client.async_get_cameras()
        except KinboardVersionError:
            # No /cameras: Kinboard before 1.13.0-rc.6. Not a fault — the
            # feature simply is not there — so it is said once, quietly.
            if not self._too_old_logged:
                _LOGGER.info(
                    "This Kinboard has no camera list (needs 1.13.0-rc.6 or newer); "
                    "doorbells will not show cameras"
                )
                self._too_old_logged = True
            self.supported = False
            self._apply({})
            return
        except KinboardError as err:
            # Keep the mapping we have. A network blip must not unhook the
            # doorbell; if Kinboard is down, the ring fails on its own anyway.
            if not self._fetch_failing:
                _LOGGER.warning("Could not read Kinboard's cameras, keeping the last list: %s", err)
                self._fetch_failing = True
            else:
                _LOGGER.debug("Could not read Kinboard's cameras: %s", err)
            return

        if self._fetch_failing or self.supported is False:
            _LOGGER.info("Kinboard's camera list is readable again")
        self._fetch_failing = False
        self._too_old_logged = False
        self.supported = True
        self._apply(build_mapping(cameras))

    @callback
    def _apply(self, mapping: dict[str, str]) -> None:
        if mapping == self.mapping:
            return
        self._unsubscribe()
        self.mapping = mapping
        # Forget the debounce for bells that are no longer watched, so the
        # dict cannot grow with every doorbell ever configured.
        self._last_rung = {k: v for k, v in self._last_rung.items() if k in mapping}
        if mapping:
            _LOGGER.debug("Watching doorbells %s", sorted(mapping))
            self._unsub_state = async_track_state_change_event(
                self.hass, list(mapping), self._async_state_changed
            )

    @callback
    def _unsubscribe(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        bell = event.data["entity_id"]
        camera = self.mapping.get(bell)
        if camera is None:
            return
        if not is_ring(event.data["old_state"], event.data["new_state"]):
            return

        now = _monotonic()
        last = self._last_rung.get(bell)
        if last is not None and now - last < DOORBELL_DEBOUNCE_SECONDS:
            _LOGGER.debug("%s rang again within %ss, not showing %s again", bell, DOORBELL_DEBOUNCE_SECONDS, camera)
            return
        self._last_rung[bell] = now

        self.entry.async_create_background_task(
            self.hass, self._async_show(bell, camera), name=f"{DOMAIN} show camera for {bell}"
        )

    async def _async_show(self, bell: str, camera: str) -> None:
        try:
            # No duration: Kinboard's default (60 s) is the household's to
            # change there, not ours to override here.
            await self.client.async_show_camera(camera)
        except KinboardAuthError as err:
            if err.status != 403:
                # 401: the token itself is dead. The coordinator turns that
                # into reauth within two polls; a second message here would
                # only say the same thing less usefully.
                _LOGGER.debug("Kinboard rejected the token while %s rang: %s", bell, err)
                return
            if not self._forbidden_logged:
                _LOGGER.warning(
                    "%s rang, but Kinboard refused to show camera %s: the integration "
                    "token lacks announcements:write (\"send messages\")",
                    bell, camera,
                )
                self._forbidden_logged = True
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_SHOW_CAMERA_FORBIDDEN,
                translation_placeholders={"family": self.entry.title, "doorbell": bell},
            )
            return
        except KinboardRateLimitError as err:
            _LOGGER.debug("%s rang, but show_camera is rate limited (retry after %ss)", bell, err.retry_after)
            return
        except KinboardRequestError as err:
            _LOGGER.warning("%s rang, but Kinboard would not show camera %s: %s", bell, camera, err)
            return
        except KinboardVersionError:
            _LOGGER.info("%s rang, but this Kinboard has no show_camera (needs 1.13.0-rc.6 or newer)", bell)
            return
        except KinboardError as err:
            _LOGGER.warning("%s rang, but Kinboard could not be reached to show camera %s: %s", bell, camera, err)
            return

        _LOGGER.debug("%s rang; Kinboard is showing camera %s", bell, camera)
        self._forbidden_logged = False
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)

    def diagnostics(self) -> dict[str, Any]:
        """The doorbell → camera mapping: entity ids and camera ids, no secrets."""
        return {
            "supported": self.supported,
            "listening": self._unsub_state is not None,
            "mapping": dict(sorted(self.mapping.items())),
        }
