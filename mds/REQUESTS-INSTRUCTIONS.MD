# How to Read and Write REQUESTS.MD

All agents MUST read this file before reading or updating REQUESTS.MD.

---

## How to READ REQUESTS.MD

Read ONLY the "Open Requests" section at the top (up to the first `---` separator). This contains all unresolved requests — bugs, feature requests, improvements, etc.

Do NOT read the full file unless you need context on a specific resolved request. Resolved requests accumulate below the separator.

---

## File Structure

```
# Requests

## Open Requests (read this section only)

### REQ-001 — Short title (YYYY-MM-DD)
Status: OPEN | IN_PROGRESS | BLOCKED
Description here — freeform, any length.

### REQ-002 — Short title (YYYY-MM-DD)
Status: OPEN
Description here.

---

## Resolved Requests (reference only)

### REQ-001 — Short title (YYYY-MM-DD)
Status: DONE (YYYY-MM-DD)
Description here.
Resolution: what was done.

### REQ-000 — Short title (YYYY-MM-DD)
Status: WONTFIX (YYYY-MM-DD)
Description here.
Resolution: why it was declined.
```

---

## How users add requests

Users write freeform entries in the Open Requests section. The only required fields are:
- `### REQ-NNN — Title (YYYY-MM-DD)` — incremented ID, short title, date added
- `Status: OPEN` — initial status
- A description — can be anything: bug report, feature idea, question, improvement, screenshot reference, etc.

There is no required format for the description. Keep it natural.

---

## How agents WRITE to REQUESTS.MD

### Picking up a request

Change the status line from `OPEN` to `IN_PROGRESS`:

```
Status: IN_PROGRESS
```

### Completing a request

This is a two-step process:

**Step 1 — Move the request from Open to Resolved.**

Remove the request block from the Open Requests section. Insert it at the **top** of the Resolved Requests section (newest resolved first). Update it:

```
### REQ-NNN — Short title (YYYY-MM-DD)
Status: DONE (YYYY-MM-DD)
Original description stays.
Resolution: one-line summary of what was done.
```

**Step 2 — Verify the Open Requests section is correct.**

Make sure the removed request didn't leave a gap or break formatting.

### Declining a request

Same process as completing, but use `WONTFIX` and explain why:

```
Status: WONTFIX (YYYY-MM-DD)
Resolution: reason it was declined.
```

### Blocking a request

If a request can't proceed, update status in place (keep it in Open):

```
Status: BLOCKED
Blocked: reason — what's needed to unblock.
```

### Rules

- **Never delete requests** — move them to Resolved, never remove entirely
- **Never modify the user's original description** — add a `Resolution:` line, don't rewrite what they wrote
- **Always update progress** after resolving a request (per PROGRESS-INSTRUCTIONS.MD)
- **REQ IDs are permanent** — never reuse a number, even if the request was declined
- **Keep Open Requests clean** — only unresolved items belong in the top section

---

## Verification Checklist

After your edit, verify ALL of these:

- [ ] Resolved request was removed from Open Requests section
- [ ] Resolved request appears at the top of Resolved Requests section
- [ ] Status includes the completion date: `DONE (YYYY-MM-DD)` or `WONTFIX (YYYY-MM-DD)`
- [ ] A `Resolution:` line was added
- [ ] The user's original description was not modified
- [ ] The `---` separator between Open and Resolved sections still exists
- [ ] REQ numbering has no gaps or duplicates
