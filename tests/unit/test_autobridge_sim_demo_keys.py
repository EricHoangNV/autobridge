"""REQ-027 owner check (sim keys by default): a fresh clone syncs green.

Every simulator credential a demo sync uses ships a working demo value in
both modes: `scripts/local-up.sh` exports `KEY=${KEY:-<demo>}`, `compose.yaml`
passes the same default to the engine services (control + worker), and
`.env.example` documents the demo values. An operator-set value always wins
(`:-` only fills unset/empty). These tests pin that contract offline so
`make test` catches a regression without the stack.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: credential_ref env keys the demo's live syncs need, with the demo value
#: each default must provide. Meridian's pair must equal the sim's documented
#: values (simulators/meridianpms/app.py VALID_*); the rest accept any
#: non-empty key.
DEMO_KEYS = {
    "ATLAS_API_KEY": "sim-demo-key",
    "HARBOR_API_KEY": "sim-demo-key",
    "LEGACYSTAY_API_KEY": "sim-demo-key",
    "MERIDIAN_CLIENT_TOKEN": "meridian-client-token",
    "MERIDIAN_ACCESS_TOKEN": "meridian-access-token",
}


def _local_up() -> str:
    return (REPO_ROOT / "scripts" / "local-up.sh").read_text(encoding="utf-8")


def _compose() -> str:
    return (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def test_local_up_exports_demo_key_defaults() -> None:
    """local-up.sh gives every demo key a non-empty default (explicit env wins)."""
    text = _local_up()
    for key, demo in DEMO_KEYS.items():
        match = re.search(rf"^export {key}=\"\$\{{{key}:-(.*?)\}}\"$", text, re.MULTILINE)
        assert match is not None, f"local-up.sh must export a default for {key}"
        assert match.group(1) == demo, f"{key} default {match.group(1)!r} != {demo!r}"


def _service_env_block(compose: str, service: str) -> str:
    match = re.search(rf"^  {service}:\n(?:.*\n)*?    environment:\n((?:      .*\n)+)", compose, re.MULTILINE)
    assert match is not None, f"compose service {service} has no environment block"
    return match.group(1)


def test_compose_engine_services_carry_demo_key_defaults() -> None:
    """control + worker (where syncs execute) pass the same demo defaults."""
    compose = _compose()
    for service in ("control", "worker"):
        block = _service_env_block(compose, service)
        for key, demo in DEMO_KEYS.items():
            match = re.search(rf"^\s*{key}: \$\{{{key}:-(.*?)\}}$", block, re.MULTILINE)
            assert match is not None, f"compose {service} must default {key}"
            assert match.group(1) == demo, f"compose {service} {key} default {match.group(1)!r} != {demo!r}"


def test_env_example_documents_demo_values() -> None:
    """`.env.example` ships the demo values so `cp .env.example .env` works."""
    values = _env_example()
    for key, demo in DEMO_KEYS.items():
        assert values.get(key) == demo, f".env.example {key} must be the demo value {demo!r}"


def test_meridian_demo_pair_matches_simulator() -> None:
    """The Meridian demo pair must equal what the sim validates (exact match)."""
    app = (REPO_ROOT / "simulators" / "meridianpms" / "app.py").read_text(encoding="utf-8")
    assert 'VALID_CLIENT_TOKEN = "meridian-client-token"' in app
    assert 'VALID_ACCESS_TOKEN = "meridian-access-token"' in app
