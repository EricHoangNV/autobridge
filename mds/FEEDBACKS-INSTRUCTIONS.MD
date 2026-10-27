# How to Read and Write FEEDBACKS.MD

All agents MUST read this file before reading or updating FEEDBACKS.MD.

---

## How to READ FEEDBACKS.MD

Read ONLY the "Latest Review Verdicts" block at the top. This contains the current PASS/FAIL status for each review track. Stop at the first `---` separator.

Do NOT read the full file unless you need a specific older report for context. The file grows over time — reading it all wastes context window.

If you need detail on a specific cycle, search for `Cycle: N` in the file and read only that section.

---

## File Structure (3 sections)

```
# Feedbacks

## Latest Review Verdicts (read this section first — stop at the breaker line)

### Security: Cycle N — PASS/FAIL (YYYY-MM-DD)
- one-line checklist items + critical issues

### Code Review: Cycle N — PASS/FAIL (YYYY-MM-DD)
- one-line summary

### Product Review: Cycle N — PASS/FAIL (YYYY-MM-DD)
- one-line summary

---

← review reports, newest first

---

# Detailed Review History (reference only — read specific entries when you need context)

← older review cycles, newest first, oldest at bottom
```

---

## How to WRITE to FEEDBACKS.MD

Updating FEEDBACKS.MD is a two-step process.

### Step 1 — Insert your full report

Insert your new report directly AFTER the `---` separator below the Latest Review Verdicts block, pushing all existing reports down.

Add a `---` separator after your report block.

Rules:
- NEVER append to the end of the file.
- NEVER insert inside the "Latest Review Verdicts" block.
- NEVER insert below the "# Detailed Review History" breaker line.
- NEVER delete or overwrite existing reports.

### Step 2 — Update your summary line in Latest Review Verdicts

Replace ONLY your review type's `###` line and its bullet points. Each review type owns its own block.

Use this format for your summary:

**Security reviewers:**
```
### Security: Cycle N — PASS/FAIL (YYYY-MM-DD)
- auth & access control: PASS/FAIL (brief reason if FAIL)
- data exposure / secrets: PASS/FAIL
- input validation: PASS/FAIL
- dependency / supply chain: PASS/FAIL
- Severity: one-line issue title (for each new issue found)
```

**Code reviewers:**
```
### Code Review: Cycle N — PASS/FAIL (YYYY-MM-DD)
- one-line summary of findings or "No critical issues."
- test coverage: improved/unchanged/degraded
- type safety / lint: PASS/FAIL
```

**Product reviewers:**
```
### Product Review: Cycle N — PASS/FAIL (YYYY-MM-DD)
- requirements coverage: PASS/FAIL (brief reason if FAIL)
- core user flow: PASS/FAIL
- data handling / privacy: PASS/FAIL
- one-line summary of findings
```

Do NOT touch other review types' lines. Security reviewers do not edit Code Review lines. Code reviewers do not edit Product Review lines. Etc.

---

## Verification Checklist

After your edit, verify ALL of these:

- [ ] The `## Latest Review Verdicts` block reflects your new cycle's verdict
- [ ] Your full report appears right after the first `---`
- [ ] A `---` separator exists between your report and the next older report
- [ ] No existing content was deleted or overwritten
- [ ] The `# Detailed Review History` breaker line still exists
- [ ] The file still ends with the oldest review cycles at the bottom
- [ ] You only modified your own `###` summary line, not other review types

---

## Common Mistakes to Avoid

1. **Appending to bottom** — your report will be invisible to all agents. Always insert right after the Latest Review Verdicts block.
2. **Overwriting another reviewer's summary** — only touch your own `###` block.
3. **Reading the entire file** — wastes context. Read the Latest Review Verdicts block only unless you need a specific past report.
4. **Inserting below the breaker** — content below `# Detailed Review History` is for older cycles only, not new reports.
5. **Forgetting the separator** — always add `---` after your report block.
