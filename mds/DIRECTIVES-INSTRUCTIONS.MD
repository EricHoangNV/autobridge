# How to Read and Write DIRECTIVES.MD

All agents MUST read this file before adding or changing a directive.

---

## How to READ DIRECTIVES.MD

1. The **Quick Reference** (the numbered list at the top) is injected into every ralph loop prompt. Do not open the file to read it again.
2. For everything else, run `./directives-for.sh`: with the paths you expect to touch before you start, and with no arguments before you commit (it uses what you changed against origin/main). It prints the sections that `## 0. Section map` maps to those paths in full and, from the other sections, only the bullets that name a touched file, folder or file name.
   To read one section by hand: `grep -n '^## ' mds/DIRECTIVES.MD` gives the line numbers, then `sed -n '<start>,<end>p' mds/DIRECTIVES.MD`.
3. Never read the file from the top or in full. Every turn of a session re-reads what was printed.

---

## How to WRITE DIRECTIVES.MD

### Quick Reference: hard size limits

The Quick Reference is copied into every prompt and carried through every turn, so it stays small:

- **One line per item, at most 300 characters.** Format: `N. **Rule in a few words** — the rule in one sentence (§N)`.
- **At most 2 KB for the whole list.** To add an item when the list is full, merge or shorten another one.
- Only rules that apply to almost every task belong here. Everything else goes in a § section.
- No examples, file lists, commands or rationale: they go in the § section the item points to.

### § sections

- One bullet per rule. Keep each bullet under 600 characters. A longer rule is split into sub-bullets or points to its LEARNINGS entry.
- Rules only: no task history, no implementation narratives, no lists of files changed (those belong in `BUILD-NOTES.md` and `progress-log.md`).
- Put a new rule in the section it belongs to. Add a new `## N.` section only for a new area, and keep the numbering.
- When a rule changes, edit it in place instead of adding a second bullet that contradicts the first.
- **Name the paths a rule is about** in backticks (`src/autobridge/runtime/interpreter.py`, `verifier/`): `./directives-for.sh` finds bullets by the paths they name, so a bullet that names no path is only seen by an agent whose section is mapped whole, and by the daily audit.
- A new `## N.` section that applies to whole folders gets a line in `## 0. Section map` (`- \`glob\` → N`).
- A rule that code can check (ruff rule, mypy, a test, a script refusing, an import-linter contract) should become that check: then agents cannot forget it. Note the check in the bullet ("checked by `…`").

### The daily audit

`sh ralph-afk.sh --validate <sandbox> 1` audits the commits since `mds/AUDIT.md`'s "Last audited" commit against every rule, writes its findings to `mds/AUDIT.md` and open requests in `mds/REQUESTS.MD`, and moves the marker forward. It is the only run that reads every section.

---

## Verification Checklist

After your edit, verify ALL of these:

- [ ] `sed -n '/^## Quick Reference/,/^---$/p' mds/DIRECTIVES.MD | wc -c` is under 2048
- [ ] Every Quick Reference item is a single line of at most 300 characters
- [ ] Your new bullet is under 600 characters and in the right section
- [ ] No two bullets say different things about the same rule
