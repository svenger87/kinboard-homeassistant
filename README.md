<p align="center">
  <!-- Absolute URLs, and the dark variant as the <img> fallback.
       HACS renders this README inside Home Assistant, where a relative path
       resolves against nothing and <picture>/<source> are stripped by the
       sanitiser — so the fallback is the only thing that survives, and it has
       to be the one that reads on a dark background. logo.png is dark ink on
       transparency: correct on GitHub in light mode, invisible in HACS. -->
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/svenger87/kinboard-homeassistant/main/custom_components/kinboard/brand/logo.png">
    <img src="https://raw.githubusercontent.com/svenger87/kinboard-homeassistant/main/custom_components/kinboard/brand/dark_logo.png" alt="Kinboard for Home Assistant" width="420">
  </picture>
</p>

<p align="center">
  Connects <a href="https://github.com/svenger87/kinboard">Kinboard</a> — a self-hosted family dashboard — to Home Assistant.
</p>

---

Most integrations point one way: Kinboard already *reads* Home Assistant, showing lights, sensors and cameras on the kitchen display. This is the other direction.

Home Assistant gains what the family has on today, the family calendar, and the shopping and task lists as real to-do lists you can tick. Kinboard gains a way for automations to put something on the shopping list or create a task.

```yaml
- alias: "Say who has a birthday when the kitchen light comes on"
  triggers:
    - trigger: state
      entity_id: light.kitchen
      to: "on"
  conditions:
    - condition: numeric_state
      entity_id: sensor.kinboard_next_birthday
      attribute: days_remaining
      below: 1
  actions:
    - action: tts.speak
      data:
        message: "It's {{ states('sensor.kinboard_next_birthday') }}'s birthday today."
```

Eleven more, ready to paste, in **[`examples/`](https://github.com/svenger87/kinboard-homeassistant/tree/main/examples)** — each one loaded
into a real Home Assistant by the test suite on every CI run.

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
| `sensor.kinboard_next_family_event` | the next appointment's title | `start_at`, `location`, `person_id`, `minutes_remaining` |
| `sensor.kinboard_events_today` | how many events today | the list of them |
| `sensor.kinboard_next_birthday` | **whose** birthday is next | `days_remaining`, `date` |
| `sensor.kinboard_school_tomorrow` | **which children** have school | `children`, `count`, `first_lesson` |
| `sensor.kinboard_shopping_items` | open items | |
| `sensor.kinboard_tasks_due` | open tasks | open, overdue |
| `sensor.kinboard_tasks_overdue` | overdue tasks | |
| `sensor.kinboard_meal_today` | what's for dinner | recipe reference |
| `sensor.kinboard_meal_tomorrow` | tomorrow's dinner | recipe reference |
| `sensor.kinboard_next_waste_collection` | **which bin** goes out next | `type`, `date`, `days_until`, the next few |
| `sensor.kinboard_<child>_pocket_money` | one per child, their balance | currency as the unit |
| `sensor.kinboard_<child>_<goal>` | one per active saving goal, as a percentage | `saved`, `target`, `currency` |
| `binary_sensor.kinboard_attention_required` | whether the board has something outstanding | `count`, `top` (its title), `top_key`, `items` (key and title, at most ten) |
| `sensor.kinboard_display_mode` | which part of the day it is | `morning` · `afternoon` · `evening` · `quiet` |

The state is deliberately the **human answer**, not a count — Home Assistant shows the state on a card and hides attributes, so "Next birthday: 0" told nobody it was Nora's. The counts are still there as attributes.

Entity ids do **not** contain your family name. The device is called Kinboard so that `sensor.kinboard_next_birthday` means the same thing on every install and an example automation can be copied verbatim; your family name is on the device instead. A second family in one household gets the usual `_2` suffix.

### Calendar

`calendar.kinboard_family_calendar` — the household's calendar, including events that merely *overlap* the window you're looking at, so a week's holiday shows on every day of it rather than only its first.

### To-do lists

`todo.kinboard_shopping_list` and `todo.kinboard_tasks` — real to-do entities. Tick an item in Home Assistant and it ticks in Kinboard; add one in Kinboard and it appears here.

Each list keeps **Kinboard's own meaning** for deletion rather than inventing a third: a deleted task goes to Kinboard's recycle bin and can be restored, a deleted shopping item is gone, because the shopping list deletes on one tap by design and has Undo in the app.

### Services

`kinboard.add_shopping_item` · `create_task` · `create_note` · `add_pocket_money` · `dismiss_attention` · `refresh_integration`.

Each service shows its fields with a description under **Developer tools → Actions**.

**`add_pocket_money`** — pick the child with `entity_id`, their pocket money sensor; the picker lists only those sensors. Automations that already know the child's Kinboard `person_id` can pass that instead. Give exactly one of the two. `amount` is in currency units — `2.50` means €2.50, negative takes money away — and `reason` shows up in the child's history. It follows Kinboard's own deposit path, so the balance moves and the child's avatar tier is credited, not just a transaction row.

```yaml
action: kinboard.add_pocket_money
data:
  entity_id: sensor.kinboard_mia_pocket_money
  amount: 2.50
  reason: Rasen gemäht
```

**`dismiss_attention`** — with no `attention_id` it dismisses the item on top, the one `binary_sensor.kinboard_attention_required` names in `top` and `top_key`. To dismiss a specific one, pass its key from the sensor's `items`. If nothing is outstanding the call says so rather than silently doing nothing.

```yaml
# A button by the door says "seen it" to whatever the board is showing.
triggers:
  - trigger: state
    entity_id: input_button.gesehen
conditions:
  - condition: state
    entity_id: binary_sensor.kinboard_attention_required
    state: "on"
actions:
  - action: kinboard.dismiss_attention
```

Both need a Kinboard that includes the fix for [svenger87/kinboard#309](https://github.com/svenger87/kinboard/issues/309). Earlier versions read different field names and refuse every call from Home Assistant; the error now says that it is Kinboard that needs updating. The attention sensor's `top_key` and `items` likewise appear only once Kinboard sends them — an older one gives you `count` and `top`.

Two more are declared and answer *"not implemented yet"* rather than *"unknown"*, so you can tell a typo from a feature that hasn't shipped: `show_announcement` and `activate_context`. Both wait on Kinboard features that do not exist yet.

### Events

Fired on the Home Assistant bus, so an automation can trigger on `platform: event`:

`kinboard_task_completed` · `kinboard_shopping_item_added` · `kinboard_family_event_created` · `kinboard_device_joined` · `kinboard_saving_goal_reached` · `kinboard_context_changed`

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

A brief *"could not verify the token — try again"* is Kinboard restarting, not a bad token: it answers `503` and the integration retries rather than asking you to reconfigure.

Every Kinboard response also carries a short reference, repeated on every log line for that request:

```bash
docker logs kinboard-webapp 2>&1 | grep <reference>
```

**Diagnostics** (⋮ on the integration → Download diagnostics) reports shapes and counts, never your family's data — no names, no titles, no token.

## Contributing

The entity, service and event names are a published contract, frozen in [`const.py`](https://github.com/svenger87/kinboard-homeassistant/tree/main/custom_components/kinboard/const.py) and mirrored by Kinboard's OpenAPI spec, which is checked against its implementation on every CI run. Add freely; never repurpose a name that exists — somebody's automation depends on it.

## Licence

MIT. Kinboard itself is licensed under PolyForm Noncommercial 1.0.0 for releases after v1.12.1-rc.3; this integration talks to it only over its HTTP API and stays MIT.
