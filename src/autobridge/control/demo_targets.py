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
from dataclasses import dataclass
from pathlib import Path

import yaml

ENV_OVERRIDE = "DEMO_TARGETS_FILE"
CONFIG_RELATIVE = Path("config") / "demo_targets.yaml"


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
