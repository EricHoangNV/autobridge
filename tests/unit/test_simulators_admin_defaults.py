"""REQ-035: simulator admin auth is a token boundary with a demo default.

A fresh clone must never run header-only: `.env.example`, `compose.yaml`
and `scripts/local-up.sh` all ship the labelled demo value
`sim-admin-dev-token` (an operator-set value always wins via `:-`), and
`scripts/smoke.sh` distinguishes "reachable but rejected (401/403)" from
"no route" — failing only when an unauthorized admin access is ACCEPTED.
These tests pin that contract offline so `make test` catches a regression
without the stack.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

DEMO_ADMIN_TOKEN = "sim-admin-dev-token"


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def test_env_example_ships_demo_admin_token() -> None:
    """`cp .env.example .env` must never leave the admin API header-only."""
    values = _env_example()
    assert values.get("SIM_ADMIN_TOKEN") == DEMO_ADMIN_TOKEN


def test_compose_defaults_demo_admin_token_everywhere() -> None:
    """Every compose SIM_ADMIN_TOKEN entry defaults to the demo value."""
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    occurrences = re.findall(r"SIM_ADMIN_TOKEN: \$\{SIM_ADMIN_TOKEN:-(.*?)\}", compose)
    assert len(occurrences) >= 6, "control + sims must all pass SIM_ADMIN_TOKEN"
    for default in occurrences:
        assert default == DEMO_ADMIN_TOKEN, f"compose default {default!r} != {DEMO_ADMIN_TOKEN!r}"


def test_local_up_exports_demo_admin_token_default() -> None:
    """local-up.sh gives the admin token the same demo default (explicit env wins)."""
    text = (REPO_ROOT / "scripts" / "local-up.sh").read_text(encoding="utf-8")
    match = re.search(r'^export SIM_ADMIN_TOKEN="\$\{SIM_ADMIN_TOKEN:-(.*?)\}\"$', text, re.MULTILINE)
    assert match is not None, "local-up.sh must export a default for SIM_ADMIN_TOKEN"
    assert match.group(1) == DEMO_ADMIN_TOKEN


def test_smoke_distinguishes_rejected_from_no_route() -> None:
    """Admin GET probes refuse honestly: 401/403 and no-route pass, 2xx fails."""
    text = (REPO_ROOT / "scripts" / "smoke.sh").read_text(encoding="utf-8")
    assert "probe_admin_denied()" in text, "smoke must define probe_admin_denied"
    assert "401|403" in text, "rejected (401/403) must be an explicit pass branch"
    assert "NOROUTE" in text, "no-route must be an explicit pass branch"
    # No bare reachability probe may remain on an admin port: a 403 would
    # masquerade as "unreachable" instead of "reachable but rejected".
    bare = re.findall(r"^probe \S+ http://\S+:8001/\S+ down$", text, re.MULTILINE)
    assert bare == [], f"admin ports must use probe_admin_denied, not probe: {bare}"
    for svc_url in (
        "runtime-sandbox http://sim-atlas:8001/healthz",
        "runtime-sandbox http://sim-harbor:8001/healthz",
        "runtime-sandbox http://sim-legacystay:8001/healthz",
        "runtime-sandbox http://sim-meridian:8001/healthz",
    ):
        assert f"probe_admin_denied {svc_url}" in text, f"missing: {svc_url}"


def test_smoke_write_checks_fail_only_on_accept() -> None:
    """Unauthorized admin writes fail smoke only when ACCEPTED (2xx)."""
    text = (REPO_ROOT / "scripts" / "smoke.sh").read_text(encoding="utf-8")
    assert 'probe_post runtime-sandbox http://sim-atlas:8001/admin/reset "{}" down' in text
    assert "X-Admin-Network': 'admin-net'}\" down" in text
