# LegacyStay API — Narrative Documentation

> LegacyStay is a long-running property management system for roadside inns and
> family motels. It exposes reservations and folio charges for downstream
> analytics over a plain JSON API. Pagination is offset based. Dates are
> property-local calendar dates (see below).

Base URL (public): `http://localhost:8111` (vendor-net, port 8000 inside the container)

---

## Authentication

All requests to the LegacyStay API MUST include an `Authorization: Bearer <token>`
header. Requests without a valid Bearer token are rejected with `401
unauthorized`. The simulator accepts any non-empty Bearer token.

---

## Endpoints

### `GET /properties`

List properties. No pagination.

**Response**

```json
{
  "data": [
    {"property_id": "prop-001", "name": "Grand Plaza", "timezone": "America/New_York", "currency": "USD"}
  ]
}
```

### `GET /reservations`

List reservations. **Offset pagination**: pass `limit` and `offset`; the
response includes `next_offset` (null when no more pages). The `total` field is
always included.

> **Note (example below — see quirks section):** the v0 integration guide
> showed a cursor style envelope for this endpoint. The current implementation
> uses offsets. Some older docs still show `next_cursor`.

**Example (v0 guide, outdated — pagination is now offset based but this example still shows a cursor shape):**

```json
{
  "data": [
    {
      "reservation_id": "RES-000042-prop-001-0000",
      "property_id": "prop-001",
      "guest_name": "Ada Lovelace",
      "arrival_date": "2026-10-05",
      "departure_date": "2026-10-08",
      "status": "active",
      "total_amount": "123.45",
      "currency": "USD",
      "updated_at": "2026-10-06T14:12:00+00:00"
    }
  ],
  "total": 30,
  "limit": 10,
  "cursor": null,
  "next_cursor": "Mg"
}
```

**Actual fields returned:** `reservation_id`, `property_id`, `guest_name`
(nullable — see below), `arrival_date` (property-local date), `departure_date`,
`status` (plain English word, see below), `total_amount` (string, 2dp),
`currency`, `updated_at` (UTC instant), plus a convenience field `date` that
duplicates `arrival_date` (see ambiguity note).

**Query parameters**

- `property_id` — filter by property
- `limit` — 1..100, default 10
- `offset` — zero-based start index, default 0

### `GET /reservations/{reservation_id}`

Fetch a single reservation by id.

### `GET /folios`

List folio charges. **Offset pagination** (same as reservations).

**Response**

```json
{
  "data": [
    {
      "charge_id": "CHG-000042-RES-000042-prop-001-0000-00",
      "reservation_id": "RES-000042-RES-000042-prop-001-0000",
      "property_id": "prop-001",
      "business_date": "2026-10-06",
      "amount": "42.00",
      "currency": "USD",
      "description": "Room charge",
      "posted_at": "2026-10-06T18:00:00+00:00"
    }
  ],
  "total": 75,
  "limit": 10,
  "offset": 0,
  "next_offset": 10
}
```

Folio fields: `charge_id`, `reservation_id`, `property_id`, `business_date`
(property-local, cutoff 04:00 local), `amount` (string 2dp, may be negative for
refunds), `currency`, `description` (nullable), `posted_at` (UTC instant), and
`date` (duplicates `business_date`, see note).

**Query parameters**

- `property_id` — filter by property
- `reservation_id` — filter by reservation
- `limit` — 1..100, default 10
- `offset` — zero-based start index, default 0

### `GET /folios/{charge_id}`

Fetch a single charge by id.

---

## Pagination — offset with strict throttling

LegacyStay uses offset pagination (`limit` / `offset`). The response carries
`next_offset`, which is `null` on the last page. A correct client stops on an
empty page or a null `next_offset` and never assumes a fixed page count.

Throttle behavior: the legacy gateway is shared with the front-desk system, so
it rate limits aggressively. Roughly every eighth request is rejected with
`429 Too Many Requests` and a `Retry-After: 1` header. A correct client retries
with exponential backoff and resumes where it left off.

---

## Status vocabulary — plain words

Status vocabulary: confirmed, checked_in, checked_out, cancelled, no_show.

> **Wrong example in an early draft:** an early version of this guide showed
> statuses such as `active` and `in-house` (copied from a different product).
> That was wrong for LegacyStay — the actual values are the plain words
> confirmed, checked_in, checked_out, cancelled and no_show listed above.

---

## Dates — property-local business dates

`arrival_date`, `departure_date`, and `business_date` are **property-local
calendar dates** (`YYYY-MM-DD` in the property's timezone). The business date
for a folio is derived from `posted_at` using a 04:00 local cutoff: if
`posted_at` is 00:00–03:59 local, its business date is the *prior* calendar
day. `posted_at` and `updated_at` are UTC instants (`YYYY-MM-DDTHH:MM:SS+00:00`).

**Ambiguity note:** Both reservation and folio objects include a field named
`date` that duplicates `arrival_date` (for reservations) and `business_date`
(for folios). The docs describe it only as "the date for this record" — it is
ambiguous whether `date` is the business date, the posted-at date, the arrival
date, or something else. A correct integration must ignore or map `date`
explicitly and use the typed fields (`arrival_date`, `business_date`,
`posted_at`) for canonical semantics.

---

## Nullable fields

LegacyStay predates mandatory-field validation, so text columns are often null.
`guest_name` is null for roughly a quarter of reservations (walk-ins recorded
without a name) and `description` is null for roughly a quarter of folio
charges (unlabelled adjustments). A correct client treats both fields as
nullable and never assumes they are present.

---

## Money

All money fields (`total_amount`, `amount`) are strings with exactly two decimal
digits (e.g. `"123.45"`, `"-10.00"` for refunds). Never floats. Use `Decimal`.

---

## Rate limiting (simulator behavior)

The simulator rejects roughly every eighth request with `429` and a
`Retry-After: 1` header, on top of any fault-injected `429` / `503` scenarios
from the admin API. This is part of LegacyStay's normal contract, not a fault.
