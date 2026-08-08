<div align="center">

<img src="custom_components/kinboard/brand/logo.png" alt="Kinboard — Home Assistant integration" width="760">

</div>

# Kinboard for Home Assistant

Makes [Kinboard](https://github.com/svenger87/kinboard) — a self-hosted family
dashboard — a first-class participant in Home Assistant.

Most integrations are one-directional: Kinboard already *reads* Home Assistant
(lights, sensors, cameras). This is the other direction. Home Assistant gains
entities for what the family has on today, services to add shopping items and
tasks, and Kinboard's own events on the bus so automations can react when a
task is completed or a saving goal is reached.

```yaml
automation:
  - alias: "Remind about sports kit"
    trigger:
      - platform: state
        entity_id: binary_sensor.kinboard_attention_required
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          message: "{{ state_attr('sensor.kinboard_school_tomorrow', 'children') }} needs kit"
```

---

## Status: not usable yet

**This is a scaffold against a contract that has not been implemented.**

The Kinboard side — the `/api/integration/v1` surface this component talks to —
is Phase 1 of the 2026 plan and ships with Kinboard **v1.9**. Until then there
is nothing for the config flow to connect to, and setup will fail with
*"this Kinboard is older than 1.9.0"*, which is the correct behaviour.

What exists here today:

| | |
|---|---|
| Contract frozen in `const.py` | ✅ 10 entities, 8 services, 7 events |
| Config flow + reauth, with distinct errors | ✅ written |
| Coordinator with a persisted event cursor | ✅ written |
| Sensors, binary sensor, diagnostics | ✅ written |
| `calendar.kinboard_family` | ✅ implemented (needs Kinboard v1.9's `/calendar/events`) |
| Tests | ❌ none yet |
| Run against a real Home Assistant | ❌ **never** |

Nothing here has executed inside Home Assistant. Every file parses and the
contract is self-consistent; that is a different and much weaker claim.

---

## Why this is a separate repository

Kinboard cut **31 releases in the 30 days to 2026-08-08** (15 stable, 16
prerelease). HACS presents GitHub releases as available versions, so shipping
this component from the Kinboard repository would show roughly one integration
update per day for a component that changed on a handful of those days. People
learn to ignore update badges — the worst possible habit for the component that
holds their integration token.

The two want opposite rhythms. Kinboard is tuned for fast releases and
auto-updating instances. An integration wants infrequent, semver-meaningful
releases that mean *something changed for you*.

The contract is kept from drifting by versioning rather than by co-location:
`/api/integration/v1` carries its version in the path, the OpenAPI spec is
published as a Kinboard release artifact, and this component's CI validates
against a pinned version of it. That has to work anyway — the Kinboard Bridge
will be a third consumer of the same API, and it cannot live in both
repositories either.

Full reasoning: RFC-001 §11.1 in the Kinboard repository.

---

## Installation (once v1.9 exists)

1. Add this repository to HACS as a custom repository, category *Integration*.
2. Install **Kinboard** and restart Home Assistant.
3. *Settings → Devices & Services → Add Integration → Kinboard*.
4. Enter the address of your Kinboard instance and an integration token.

Create the token in Kinboard under **Settings → Integrations**. It is shown
once, stored only as a hash, and can be revoked or rotated individually — it is
deliberately not your join code, settings PIN, or a device session.

### Scopes

The token carries explicit scopes. `family:read` is the floor; without it there
is nothing to display and setup refuses. Write scopes are optional — an install
that only publishes entities into Home Assistant is a legitimate setup.

| Scope | Needed for |
|---|---|
| `family:read` | all entities (required) |
| `shopping:write` | `kinboard.add_shopping_item` |
| `tasks:write` | `kinboard.create_task`, `kinboard.add_pocket_money` |
| `notes:write` | `kinboard.create_note` |
| `announcements:write` | announcements, context, dismissing attention items |

A service call made without the matching scope fails with a message naming the
problem, rather than a bare `403`.

---

## Design notes

**Events are not lost across a restart.** The coordinator stores the last
`domain_events` id it processed and resumes from it. The cursor advances only
*after* events are dispatched onto the bus: crashing in between replays an
event, which consumers can deduplicate on `event_id`, whereas the other order
would drop it silently.

**Unknown event types are ignored on purpose.** A newer Kinboard may emit
events this version has never heard of. Treating that as an error would make
every Kinboard upgrade a breaking change.

**Attributes stay small.** Home Assistant writes every state change to its
recorder database, so a large attribute payload is copied on each update. Long
lists belong behind an API call.

**One state call, not eight.** All sensors are backed by a single
`/family/summary` request. A self-hosted Kinboard may be running on a Raspberry
Pi; eight polls where one would do is eight times the load.

---

## Licence

MIT, matching Kinboard.
