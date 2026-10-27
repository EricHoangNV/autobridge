# How to Read and Write LEARNINGS.MD

All agents MUST read this file before reading or updating LEARNINGS.MD.

---

## How to READ LEARNINGS.MD

Read the table of contents (the numbered list at the top) to find relevant learnings by topic. Then read only the specific `## N. Title` section you need.

Do NOT read the full file unless performing a comprehensive audit. The file grows over time — reading it all wastes context window.

---

## File Structure

```
# Learnings

Distilled insights from code reviews, security reviews, product reviews and debugging sessions.

---

## 1. Title of Learning

**Context:** Which review cycle, task or incident surfaced this

- Bullet points explaining the problem and root cause
- The fix or pattern that resolved it
- **Rule:** The generalized principle to follow going forward

---

## 2. Title of Learning

...
```

Each learning is a numbered section with a consistent structure: context, explanation, and a bolded rule.

---

## How to WRITE to LEARNINGS.MD

### Adding a new learning

1. Read the current last numbered section to determine the next number
2. Append your new learning at the end of the file (before any trailing blank lines), using this template:

```markdown
---

## N. Title of Learning

**Context:** Review type and cycle, task id, or incident description

- What went wrong or what was discovered
- Root cause explanation
- How it was fixed

**Rule:** One-sentence generalized principle for future development.
```

### Rules

- **Always increment the number** — never reuse or skip numbers
- **Never insert in the middle** — new learnings go at the end
- **Never delete existing learnings** — if a learning is superseded, add a new one referencing the old number (e.g., "Supersedes #N")
- **Never modify existing learnings** — unless correcting a factual error, in which case add a note: `**Updated YYYY-MM-DD:** reason`
- **Keep it generic** — learnings should be applicable beyond the specific bug. Extract the general principle.
- **One learning per issue** — don't combine unrelated discoveries into a single entry
- **Context is required** — every learning must reference where it came from (review cycle, task, incident, debugging session)

### What qualifies as a learning

Add a learning when you discover something that:
- Was **non-obvious** and caused a real bug, a false verifier verdict, a trust-boundary leak or a budget overrun
- Represents a **pattern** that could recur in future development
- Contradicts a **reasonable assumption** a developer might make
- Involves **platform-, library- or sandbox-specific behavior** (a Docker network quirk, a SQLAlchemy/Alembic gotcha, a uv or pytest quirk, an SSE/Next.js behaviour, a sandbox filesystem quirk)

Do NOT add learnings for:
- Standard best practices already in DIRECTIVES.MD
- One-off typos or simple mistakes
- Things derivable from reading the docs

### After adding a learning

Check if the learning implies a new directive. If the rule is important enough to enforce on every future change, add it to DIRECTIVES.MD as well. Learnings explain *why*; directives enforce *what*.

---

## Verification Checklist

After your edit, verify ALL of these:

- [ ] The new learning has the next sequential number
- [ ] It includes a `**Context:**` line
- [ ] It includes a `**Rule:**` line with a generalized principle
- [ ] A `---` separator exists before the new section
- [ ] No existing learnings were modified or deleted
- [ ] If the learning warrants a directive, DIRECTIVES.MD was also updated
