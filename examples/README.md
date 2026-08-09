# Example automations

[`automations.yaml`](automations.yaml) holds eleven automations you can paste
straight into your own `automations.yaml`, or into **Settings → Automations →
⋮ → Edit in YAML** one at a time.

They are not illustrative pseudo-code. Every one of them is loaded into a real
Home Assistant by the test suite on each CI run, and every Kinboard entity,
service and event they mention is checked against the integration itself — so
an example cannot quietly rot into naming something that no longer exists.

## What you will need to change

The Kinboard half works as written. The rest is a guess about your house:

| In the examples | Replace with |
|---|---|
| `notify.notify` | your notifier — `notify.mobile_app_…` |
| `person.papa`, `zone.supermarkt` | a real person and zone |
| `media_player.kitchen` | a speaker you own |
| `input_button.milch_alle` | any button, or a tag scan |
| `sensor.waschmaschine_leistung` | your washing machine's power sensor |

## What each one shows

| | Automation | Worth copying for |
|---|---|---|
| 1 | Birthday greeting at breakfast | the state *is* the person's name, so it can be spoken directly |
| 2 | A week's warning to buy a present | writes a **task back into Kinboard** instead of a notification that gets swiped away |
| 3 | Nudge before the next appointment | `minutes_remaining` crossing a threshold |
| 4 | Pack the school bag tonight | an empty state means holidays — no reminder, without a weekday condition |
| 5 | Overdue tasks at dinner | once, when the family is together, not at 03:00 |
| 6 | The list when you pass the shop | zone trigger gated on the list not being empty |
| 7 | A button by the fridge adds milk | Kinboard without a screen |
| 8 | A finished wash becomes a task | still there tomorrow if nobody acts tonight |
| 9 | Acknowledge a completed task | an **event**, so ticking on the wall tablet counts too |
| 10 | A device joined the family board | a join code is a shared secret |
| 11 | Nothing planned for tomorrow | `unknown` is deliberately not zero |

## Why the event-driven ones are reliable

Numbers 9 and 10 trigger on `platform: event`. Those events come from **database
triggers inside Kinboard**, not from its API layer — so a task ticked on the
kitchen tablet fires one exactly as an API call does. Most of Kinboard's screens
write straight to the database, and events raised in the API layer would have
missed nearly all of them.

Delivery is resumable: the integration remembers the last event it processed, so
a restart of either system loses nothing.
