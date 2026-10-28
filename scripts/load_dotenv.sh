#!/usr/bin/env bash
# REQ-014: safe .env loader for host targets (make smoke/e2e/demo/bench).
#
# Usage (from the repo root):  . ./scripts/load_dotenv.sh
# Test override:               DOTENV_PATH=/tmp/x/.env . ./scripts/load_dotenv.sh
#
# Parses KEY=value lines and exports only non-empty values:
#  - blank lines and full-line `#` comments are ignored;
#  - inline ` # ...` comments are stripped (outside quotes);
#  - matching single/double quotes around a value are removed;
#  - keys must match [A-Za-z_][A-Za-z0-9_]*; anything else is skipped;
#  - an already-exported non-empty variable always wins over .env, and an
#    empty .env value never overrides anything (REQ-014 item 2).
#
# The file is parsed as data and never executed (no `set -a; . .env`),
# so inline comments cannot leak into values and stray content cannot run.
# Silent on success; a no-op when the file does not exist.

# Resolve the file: explicit DOTENV_PATH wins, else ./.env (repo root),
# else the checkout's .env next to this script. Safe under `set -u`.
if [ -n "${DOTENV_PATH:-}" ]; then
  _ab_dotenv="$DOTENV_PATH"
elif [ -f .env ]; then
  _ab_dotenv=.env
else
  _ab_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  _ab_dotenv="$_ab_script_dir/../.env"
fi

if [ -n "${_ab_dotenv:-}" ] && [ -f "$_ab_dotenv" ] && command -v python3 >/dev/null 2>&1; then
  # The parser prints `export KEY=<quoted>` lines for vars that are unset
  # or empty in the current environment; eval applies them to this shell.
  eval "$(python3 - "$_ab_dotenv" <<'PY'
import os
import re
import shlex
import sys

path = sys.argv[1]
key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
try:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
except OSError:
    sys.exit(0)
for raw in text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    if "=" not in line:
        continue
    key, _, val = line.partition("=")
    key = key.strip()
    if not key_re.match(key):
        continue
    val = val.strip()
    if val[:1] in ("'", '"'):
        # Quoted value: keep everything up to the matching close quote
        # verbatim (a `#` inside quotes is data, not a comment); anything
        # after the close quote (e.g. a trailing comment) is dropped.
        end = val.find(val[0], 1)
        if end != -1:
            val = val[1:end]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].rstrip()
    else:
        # Unquoted: an inline comment starts at whitespace + `#`.
        val = re.split(r"\s+#", val, maxsplit=1)[0].rstrip()
    if not val:
        continue
    if os.environ.get(key):
        continue
    print(f"export {key}={shlex.quote(val)}")
PY
)"
fi
unset _ab_dotenv _ab_script_dir
