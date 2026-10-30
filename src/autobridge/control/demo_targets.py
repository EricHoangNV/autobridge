"""Demo target registry loader (REQ-022 owner check 2026-10-29).

The control plane's demo targets (canonical simulator ids, accepted
aliases, host admin ports, extra admin resets) live in
``config/demo_targets.yaml`` — the single registry. This module loads and
validates that file; it names no simulator itself, so the "never seen"
vendor proof (no vendor literal under ``src/autobridge``) keeps passing.

Resolution order: ``DEMO_TARGETS_FILE`` env override, else
``config/demo_targets.yaml`` next to the repo root (derived from this
file's location, which also holds inside the engine image where the
repo root ships as ``/srv``).
"""

from __future__ import annotations

import functools
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import yaml

ENV_OVERRIDE = "DEMO_TARGETS_FILE"
CONFIG_RELATIVE = Path("config") / "demo_targets.yaml"

#: Env var selecting service-DNS routing when set (compose homology with
#: ``config/demo_targets.yaml`` admin routing; see ``routes/demo.py``).
ADMIN_NET_HOST_ENV = "ADMIN_NET_HOST"

#: Internal public-API port every simulator serves inside Compose
#: (``compose.yaml`` publishes ``<loopback>:<public_port>:8000``; the
#: registry cross-check test fails if a sim service stops using it).
COMPOSE_PUBLIC_PORT = 8000


class DemoTargetsError(ValueError):
    """The registry file is missing, unreadable or invalid."""


@dataclass(frozen=True)
class DemoTarget:
    """One registry entry: canonical id, aliases, host admin port."""

    id: str
    aliases: tuple[str, ...]
    host_admin_port: int
    extra_resets: tuple[tuple[str, str], ...]  # (entry suffix, admin path)
    # Display metadata for the simulators index (REQ-022 E). Optional so
    # synthetic test registries stay minimal; validated when present.
    display_name: str = ""
    api_style: str = ""
    public_port: int = 0
    docs_source: str = ""
    # REQ-053 items 8/9: what a UI build pre-fills when the operator starts
    # from this simulator's docs — the same credential_ref and entities
    # ``make demo`` sends, so the UI path stops leaving them at form
    # defaults (wrong credential ⇒ Blocked before any vendor request).
    # Env key NAMES only, never values (DIRECTIVES §6).
    suggested_credential_ref: str = ""
    suggested_target_entities: tuple[str, ...] = ()


@dataclass(frozen=True)
class DemoTargets:
    """Validated registry: ordered entries plus lookup maps."""

    targets: tuple[DemoTarget, ...]
    alias_map: dict[str, str]  # every accepted key (lower-cased) -> canonical id
    reset_targets: tuple[str, ...]  # canonical ids, in file order
    host_admin_ports: dict[str, int]  # canonical id -> loopback admin port
    extra_resets: dict[str, tuple[tuple[str, str], ...]]  # canonical id -> entries


def default_targets_path() -> Path:
    """Registry file location: env override, else the repo checkout."""
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return Path(override)
    # src/autobridge/control/demo_targets.py -> repo root is parents[3].
    return Path(__file__).resolve().parents[3] / CONFIG_RELATIVE


def _optional_text(item: dict, key: str, where: str) -> str:
    """Optional display string: missing/blank is allowed, other types fail."""
    value = item.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DemoTargetsError(f"{where}: {key!r} must be a string")
    return value.strip()


def _optional_port(item: dict, key: str, where: str) -> int:
    """Optional display port: 0 means unset; otherwise a TCP port number."""
    value = item.get(key, 0)
    if value is None or value == 0:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise DemoTargetsError(f"{where}: {key!r} must be a TCP port number")
    return value


def _optional_entities(item: dict, key: str, where: str) -> list[str]:
    """Optional entity-name list: missing/blank is allowed, else non-empty strings."""
    value = item.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise DemoTargetsError(f"{where}: {key!r} must be a list of non-empty strings")
    return [v.strip() for v in value]


def load_demo_targets(path: str | Path | None = None) -> DemoTargets:
    """Load and validate the registry file; fail closed on any problem."""
    resolved = Path(path) if path is not None else default_targets_path()
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DemoTargetsError(
            f"demo targets registry missing: {resolved} "
            f"(set {ENV_OVERRIDE} to point at it)"
        ) from exc
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DemoTargetsError(f"demo targets registry unreadable: {resolved}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("targets"), list) or not raw["targets"]:
        raise DemoTargetsError(f"demo targets registry invalid: {resolved}: needs a non-empty 'targets' list")
    entries: list[DemoTarget] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for index, item in enumerate(raw["targets"]):
        where = f"{resolved} targets[{index}]"
        if not isinstance(item, dict):
            raise DemoTargetsError(f"{where}: entry must be a mapping")
        target_id = item.get("id")
        if not isinstance(target_id, str) or not target_id.strip():
            raise DemoTargetsError(f"{where}: 'id' must be a non-empty string")
        if target_id in seen_ids:
            raise DemoTargetsError(f"{where}: duplicate id {target_id!r}")
        seen_ids.add(target_id)
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise DemoTargetsError(f"{where}: 'aliases' must be a list of non-empty strings")
        port = item.get("host_admin_port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise DemoTargetsError(f"{where}: 'host_admin_port' must be a TCP port number")
        extra_raw = item.get("extra_resets", [])
        if not isinstance(extra_raw, list):
            raise DemoTargetsError(f"{where}: 'extra_resets' must be a list")
        extra: list[tuple[str, str]] = []
        for entry in extra_raw:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("suffix"), str)
                or not entry["suffix"].strip()
                or not isinstance(entry.get("path"), str)
                or not entry["path"].startswith("/")
            ):
                raise DemoTargetsError(f"{where}: extra_resets entries need a 'suffix' and an absolute 'path'")
            extra.append((entry["suffix"], entry["path"]))
        for key in [target_id, *aliases]:
            lowered = key.strip().lower()
            if lowered in seen_keys:
                raise DemoTargetsError(f"{where}: duplicate alias {key!r}")
            seen_keys.add(lowered)
        entries.append(
            DemoTarget(
                id=target_id,
                aliases=tuple(a.strip() for a in aliases),
                host_admin_port=port,
                extra_resets=tuple(extra),
                display_name=_optional_text(item, "display_name", where),
                api_style=_optional_text(item, "api_style", where),
                public_port=_optional_port(item, "public_port", where),
                docs_source=_optional_text(item, "docs_source", where),
                suggested_credential_ref=_optional_text(item, "suggested_credential_ref", where),
                suggested_target_entities=tuple(
                    _optional_entities(item, "suggested_target_entities", where)
                ),
            )
        )
    alias_map = {t.id.lower(): t.id for t in entries}
    for t in entries:
        for a in t.aliases:
            alias_map[a.lower()] = t.id
    return DemoTargets(
        targets=tuple(entries),
        alias_map=alias_map,
        reset_targets=tuple(t.id for t in entries),
        host_admin_ports={t.id: t.host_admin_port for t in entries},
        extra_resets={t.id: t.extra_resets for t in entries if t.extra_resets},
    )


@functools.lru_cache(maxsize=1)
def get_demo_targets() -> DemoTargets:
    """Process-wide registry (what the control service loads at startup)."""
    return load_demo_targets()


def known_target_ids(registry: DemoTargets | None = None) -> list[str]:
    """Every accepted target key (canonical ids first, then aliases)."""
    reg = registry or get_demo_targets()
    ids = [t.id for t in reg.targets]
    aliases = sorted(k for k in reg.alias_map if k not in {t.id.lower() for t in reg.targets})
    return [*ids, *aliases]


def in_compose_network() -> bool:
    """True when this process runs inside the Compose network (REQ-027).

    The single mode decider for simulator addresses: ``ADMIN_NET_HOST`` set
    (compose homology) or ``/.dockerenv`` present (inside any container).
    Host processes (local mode, e2e, demo script) see neither and get
    loopback addresses. Admin URLs (``routes/demo.py``) and data-plane
    base URLs below both go through here, so no Compose hostname leaks
    into local mode and no loopback leaks into Compose.
    """
    if os.environ.get(ADMIN_NET_HOST_ENV, "").strip():
        return True
    try:
        return os.path.exists("/.dockerenv")
    except Exception:
        return False


def public_base_url_for_target(name: str, registry: DemoTargets | None = None, *, compose: bool | None = None) -> str:
    """Mode-correct public base URL for a simulator (REQ-027 sub-step 1).

    *name* is a canonical id or alias (case-insensitive). Compose mode
    returns service DNS (``http://<canonical-id>:8000``); host mode returns
    loopback (``http://localhost:<public_port>``) per ``demo_targets.yaml``.
    Unknown names fail closed; ``compose=None`` auto-detects via
    :func:`in_compose_network` (explicit only in tests).
    """
    reg = registry or get_demo_targets()
    key = (name or "").strip().lower()
    canonical = reg.alias_map.get(key)
    if canonical is None:
        raise DemoTargetsError(f"unknown demo target {name!r}; known: {', '.join(t.id for t in reg.targets)}")
    target = next(t for t in reg.targets if t.id == canonical)
    use_compose = in_compose_network() if compose is None else compose
    if use_compose:
        return f"http://{canonical}:{COMPOSE_PUBLIC_PORT}"
    if not target.public_port:
        raise DemoTargetsError(f"demo target {canonical!r} has no public_port for host mode")
    return f"http://localhost:{target.public_port}"


def normalize_connector_base_url(url: str | None, registry: DemoTargets | None = None, *, compose: bool | None = None) -> str | None:
    """Rewrite a simulator address to the form that works in this stack mode.

    Recognized inputs — a registry id/alias as hostname (``sim-atlas``,
    bare ``atlas``, any port) or loopback with a registry public port —
    become :func:`public_base_url_for_target`. Everything else passes
    through untouched: ``${VAR}`` placeholders (resolved per-mode from the
    environment at sync time), real vendor hosts, and unparseable values.
    """
    if not url or not isinstance(url, str):
        return url
    text = url.strip()
    if not text or text.startswith("${"):
        return url
    try:
        parts = urllib.parse.urlsplit(text)
    except Exception:
        return url
    if parts.scheme not in ("http", "https"):
        return url
    try:
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return url
    reg = registry or get_demo_targets()
    if host in reg.alias_map:
        return public_base_url_for_target(reg.alias_map[host], reg, compose=compose)
    if host in ("localhost", "127.0.0.1") and port is not None:
        by_port = {t.public_port: t.id for t in reg.targets if t.public_port}
        if port in by_port:
            return public_base_url_for_target(by_port[port], reg, compose=compose)
    return url
