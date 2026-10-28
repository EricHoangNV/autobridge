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

## 5. A Dockerfile `COPY` of a scaffold dir that was never committed breaks `make up` on every clean clone

**Context:** P1.W10.3 (2026-10-27) — `make up` failed in a fresh worktree at the `ui` image build: `COPY --from=builder /srv/ui/public ./public: not found`. W7.4's Next.js scaffold never created `ui/public/`, and no prior run caught it because existing checkouts/sandbox images either had the dir incidentally or built with cache.

- Docker `COPY` has no tolerate-missing flag: one absent scaffold path fails the whole `docker compose up --build`, so no service starts and the W10 passing chain (`up → test → e2e → demo`) never runs.
- Fixed by committing the scaffold dir itself (`ui/public/.gitkeep`) rather than editing the Dockerfile — the Dockerfile was written against the standard Next.js layout, the layout was what drifted.

- **Rule:** When a Dockerfile `COPY`s a scaffold path, that path must exist in git (add `.gitkeep` for intentionally-empty dirs); after touching any `COPY` source or scaffold task, verify with a clean-context build (`docker compose build --no-cache <svc>` or a fresh worktree), not an incremental one.
---

## 6. Node resolves modules from the importing file, not the shell cwd — a config outside the package dir needs NODE_PATH

**Context:** REQ-009 (2026-10-28) — `make e2e-ui` failed in a fresh clone with `Cannot find module '@playwright/test'` even with `ui/node_modules` fully installed, while the Makefile comment claimed the journey was "green with mocks when the stack is down".

- The `test:e2e` npm script runs with cwd `ui/`, but the config it loads (`../playwright.config.ts`) lives at the repo root, and Node resolves `import ... from "@playwright/test"` relative to the importing file — walking up from the repo root, where no `node_modules` exists — never consulting the shell's cwd.
- The first symptom (`playwright: command not found`) masked the second: installing the browsers fixed the binary but the config load still crashed. The docs already recorded the workaround (`NODE_PATH=ui/node_modules ...`), but it never made it into the automation.
- Fixed in the `e2e-ui` recipe (`NODE_PATH="$$PWD/node_modules"` after `cd ui`), keeping the npm script as the single flag definition; added a Makefile-contract test so the env var cannot silently drop out.

**Rule:** When a tool loads a config file outside the package dir, set the module path (NODE_PATH) at the invocation site — cwd does not affect module resolution — and cover the invocation, not just the install, with a test.

---

## 7. A proxy that swallows upstream 403s makes "labelled" fault injection silently do nothing

**Context:** REQ-010 (2026-10-28) — `POST /demo/inject-fault` proxied to the simulator admin API without the `X-Admin-Network: admin-net` header that `simulators/common/admin.py` requires on every write, so the admin answered 403 and the fault never landed. The endpoint still returned 200 (`labelled: true`) with the 403 buried in `admin_response._http_status`, so `make demo` steps 3–4 and every consumer looked green while injecting nothing.

- The admin module's own docstring said "real callers (control proxy, e2e harness) send the header" — the harness did, the proxy didn't; the doc described intent, not code.
- The new live test caught it only because it asserts the injection *landed* (`admin_response._http_status == 200`) instead of trusting the 200 envelope — the same class of mistake as asserting on the demo's local-evidence output alone.
- Fixed by sending the header in `inject-fault` and `reset` (plus env-override parity for reset), with unit tests asserting the header on the mocked `httpx.Client` call.

**Rule:** Any proxy endpoint that returns success while delegating the real work must assert the upstream status in at least one test — a 200 envelope around a failed delegation is a silent lie.
