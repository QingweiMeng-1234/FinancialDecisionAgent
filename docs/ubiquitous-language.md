# Ubiquitous Language — Event Collector Agent

## Core Entities

### Event
A normalized, validated representation of a market signal or event that occurred at a specific point in time.

**Attributes:**
- `id` (str, UUID): Unique identifier for the event. Generated internally.
- `source` (EventSource): The origin of this event (manual, news, api, etc.).
- `raw_text` (str): The original, unstructured market information. Must be at least 50 characters.
- `timestamp` (datetime): When the event was collected or occurred.

**Invariants:**
- `id` must be unique across all events
- `raw_text` must not be empty
- `raw_text` must be at least 50 characters (considered "useful" signal)
- `source` must be a valid EventSource
- `timestamp` must be a valid datetime

---

### EventSource
A value object representing the origin of an event.

**Allowed Values:**
- `manual` — Manually entered by a user
- `news` — Sourced from news feed or news API
- `api` — Sourced from market data API (e.g., Bloomberg, IEX, etc.)

**Validation Rule:**
Only these three values are accepted. Any other source is invalid.

---

### RawEventInput
The untrusted, unvalidated input received from external sources or user submission before normalization and validation.

**Attributes:**
- `source` (str): Raw source identifier (not yet validated as EventSource)
- `raw_text` (str): Raw unstructured text (not yet validated for length, content, etc.)

**Purpose:**
Acts as a boundary between the external world and the domain. Input validation rules are applied to convert RawEventInput → Event.

---

### EventBatch
A container holding a collection of Events produced by a single collection run or operation.

**Attributes:**
- `events` (list[Event]): The events collected in this batch
- `batch_id` (str, UUID): Identifier for tracking this collection operation
- `created_at` (datetime): When the batch was created

**Purpose:**
Groups multiple events together for easier tracking, reporting, and downstream processing by the Event Structuring Agent.

---

## Validation Rules

### For RawEventInput → Event

1. **Source Validation**: Ensure `source` is one of {`manual`, `news`, `api`}. If not, raise `InvalidEventSourceError`.
2. **Text Length**: Ensure `raw_text` length ≥ 50 characters. If not, raise `InvalidEventTextError`.
3. **Text Not Empty**: Ensure `raw_text` is not None or empty string. If not, raise `InvalidEventTextError`.

---

## Process Flow

```
RawEventInput
    ↓ (validate)
Event
    ↓ (collect multiple)
EventBatch
    ↓ (pass to Event Structuring Agent)
```

---

## Design Decisions

- **Events are immutable once created**: Once an Event is validated and created, it should not be modified.
- **Validation is strict**: We throw exceptions on invalid input rather than silently accepting or coercing values. This forces upstream callers to handle errors explicitly.
- **EventBatch is a simple container**: It has no business logic beyond aggregating events. It does not enforce uniqueness or ordering.
