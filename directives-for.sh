#!/usr/bin/env bash
# Prints the rules in mds/DIRECTIVES.MD that apply to a change, so an agent reads only those
# (mds/DIRECTIVES-INSTRUCTIONS.MD). Sections named in "## 0. Section map" are printed whole for
# the paths they list; from every other section, only the bullets that name a touched file,
# its folder, or its file name.
#
#   ./directives-for.sh               files changed against origin/main (committed, staged, unstaged, new)
#   ./directives-for.sh <path>...     the files you are about to touch
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DIRECTIVES="$ROOT/mds/DIRECTIVES.MD"

if [ $# -gt 0 ]; then
  files=("$@")
else
  base=$(git -C "$ROOT" merge-base HEAD origin/main 2>/dev/null || echo HEAD)
  files=()
  while IFS= read -r f; do files+=("$f"); done < <({
    git -C "$ROOT" diff --name-only "$base"
    git -C "$ROOT" ls-files --others --exclude-standard
  } | sort -u)
fi
# mds/ bookkeeping has its own *-INSTRUCTIONS.MD files.
kept=()
for f in "${files[@]}"; do
  f="${f#./}"
  case "$f" in mds/*|"") ;; *) kept+=("$f") ;; esac
done
if [ ${#kept[@]} -eq 0 ]; then
  echo "No code files changed: no directives to check."
  exit 0
fi

# Section map lines: - `glob` `glob` → 1, 2 (globs are patterns, not filenames: no expansion)
set -f
whole=()
while IFS= read -r line; do
  globs="${line%%→*}"
  secs="${line##*→}"
  hit=0
  for g in $(grep -oE '`[^`]+`' <<<"$globs" | tr -d '`'); do
    for f in "${kept[@]}"; do
      # shellcheck disable=SC2053
      if [[ "$f" == $g ]]; then hit=1; break 2; fi
    done
  done
  if [ $hit -eq 1 ]; then
    for s in $(grep -oE '[0-9]+' <<<"$secs"); do whole+=("$s"); done
  fi
done < <(sed -n '/^## 0\. Section map/,/^## 1\./p' "$DIRECTIVES" | grep -E '^- `.*→')
set +f
whole_list=" "
if [ ${#whole[@]} -gt 0 ]; then whole_list=" $(printf '%s\n' "${whole[@]}" | sort -un | tr '\n' ' ')"; fi

# Words that pick bullets: each file's path, its folder, and its file name (when not too generic).
needles=()
for f in "${kept[@]}"; do
  d="$(dirname "$f")"
  b="$(basename "$f")"
  needles+=("$f")
  if [ "$d" != "." ]; then needles+=("$d/"); fi
  if [ ${#b} -ge 8 ]; then needles+=("$b"); fi
done

echo "Directives for: ${kept[*]}"
shown_whole="$(echo $whole_list)"
echo "(The Quick Reference is in your prompt. Sections printed whole: ${shown_whole:-none}; other sections: only the bullets that name your files.)"
echo
NEEDLES="$(printf '%s\n' "${needles[@]}")" WHOLE="$whole_list" awk '
  BEGIN { whole = ENVIRON["WHOLE"]; n = split(ENVIRON["NEEDLES"], nd, "\n") }
  /^## [0-9]+\./ { sec = $2; sub(/\.$/, "", sec); title = $0; shown = 0; inbullet = 0
                   if (sec == "0") { skip = 1 } else { skip = 0 }
                   if (!skip && index(whole, " " sec " ")) { print ""; print title; shown = 1 }
                   next }
  /^## / { skip = 1; next }
  skip { next }
  index(whole, " " sec " ") { print; next }
  /^- / { inbullet = 0
          for (i = 1; i <= n; i++) if (nd[i] != "" && index($0, nd[i])) { inbullet = 1; break }
          if (inbullet && !shown) { print ""; print title; shown = 1 }
          if (inbullet) print
          next }
  /^  / { if (inbullet) print; next }
  { inbullet = 0 }
' "$DIRECTIVES"
