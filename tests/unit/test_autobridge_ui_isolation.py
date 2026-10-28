"""UI trust-boundary isolation (P1.W7.8): the UI never calls simulators or verifier directly.

Checked twice:
  1. **Static compose test** — ui service is attached to engine-net only (no
     vendor-net / verifier-net / admin-net), so Docker gives it no route to
     sim public API, verifier, or simulator admin API.
  2. **Source test** — frontend source (ui/app, ui/components, ui/lib) never
     fetches a simulator or verifier URL. The connector form's BASE_URL_SUGGESTIONS
     containing sim-atlas/sim-harbor are allowed as *spec field values* (the
     connector's source.base_url), not as fetch targets.

REQ-018: the browser only calls same-origin `/api/*` (Next.js rewrites to
control, `ui/middleware.ts` injects the token server-side). No browser code
may reference `NEXT_PUBLIC_API_URL` or a control hostname (`control:8000`,
`:8101`); the server-only `CONTROL_URL` lives in `next.config.mjs`,
`ui/lib/api/base.ts` (server branch) and `compose.yaml`.

This test is the unit analogue of scripts/smoke.sh negative probes and of the
Playwright network interception in tests/e2e/test_06_ui_journey.spec.ts.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "compose.yaml"
UI_ROOT = ROOT / "ui"

# Any fetch/XHR to these hosts/ports would be a trust-boundary violation.
# REQ-018: the browser talks only to same-origin /api/* — even direct
# browser calls to control (:8101 locally, control:8000 in compose) are
# banned because they bypass the token middleware.
BANNED_FETCH_PATTERNS = [
    re.compile(r"fetch\s*\([^)]*sim-atlas", re.IGNORECASE),
    re.compile(r"fetch\s*\([^)]*sim-harbor", re.IGNORECASE),
    re.compile(r"fetch\s*\([^)]*verifier", re.IGNORECASE),
    re.compile(r"fetch\s*\([^)]*control:8000", re.IGNORECASE),
    re.compile(r"fetch\s*\([^)]*:8101", re.IGNORECASE),
    re.compile(r"fetch\s*\(\s*[^)]*:8105", re.IGNORECASE),
    re.compile(r"fetch\s*\(\s*[^)]*:8106", re.IGNORECASE),
    re.compile(r"fetch\s*\(\s*[^)]*:8104", re.IGNORECASE),
    re.compile(r"fetch\s*\(\s*[^)]*:8109", re.IGNORECASE),
    re.compile(r"fetch\s*\(\s*[^)]*:8110", re.IGNORECASE),
    re.compile(r"fetch\s*\(\s*[^)]*sim-atlas:8000", re.IGNORECASE),
    re.compile(r"fetch\s*\(\s*[^)]*sim-harbor:8000", re.IGNORECASE),
    re.compile(r"fetch\s*\(\s*[^)]*verifier:8000", re.IGNORECASE),
]

# Files that are allowed to mention sim URLs: only as base_url suggestion
# values or test fixtures (not as fetch targets). We check more narrowly:
# a banned pattern inside a fetch line is always forbidden, even in those files.
ALLOWLIST_FETCH_FILES: set[str] = set()  # no exception – fetch to sim is never allowed

# Plain string check for direct host references that would imply UI calling them.
# We allow the suggestion list and display values but not fetch.
BANNED_HOST_STRINGS = ["sim-atlas:8000", "sim-harbor:8000", "verifier:8000"]


def _load_compose() -> dict:
    with open(COMPOSE_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)  # type: ignore[no-untyped-call]


def test_ui_compose_network_isolation() -> None:
    data = _load_compose()
    ui_nets = set(data["services"]["ui"]["networks"])
    assert ui_nets == {"engine-net"}, f"ui networks must be engine-net only, got {ui_nets}"
    # Must not be able to reach vendor / verifier / admin nets
    assert "vendor-net" not in ui_nets
    assert "verifier-net" not in ui_nets
    assert "admin-net" not in ui_nets
    # Negative: ui not on verifier-net even after bench added
    assert "verifier-net" not in data["services"]["ui"]["networks"]


def test_ui_next_config_proxies_only_to_control() -> None:
    cfg = (UI_ROOT / "next.config.mjs").read_text(encoding="utf-8")
    # REQ-018: rewrite destination is the server-only CONTROL_URL
    # (compose sets http://control:8000; local default localhost:8101).
    assert "CONTROL_URL" in cfg, "next.config.mjs must rewrite /api/* via server-only CONTROL_URL"
    assert "NEXT_PUBLIC_API_URL" not in cfg, "rewrite must not use NEXT_PUBLIC_* (it leaks into the browser bundle)"
    # Must not proxy or rewrite to sim / verifier
    for bad in ("sim-atlas", "sim-harbor", "verifier:8000", ":8105", ":8106", ":8104"):
        # Allow control:8000 but not these
        if bad in cfg:
            # We explicitly allow them only if commented out; otherwise fail
            # Check they are not in a rewrite destination
            for line in cfg.splitlines():
                if bad in line and "destination" in line:
                    raise AssertionError(f"next.config rewrites to banned host {bad!r}: {line.strip()!r}")


def test_ui_source_never_fetches_simulator_or_verifier() -> None:
    """All fetch() calls in ui/lib and ui/app and ui/components must target control.

    The frontend resolves base URL via getBaseUrl() → CONTROL_URL / NEXT_PUBLIC_API_URL
    → control:8000 in compose, :8101 locally. It must never fetch
    sim-atlas/sim-harbor/verifier or ports 8104/8105/8106/8109/8110.
    """
    src_dirs = [UI_ROOT / "lib", UI_ROOT / "app", UI_ROOT / "components"]
    violations: list[str] = []
    for src_dir in src_dirs:
        if not src_dir.exists():
            continue
        for path in src_dir.rglob("*.ts"):
            text = path.read_text(encoding="utf-8")
            rel = str(path.relative_to(ROOT))
            # Skip test files – they may assert display values like base_url
            if path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
                continue
            for pat in BANNED_FETCH_PATTERNS:
                # Check per-line to give precise location, and to allow
                # BASE_URL_SUGGESTIONS which are not fetches
                for i, line in enumerate(text.splitlines(), start=1):
                    if pat.search(line):
                        # Allow suggestion list line (contains BASE_URL_SUGGESTIONS)
                        # only if the line is not a fetch call itself.
                        # Our banned patterns are fetch-prefixed, so this is already
                        # strict – plain suggestion strings won't match fetch pattern.
                        violations.append(f"{rel}:{i}: banned fetch pattern {pat.pattern!r} -> {line.strip()!r}")
            # Also check that raw host strings only appear in allowed contexts
            # (suggestion list or display). They must not be in a fetch URL.
            # This is covered by above fetch patterns.

        for path in src_dir.rglob("*.tsx"):
            text = path.read_text(encoding="utf-8")
            rel = str(path.relative_to(ROOT))
            if path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
                continue
            for pat in BANNED_FETCH_PATTERNS:
                for i, line in enumerate(text.splitlines(), start=1):
                    if pat.search(line):
                        violations.append(f"{rel}:{i}: banned fetch pattern {pat.pattern!r} -> {line.strip()!r}")
    assert not violations, "UI source fetches simulator/verifier directly:\n" + "\n".join(violations)


def test_ui_fetcher_uses_control_base_url() -> None:
    """The shared fetcher resolves to same-origin /api, never to a control hostname."""
    for candidate in [UI_ROOT / "lib" / "api" / "hooks.ts", UI_ROOT / "lib" / "api" / "client.ts"]:
        if not candidate.exists():
            continue
        text = candidate.read_text(encoding="utf-8")
        rel = str(candidate.relative_to(ROOT))
        # Must resolve via the shared apiBaseUrl helper (browser: /api, server: CONTROL_URL)
        assert "apiBaseUrl" in text, (
            f"{rel} must resolve base URL via apiBaseUrl (REQ-018, no NEXT_PUBLIC_API_URL)"
        )
        # Must not hard-code sim / verifier ports as base
        for bad in (":8105", ":8106", ":8104", "verifier:8000"):
            if bad in text:
                for line in text.splitlines():
                    if bad in line and "localhost" in line and "http" in line and ":8101" not in line:
                        raise AssertionError(f"{rel} has banned fallback host {bad!r}: {line.strip()!r}")


def test_ui_browser_bundle_hygiene() -> None:
    """REQ-018: no browser code may embed a control hostname or NEXT_PUBLIC_API_URL.

    Anything a browser file reads via NEXT_PUBLIC_* is inlined into
    .next/static at build time; the browser must only call same-origin /api.
    Server-only CONTROL_URL is allowed (next.config.mjs, base.ts server branch).
    """
    browser_files: list[pathlib.Path] = []
    for sub in ("app", "components", "lib"):
        src = UI_ROOT / sub
        if not src.exists():
            continue
        browser_files.extend(sorted(src.rglob("*.ts")))
        browser_files.extend(sorted(src.rglob("*.tsx")))
    violations: list[str] = []
    for path in browser_files:
        if path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
            continue
        # Next.js route handlers (app/**/route.ts) run server-side and may use
        # the server-only CONTROL_URL fallback; the ban is for browser code.
        if path.name == "route.ts":
            continue
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(ROOT))
        # base.ts is the one place that knows the server fallback; everything
        # else must go through it.
        if "NEXT_PUBLIC_API_URL" in text:
            violations.append(f"{rel}: references NEXT_PUBLIC_API_URL (banned in browser code)")
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if "control:8000" in line and path.name != "base.ts":
                violations.append(f"{rel}:{i}: embeds control:8000 -> {stripped!r}")
            if ":8101" in line and path.name != "base.ts" and "control API at :8101" not in line:
                violations.append(f"{rel}:{i}: embeds :8101 -> {stripped!r}")
    assert not violations, "browser bundle hygiene violated:\n" + "\n".join(violations)


def test_ui_compose_sets_server_only_control_url() -> None:
    """Compose gives the ui container a server-only CONTROL_URL (REQ-018).

    Next.js evaluates rewrites() at build time, so the address must be
    present both as a build arg (baked rewrite destination) and as runtime
    env (readyz route). Neither may be a NEXT_PUBLIC_* variable.
    """
    data = _load_compose()
    ui_svc = data["services"]["ui"]
    ui_env = ui_svc.get("environment", {})
    if isinstance(ui_env, list):
        ui_env = dict(item.split("=", 1) for item in ui_env if "=" in item)
    assert ui_env.get("CONTROL_URL") == "http://control:8000", (
        f"ui service must set CONTROL_URL=http://control:8000, got {ui_env.get('CONTROL_URL')!r}"
    )
    build_args = ui_svc.get("build", {}).get("args", {})
    assert build_args.get("CONTROL_URL") == "http://control:8000", (
        "ui build must pass CONTROL_URL=http://control:8000 (rewrites() is build-time)"
    )
    dockerfile = (UI_ROOT / "Dockerfile").read_text(encoding="utf-8")
    for i, line in enumerate(dockerfile.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "NEXT_PUBLIC_" not in stripped, (
            f"ui/Dockerfile:{i} bakes a NEXT_PUBLIC_ var into the browser bundle: {stripped!r}"
        )


def test_ui_inject_fault_goes_via_control_proxy() -> None:
    """InjectFaultDrawer must POST to /demo/inject-fault on control, not to /admin directly."""
    drawer = UI_ROOT / "components" / "InjectFaultDrawer.tsx"
    if not drawer.exists():
        return
    text = drawer.read_text(encoding="utf-8")
    assert "/demo/inject-fault" in text, "InjectFaultDrawer must POST via control /demo/inject-fault"
    # Must not call simulator admin API directly via fetch
    for line in text.splitlines():
        if "fetch(" in line and "/admin" in line:
            raise AssertionError(f"InjectFaultDrawer fetches admin directly: {line.strip()!r}")
    # Explanatory text mentioning POST /admin/scenario is allowed if it also
    # documents the proxy via control plane (whitespace-normalized)
    if "/admin/scenario" in text:
        normalized = " ".join(text.split())
        assert "via the control plane" in normalized.lower(), (
            "InjectFaultDrawer mentions /admin/scenario but must explain it proxies via control"
        )


def test_playwright_ui_journey_spec_exists() -> None:
    """Plan §4 W7 requires tests/e2e/test_06_ui_journey.spec.ts (Playwright).

    The spec must be non-empty, contain the network isolation assertion, and
    walk the four screens plus the labelled Inject Fault drawer.
    """
    spec = ROOT / "tests" / "e2e" / "test_06_ui_journey.spec.ts"
    assert spec.is_file(), f"{spec} missing — required by P1.W7.8"
    text = spec.read_text(encoding="utf-8")
    assert len(text) > 1000, f"spec too short ({len(text)} chars) — should be a full journey"
    # Network isolation: must intercept requests and assert no banned host
    assert "isBannedUrl" in text or "BANNED_HOST" in text, "spec must implement banned-host check"
    assert "8105" in text and "8104" in text, "spec must list banned ports 8105/8104"
    assert "bannedRequests" in text, "spec must collect bannedRequests and assert empty"
    # Journey coverage — four screens
    for needle in [
        "/connectors",
        "/connectors/new",
        "/connectors/[id]",
        "/repairs/[id]",
        "inject-fault-drawer",
        "ladder",
        "patch-diff",
        "model-usage",
        "Demo",
        "labelled",
    ]:
        low = needle.lower()
        assert low in text.lower() or needle in text, f"spec missing journey element {needle!r}"
    # Must contain at least 4 test cases (one per screen/journey slice)
    assert text.count("test(") >= 4 or text.count('test("') >= 4, "spec should have ≥4 test cases"


def test_playwright_config_present() -> None:
    """Playwright config must exist at repo root or ui/ for `npx playwright test`."""
    root_cfg = ROOT / "playwright.config.ts"
    ui_cfg = ROOT / "ui" / "playwright.config.ts"
    assert root_cfg.is_file() or ui_cfg.is_file(), "playwright.config.ts missing at root or ui/"
    cfg_text = (root_cfg.read_text(encoding="utf-8") if root_cfg.is_file() else ui_cfg.read_text(encoding="utf-8"))
    assert "test_06_ui_journey" in cfg_text, "playwright config must target test_06_ui_journey.spec.ts"
