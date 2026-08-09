"""The shipped examples are checked against the real integration.

Documentation rots quietly: an example that names an entity which was renamed
two releases ago still looks perfectly reasonable on the page. Both halves are
checked here — that every automation is valid enough for Home Assistant to
load, and that every Kinboard name it mentions actually exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from homeassistant.setup import async_setup_component

from custom_components.kinboard.const import DOMAIN, KNOWN_EVENTS
from custom_components.kinboard import ALL_SERVICES

from .test_entity_ids import CONTRACT_IDS

EXAMPLES = Path(__file__).parent.parent / "examples" / "automations.yaml"
CONFIG = yaml.safe_load(EXAMPLES.read_text(encoding="utf-8"))
RAW = EXAMPLES.read_text(encoding="utf-8")


def test_there_are_at_least_ten():
    """The pack is advertised as ten. Fewer is a broken promise."""
    assert len(CONFIG) >= 10


def test_every_example_is_distinctly_identified():
    ids = [a["id"] for a in CONFIG]
    aliases = [a["alias"] for a in CONFIG]
    assert len(set(ids)) == len(ids), "duplicate id — the second would overwrite the first"
    assert len(set(aliases)) == len(aliases)


@pytest.mark.parametrize("entity_id", sorted(set(re.findall(r"\b(?:sensor|binary_sensor|todo|calendar)\.kinboard_[a-z_]+", RAW))))
def test_referenced_entities_exist(entity_id):
    """Otherwise the example silently never fires."""
    assert entity_id in CONTRACT_IDS, f"{entity_id} is not an entity this integration creates"


@pytest.mark.parametrize("service", sorted(set(re.findall(r"\bkinboard\.([a-z_]+)", RAW))))
def test_referenced_services_exist(service):
    assert service in ALL_SERVICES, f"kinboard.{service} is not a service this integration registers"


# Matched on `event_type:` specifically, because the examples' own ids also
# start with `kinboard_` and only the triggers have to be real events.
@pytest.mark.parametrize("event", sorted(set(re.findall(r"event_type:\s*(kinboard_[a-z_]+)", RAW))))
def test_referenced_events_exist(event):
    assert event in KNOWN_EVENTS, f"{event} is not an event Kinboard emits"


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_home_assistant_can_load_all_of_them(
    hass, setup_integration, expected_lingering_timers
):
    """Schema validation, which catches a mistyped trigger key or a bad
    template long before somebody pastes it into their own config.

    Lingering timers are expected and declared. Most of these automations
    trigger on a time of day, so loading them registers timers that outlive the
    test by design — that is the thing being tested. Newer Home Assistant test
    harnesses fail teardown on leftover timers unless told, which surfaced only
    when the suite was run against 2026.7 rather than the harness default.
    """
    assert await async_setup_component(hass, "automation", {"automation": CONFIG})
    await hass.async_block_till_done()

    loaded = hass.states.async_entity_ids("automation")
    assert len(loaded) == len(CONFIG), (
        f"{len(CONFIG) - len(loaded)} example(s) failed to load"
    )
