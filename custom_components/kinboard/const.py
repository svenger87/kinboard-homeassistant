"""Constants for the Kinboard integration.

The names in this file are the public contract frozen by RFC-001 in the
Kinboard repository (`docs/rfc/001-integration-api.md`). Once a household has
written an automation against one of them, changing it breaks that automation
silently. Treat every string here as an API: additive changes only, and a
rename means a new name plus a deprecation period.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "kinboard"

# The integration talks to exactly one versioned surface. The version lives in
# the path so that consumers can be released independently of Kinboard itself
# (RFC-001 section 3).
API_BASE_PATH: Final = "/api/integration/v1"

# Kinboard releases that predate the Integration API cannot serve this
# component. The config flow checks this and refuses with a legible message
# rather than failing later with confusing 404s.
MIN_KINBOARD_VERSION: Final = "1.9.0"

CONF_BASE_URL: Final = "base_url"
CONF_TOKEN: Final = "token"

DEFAULT_SCAN_INTERVAL_SECONDS: Final = 60

# --------------------------------------------------------------------------
# Entities Kinboard publishes into Home Assistant (RFC-001 section 5.1)
# --------------------------------------------------------------------------
# Keys are the API's field names; the suffix is what the entity is called in
# Home Assistant. Attributes deliberately stay small: HA writes every state
# change to its recorder database, so a fat attribute set is copied on every
# update.

SENSOR_NEXT_FAMILY_EVENT: Final = "next_family_event"
SENSOR_EVENTS_TODAY: Final = "events_today"
SENSOR_SHOPPING_ITEMS: Final = "shopping_items"
SENSOR_MEAL_TODAY: Final = "meal_today"
SENSOR_TASKS_DUE: Final = "tasks_due"
SENSOR_SCHOOL_TOMORROW: Final = "school_tomorrow"
SENSOR_BIRTHDAYS_UPCOMING: Final = "birthdays_upcoming"
SENSOR_DISPLAY_MODE: Final = "display_mode"
SENSOR_TASKS_OVERDUE: Final = "tasks_overdue"
SENSOR_MEAL_TOMORROW: Final = "meal_tomorrow"

# One sensor per child, so the key is a prefix rather than a whole key.
SENSOR_POCKET_MONEY_PREFIX: Final = "pocket_money"

BINARY_SENSOR_ATTENTION_REQUIRED: Final = "attention_required"

CALENDAR_FAMILY: Final = "family"

# The two lists Kinboard exposes as to-do lists. Names match the API path.
#
# `supports_due` mirrors the server's own list description (todos have a
# due_date column, shopping_items does not) and has to be declared here rather
# than discovered, because Home Assistant validates a service call against an
# entity's supported features BEFORE the entity is asked to do anything — so
# the answer must exist at construction, before any fetch.
#
# Getting this wrong is not a soft failure: adding a task with a due date was
# rejected with `update_field_not_supported` and the item was never created.
TODO_LISTS: Final[dict[str, dict[str, object]]] = {
    "shopping": {"name": "Shopping list", "supports_due": False},
    "tasks": {"name": "Tasks", "supports_due": True},
}

# --------------------------------------------------------------------------
# Services Home Assistant can call on Kinboard (RFC-001 section 5.2)
# --------------------------------------------------------------------------

SERVICE_ADD_SHOPPING_ITEM: Final = "add_shopping_item"
SERVICE_CREATE_TASK: Final = "create_task"
SERVICE_CREATE_NOTE: Final = "create_note"
SERVICE_SHOW_ANNOUNCEMENT: Final = "show_announcement"
SERVICE_ACTIVATE_CONTEXT: Final = "activate_context"
SERVICE_DISMISS_ATTENTION: Final = "dismiss_attention"
SERVICE_ADD_POCKET_MONEY: Final = "add_pocket_money"
SERVICE_REFRESH_INTEGRATION: Final = "refresh_integration"

# Each service needs a scope on the integration token. The config flow shows
# which scopes the supplied token is missing rather than letting the call fail
# at runtime with a 403 the user cannot interpret.
SERVICE_REQUIRED_SCOPES: Final[dict[str, str]] = {
    SERVICE_ADD_SHOPPING_ITEM: "shopping:write",
    SERVICE_CREATE_TASK: "tasks:write",
    SERVICE_CREATE_NOTE: "notes:write",
    SERVICE_SHOW_ANNOUNCEMENT: "announcements:write",
    SERVICE_ACTIVATE_CONTEXT: "announcements:write",
    SERVICE_DISMISS_ATTENTION: "announcements:write",
    SERVICE_ADD_POCKET_MONEY: "tasks:write",
    SERVICE_REFRESH_INTEGRATION: "family:read",
}

# --------------------------------------------------------------------------
# Events Kinboard emits onto the Home Assistant bus (RFC-001 section 5.3)
# --------------------------------------------------------------------------
# Fired verbatim, so an automation trigger reads `platform: event, event_type:
# kinboard_task_completed`. Every event carries event_id, family_id,
# occurred_at, source, actor_id and a versioned payload.

EVENT_TASK_COMPLETED: Final = "kinboard_task_completed"
EVENT_SHOPPING_ITEM_ADDED: Final = "kinboard_shopping_item_added"
EVENT_FAMILY_EVENT_CREATED: Final = "kinboard_family_event_created"
EVENT_ANNOUNCEMENT_ACKNOWLEDGED: Final = "kinboard_announcement_acknowledged"
EVENT_SAVING_GOAL_REACHED: Final = "kinboard_saving_goal_reached"
EVENT_DEVICE_JOINED: Final = "kinboard_device_joined"
EVENT_CONTEXT_CHANGED: Final = "kinboard_context_changed"

KNOWN_EVENTS: Final[frozenset[str]] = frozenset(
    {
        EVENT_TASK_COMPLETED,
        EVENT_SHOPPING_ITEM_ADDED,
        EVENT_FAMILY_EVENT_CREATED,
        EVENT_ANNOUNCEMENT_ACKNOWLEDGED,
        EVENT_SAVING_GOAL_REACHED,
        EVENT_DEVICE_JOINED,
        EVENT_CONTEXT_CHANGED,
    }
)

# Storage key for the event cursor. The coordinator records the last
# domain_events id it processed so a restart of either system resumes rather
# than replays — an explicit acceptance criterion of RFC-001 section 7.
STORAGE_KEY_CURSOR: Final = f"{DOMAIN}.event_cursor"
STORAGE_VERSION: Final = 1
