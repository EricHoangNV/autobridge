"""Trust-boundary import checks (REQ-003, DIRECTIVES §2).

The engine (`src/autobridge`) never imports `verifier` or `simulators`;
`verifier/` and `simulators/` never import `autobridge`. Sanctioned
exception: `verifier/` may import the shared simulator data-generator code
(`simulators/common/` and `simulators/scenarios/`) and nothing else under
`simulators/`. Complements the import-linter contracts in `pyproject.toml`,
which cannot express the narrowed exception (forbidden contracts have no
allowlist).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ENGINE_ROOT = REPO_ROOT / "src" / "autobridge"
VERIFIER_ROOT = REPO_ROOT / "verifier"
SIMULATORS_ROOT = REPO_ROOT / "simulators"

# Full dotted names the verifier may import under simulators/.
_ALLOWED_SIMULATORS_PREFIXES = ("simulators.common.", "simulators.scenarios")


def _file_imports(path: Path) -> list[str]:
    """Return the full dotted module names imported by one file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (level > 0) stay inside the current package.
            if node.level:
                continue
            if node.module:
                names.append(node.module)
    return names


def _tree_imports(root: Path) -> dict[str, list[str]]:
    """Map relative file path -> imported dotted names for a source tree."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found[str(path.relative_to(REPO_ROOT))] = _file_imports(path)
    return found


def _top(name: str) -> str:
    return name.split(".")[0]


def test_engine_imports_neither_package() -> None:
    bad = {
        rel: [n for n in names if _top(n) in {"verifier", "simulators"}]
        for rel, names in _tree_imports(ENGINE_ROOT).items()
    }
    bad = {rel: names for rel, names in bad.items() if names}
    assert not bad, f"engine imports verifier/simulators: {bad}"


def test_verifier_never_imports_engine() -> None:
    bad = {
        rel: [n for n in names if _top(n) == "autobridge"]
        for rel, names in _tree_imports(VERIFIER_ROOT).items()
    }
    bad = {rel: names for rel, names in bad.items() if names}
    assert not bad, f"verifier imports autobridge: {bad}"


def test_verifier_simulators_imports_confined_to_shared_code() -> None:
    bad: dict[str, list[str]] = {}
    for rel, names in _tree_imports(VERIFIER_ROOT).items():
        outside = [
            n
            for n in names
            if _top(n) == "simulators"
            and not n.startswith(_ALLOWED_SIMULATORS_PREFIXES)
        ]
        if outside:
            bad[rel] = outside
    assert not bad, f"verifier imports simulators outside common/scenarios: {bad}"


def test_simulators_never_imports_engine() -> None:
    bad = {
        rel: [n for n in names if _top(n) == "autobridge"]
        for rel, names in _tree_imports(SIMULATORS_ROOT).items()
    }
    bad = {rel: names for rel, names in bad.items() if names}
    assert not bad, f"simulators import autobridge: {bad}"


def test_engine_never_references_legacystay() -> None:
    """REQ-019 item 5: LegacyStay stays unseen — no vendor-specific tuning.

    No L1 rule, hypothesis enumerator, classifier, prompt or knowledge entry
    may reference LegacyStay or be developed/tuned against it. This guard
    greps ``src/autobridge`` for ``legacystay`` (case-insensitive) outside
    comments and fails on a hit.
    """
    hits: list[str] = []
    for path in sorted(ENGINE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if "legacystay" in code.lower():
                hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "engine references LegacyStay outside comments (REQ-019.5):\n" + "\n".join(hits)


def test_engine_never_references_meridian() -> None:
    """REQ-020 item 5: no Meridian-specific branch anywhere in the engine.

    ``grep`` must find no "meridian" in ``src/autobridge`` outside tests
    and fixtures — the vocabulary extension lands as generic spec data.
    """
    hits: list[str] = []
    for path in sorted(ENGINE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if "meridian" in code.lower():
                hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "engine references Meridian outside comments (REQ-020.5):\n" + "\n".join(hits)
