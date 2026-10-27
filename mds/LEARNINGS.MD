# Learnings

Distilled insights from code reviews, security reviews, product reviews and debugging sessions. Read and write it as `LEARNINGS-INSTRUCTIONS.MD` says.

## 1. `internal: true` Compose networks break host port publishing and service DNS

**Context:** P1.W1.2 — `make up` reported all services healthy, but `make smoke` could not reach the verifier on its host port, and control could not resolve `verifier` by name.

- A service attached only to an `internal: true` network (the verifier on `verifier-net`) gets no published host port, even with an explicit `ports:` mapping.
- Flipping an existing internal network to a plain bridge in place leaves containers without their service-name DNS aliases; only `docker compose down` (removing the networks) and a fresh `up` restored `verifier` resolution.
- **Rule:** Isolate Compose trust zones by network attachment only, never `internal: true` on a network whose services need host ports; after changing a network's `internal` flag, recreate from `docker compose down`, and guard it with a static compose test (`test_no_network_is_internal`).

---

## 2. Bare `on:` / `off:` / `yes:` keys in YAML parse as booleans and break spec key names

**Context:** P1.W1.3 — the plan's `retry: { on: [429, 503] }` example parsed under PyYAML (YAML 1.1) as `{True: [429, 503]}`, failing pydantic validation with a cryptic `invalid_key` error far from the real cause.

- PyYAML coerces unquoted `on/off/yes/no/y/n/true/false` mapping keys (and values) to booleans, so any spec/schema/scenario key spelled that way is unrepresentable without quoting — a trap every future YAML author (simulator scenarios, connector specs) will walk into.
- Renaming the field sidesteps it entirely: `retry.statuses` instead of `retry.on`, with the reason recorded in a code comment so nobody "fixes" it back to match the plan's spelling.

**Rule:** Never use `on/off/yes/no` (or any YAML 1.1 boolean spelling) as a spec, schema or scenario key; pick a synonym and comment why.

## 3. mypy hides `rowcount` on DML `Session.execute`; ruff TC006 wants the cast target quoted

**Context:** P1.W1.6 — `session.execute(update(Job)...).rowcount` failed typecheck (`Result` has no attribute `rowcount`) even though it works at runtime (DML returns a `CursorResult`).

- `cast(CursorResult, ...)` with a runtime import fixed mypy but tripped ruff TC006 (typing-only names belong in quotes); the combination that satisfies both is `cast("CursorResult", ...)` with `from sqlalchemy import CursorResult` inside the `TYPE_CHECKING` block.
- **Rule:** For DML rowcounts, write `cast("CursorResult", session.execute(stmt)).rowcount` and import `CursorResult` under `TYPE_CHECKING` — every future task that writes UPDATE/DELETE through the ORM will need exactly this shape.

---

## 4. E2E exact-text assertions anchor on table headers that restyles remove

**Context:** REQ-002 slice 4 — converting `ReleaseHistory` from a `<table>` to a broadcast `<ol>` broke `tests/e2e/test_06_ui_journey.spec.ts:483` (`getByText("Approved by", { exact: true })`), even though the row text (`approved by e2e-operator`) was unchanged.

- The journey was not matching the row text at all: exact match needs the whole string, and the only element whose full text was exactly `Approved by` was the old `<th>Approved by</th>` column header. Dropping the table silently removed the anchor.
- Unit tests stayed green throughout (they assert testids and row text, never the header), so only the browser run caught it.
- Fixed by adding a quiet two-column label line (`Release` / `Approved by`) above the broadcast list — same grammar as the design's `.clbl` labels, keeps the journey green without reintroducing the table.

- **Rule:** Before converting a table to a list, grep the e2e specs for exact-text assertions that the table headers satisfy; carry each one over as a visible label in the new markup and re-run the journey spec before committing.
