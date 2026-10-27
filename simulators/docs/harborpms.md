# HarborPMS API — Narrative Documentation

> HarborPMS is a property management system for boutique hotels. It exposes
> reservations and folio charges for downstream analytics. The API is simple:
> list properties, list reservations, fetch folios. Pagination uses opaque
> cursors. Dates are property-local calendar dates (see below).

Base URL (public): `http://localhost:8106` (vendor-net, port 8000 inside the container)

---

## Authentication

HarborPMS is permissive in development — no `Authorization` header is required.
In production a header is validated, but the simulator accepts any value or none
(see `check_auth` without `require_bearer`).

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

List reservations. **Cursor pagination**: pass `limit` and `cursor`; response
includes `next_cursor` (null when no more pages). The `total` field is always
included.

> **Note (example below — see quirks section):** The example response in the
> v1 narrative guide showed `offset` pagination. The current implementation uses
> cursors. Some older docs still show `next_offset`.

**Example (v1 guide, outdated — pagination is now cursor-based but this example still shows offset shape):**

```json
{
  "data": [
    {
      "reservation_id": "RES-000042-prop-001-0000",
      "property_id": "prop-001",
      "guest_name": "Ada Lovelace",
      "arrival_date": "2026-10-05",
      "departure_date": "2026-10-08",
      "status": "confirmed",
      "total_amount": "123.45",
      "currency": "USD",
      "updated_at": "2026-10-06T14:12:00+00:00"
    }
  ],
  "total": 30,
  "limit": 10,
  "offset": 0,
  "next_offset": 10
}
```

**Actual fields returned:** `reservation_id`, `property_id`, `guest_name`,
`arrival_date` (property-local date), `departure_date`, `status` (opaque code,
see below), `total_amount` (string, 2dp), `currency`, `updated_at` (UTC
instant), plus a convenience field `date` that duplicates `arrival_date` (see
ambiguity note).

**Query parameters**

- `property_id` — filter by property
- `limit` — 1..100, default 10
- `cursor` — opaque pagination cursor (base64 idx)
- `offset` — (legacy, ignored when cursor is used)

### `GET /reservations/{reservation_id}`

Fetch a single reservation by id.

### `GET /folios`

List folio charges. **Cursor pagination** (same as reservations).

**Response**

```json
{
  "data": [
    {
      "charge_id": "CHG-000042-RES-000042-prop-001-0000-00",
      "reservation_id": "RES-000042-prop-001-0000",
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
  "cursor": null,
  "next_cursor": "Mg"
}
```

Folio fields: `charge_id`, `reservation_id`, `property_id`, `business_date`
(property-local, cutoff 04:00 local), `amount` (string 2dp, may be negative for
refunds), `currency`, `description` (nullable), `posted_at` (UTC instant), and
`date` (duplicates `business_date`, see note).

**Query parameters**: `property_id`, `reservation_id`, `limit`, `cursor`

### `GET /folios/{charge_id}`

Fetch a single charge by id.

---

## Pagination — cursor with known quirk

HarborPMS uses cursor pagination (`cursor` / `next_cursor`). The cursor is a
base64-encoded index. The API is simple: pass `next_cursor` from the previous
response as `cursor` on the next request. When `next_cursor` is `null`, there
are no more pages.

**Known issue (cursor_repeat_token):** On the fault-injected scenario
`cursor_repeat_token`, every second page repeats the previous cursor instead of
advancing, causing an infinite loop if the caller does not detect the repeat.
A correct client must guard against `repeated_cursor` (compare `next_cursor` to
the previous `cursor` and stop).

---

## Status codes — opaque vocabulary

HarborPMS does not use English words for reservation status. It uses short
opaque codes:

- `H1` — (maps to canonical `confirmed`)
- `H2` — (`checked_in`)
- `H3` — (`checked_out`)
- `H4` — (`cancelled`)
- `H5` — (`no_show`)

> **Wrong example in earlier draft:** An early version of this doc showed
> statuses as `confirmed / checked_in / ...` (Atlas vocabulary). That was
> copied from Atlas and is wrong for Harbor — the actual codes are `H1`..`H5`.

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

## Undocumented endpoint

> This guide deliberately lists only the flat endpoints above. The simulator
> also exposes `GET /reservations/{reservation_id}/folios` — a **nested**
> folio listing under a reservation (same filter as `GET /folios?reservation_id=...`
> but scoped to the reservation id in the path, with cursor pagination). It is
> not described in the Markdown guide (intentionally undocumented for the
> bootstrap discovery challenge) but it is discoverable via probing and is the
> preferred access pattern for Harbor's nested folios.

For discovery: probing `GET /reservations/{id}/folios` returns
`{data: [...], total, limit, cursor, next_cursor}` where `data` are the
folios for that reservation. It supports `limit` and `cursor`.

---

## Folios — nested access pattern

In Harbor, folios are conceptually nested under reservations: each folio belongs
to a reservation and the natural access is via the reservation. The flat
`GET /folios` endpoint exists for historical compatibility and supports the same
filters, but the nested endpoint is the canonical one for the discovery engine to
find.

---

## Money

All money fields (`total_amount`, `amount`) are strings with exactly two decimal
digits (e.g. `"123.45"`, `"-10.00"` for refunds). Never floats. Use `Decimal`.

---

## Rate limiting / auth (simulator faults only)

The simulator can inject `429` / `503` / `token_expired` / `token_revoked` via
the admin API — not part of Harbor's documented contract.
