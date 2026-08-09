<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/kinboard/brand/dark_logo@2x.png">
    <img src="custom_components/kinboard/brand/logo@2x.png" alt="Kinboard for Home Assistant" width="420">
  </picture>
</p>

<p align="center">
  Connects <a href="https://github.com/svenger87/kinboard">Kinboard</a> — a self-hosted family dashboard — to Home Assistant.
</p>

---

Most integrations point one way: Kinboard already *reads* Home Assistant, showing lights, sensors and cameras on the kitchen display. This is the other direction.

Home Assistant gains what the family has on today, the family calendar, and the shopping and task lists as real to-do lists you can tick. Kinboard gains a way for automations to put something on the shopping list or create a task.

```yaml
automation:
  - alias: "Say who has a birthday when the kitchen light comes on"
    trigger:
      - platform: state
        entity_id: light.kitchen
        to: "on"
    condition:
      - condition: numeric_state
        entity_id: sensor.kinboard_next_birthday
        attribute: days_remaining
        below: 1
    action:
      - service: tts.speak
        data:
          message: "It's {{ states('sensor.kinboard_next_birthday') }}'s birthday today."
```

## Requirements

- **Kinboard 1.9.0 or newer**, reachable from Home Assistant.
- Home Assistant 2024.10 or newer.

## Install

Not yet in HACS's default list. Add it as a custom repository:

**HACS → ⋮ → Custom repositories** → `svenger87/kinboard-homeassistant`, category **Integration** → install → restart Home Assistant.

Then **Settings → Devices & Services → Add Integration → Kinboard**, and give it:

| | |
|---|---|
| **Address** | the URL Home Assistant can reach Kinboard on — `http://kinboard.local:3000`, not `localhost` |
| **Token** | created in Kinboard under **Settings → Integrations** |

The token is shown once and stored only as a fingerprint. If you lose it, make another; if you suspect it, revoke it — each token is revoked on its own without disturbing the others.

## Permissions

Tick only what the integration needs. Nothing is granted by default, and **no permission implies another** — a token that may add shopping items cannot create tasks, and a read-only token cannot write at all.

| Scope | Needed for |
|---|---|
| `family:read` | every sensor, and the calendar — the minimum |
| `events:read` | the events on the Home Assistant bus |
| `shopping:write` | the shopping to-do list, `kinboard.add_shopping_item` |
| `tasks:write` | the task to-do list, `kinboard.create_task` |
| `notes:write` | `kinboard.create_note` |

A call made without the matching scope fails with a message that says so, rather than a bare `403`.

## What you get

### Sensors

| Entity | Shows | Also carries |
|---|---|---|
| `sensor.kinboard_next_family_event` | the next appointment's title | start, location, person, minutes remaining |
| `sensor.kinboard_events_today` | how many events today | the list of them |
| `sensor.kinboard_next_birthday` | **whose** birthday is next | days remaining, the date |
| `sensor.kinboard_school_tomorrow` | **which children** have school | first lesson, count |
| `sensor.kinboard_shopping_items` | open items | |
| `sensor.kinboard_tasks_due` | open tasks | open, overdue |
| `sensor.kinboard_tasks_overdue` | overdue tasks | |
| `sensor.kinboard_meal_today` | what's for dinner | recipe reference |
| `sensor.kinboard_meal_tomorrow` | tomorrow's dinner | recipe reference |
| `sensor.<child>_pocket_money` | one per child, their balance | currency as the unit |
| `binary_sensor.kinboard_attention_required` | reserved for a later release | |
| `sensor.kinboard_display_mode` | reserved for a later release | |

The state is deliberately the **human answer**, not a count — Home Assistant shows the state on a card and hides attributes, so "Next birthday: 0" told nobody it was Nora's. The counts are still there as attributes.

### Calendar

`calendar.kinboard_family` — the household's calendar, including events that merely *overlap* the window you're looking at, so a week's holiday shows on every day of it rather than only its first.

### To-do lists

`todo.kinboard_shopping_list` and `todo.kinboard_tasks` — real to-do entities. Tick an item in Home Assistant and it ticks in Kinboard; add one in Kinboard and it appears here.

Each list keeps **Kinboard's own meaning** for deletion rather than inventing a third: a deleted task goes to Kinboard's recycle bin and can be restored, a deleted shopping item is gone, because the shopping list deletes on one tap by design and has Undo in the app.

### Services

`kinboard.add_shopping_item`, `create_task`, `create_note`.

Also declared and answering *"not implemented yet"* rather than *"unknown"*, so you can tell a typo from a feature that hasn't shipped: `show_announcement`, `activate_context`, `dismiss_attention`, `add_pocket_money`, `refresh_integration`.

### Events

Fired on the Home Assistant bus, so an automation can trigger on `platform: event`:

`kinboard_task_completed` · `kinboard_shopping_item_added` · `kinboard_family_event_created` · `kinboard_device_joined` · `kinboard_saving_goal_reached`

These come from **database triggers in Kinboard**, not from its API layer — so a task ticked on the kitchen tablet fires one exactly as an API call does. Most of Kinboard's screens write straight to the database, and events raised in the API layer would have missed nearly all of them.

Delivery is resumable: the integration remembers the last event it processed, so a restart of either system loses nothing.

## Troubleshooting

Setup tells you which of four things went wrong, because they need different fixes:

| Message | Means |
|---|---|
| *Could not reach Kinboard* | wrong address, or Kinboard is down. Use the address **Home Assistant** can reach. |
| *Kinboard rejected this token* | the token was mistyped, revoked, or has expired |
| *This Kinboard is older than 1.9.0* | upgrade Kinboard; the integration API does not exist in earlier versions |
| *This token cannot read family data* | recreate it with at least `family:read` |

Every Kinboard response also carries a short reference, repeated on every log line for that request:

```bash
docker logs kinboard-webapp 2>&1 | grep <reference>
```

**Diagnostics** (⋮ on the integration → Download diagnostics) reports shapes and counts, never your family's data — no names, no titles, no token.

## Contributing

The entity, service and event names are a published contract, frozen in [`const.py`](custom_components/kinboard/const.py) and mirrored by Kinboard's OpenAPI spec, which is checked against its implementation on every CI run. Add freely; never repurpose a name that exists — somebody's automation depends on it.

## Licence

MIT, matching Kinboard.
