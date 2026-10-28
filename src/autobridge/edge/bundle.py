"""Signed declarative command bundle (REQ-022 sub-step B).

The edge agent executes only declarative spec data — never generated code.
The mapping from cloud command kinds (``wakeup.set``, ``guest.in``, …) to
FIAS records lives in ``agent_bundle.json`` next to this module: record
type, field templates with ``{param}`` placeholders, and which answer
completes the command (``ack`` or a record type like ``KA``).

The bundle is a verified, signed release (AR-2-4), reusing the exact
:func:`release_signature` / :func:`verify_release_signature` primitive the
signed-release path uses, keyed by the same ``EDGE_RELEASE_SECRET``:

* secret set + ``EDGE_BUNDLE_SIGNATURE`` set → HMAC verified, fail closed;
* secret set + signature unset → refuse (a keyed deployment must pin the
  bundle, otherwise anyone's mapping would run under the production key);
* secret unset + signature set → unkeyed checksum verified;
* neither set → local-dev mode (accepted, reported unsigned).

Substitution is strict: every ``{placeholder}`` must be supplied, values
must be wire-safe (ASCII, no ``|`` or control bytes — the FIAS wire rule),
and unknown command kinds are ``rejected`` before anything is sent.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from autobridge.runtime.targets import (
    TargetConfigError,
    release_signature,
    verify_release_signature,
)

#: Env var carrying the pinned bundle signature (mint with
#: ``uv run python -c "from autobridge.edge.bundle import mint; print(mint())"``).
BUNDLE_SIGNATURE_ENV_VAR = "EDGE_BUNDLE_SIGNATURE"

BUNDLE_PATH = pathlib.Path(__file__).resolve().parent / "agent_bundle.json"

#: Env var overriding the property timezone for ``now`` fill-ins
#: (site setting; the Grand Plaza default ships in the bundle).
PROPERTY_TZ_ENV_VAR = "EDGE_PROPERTY_TZ"

_WIRE_FORBIDDEN = set("|\x00\x02\x03\x06\x15")


class BundleError(TargetConfigError):
    """The command bundle is missing, unsigned (in a keyed deployment) or tampered."""


def load_bundle(path: pathlib.Path | str | None = None) -> dict[str, Any]:
    """Read and JSON-parse the bundle file (no verification yet)."""
    bundle_path = pathlib.Path(path) if path is not None else BUNDLE_PATH
    try:
        return json.loads(bundle_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BundleError(f"command bundle not found: {bundle_path}") from exc
    except json.JSONDecodeError as exc:
        raise BundleError(f"command bundle is not valid JSON: {exc}") from exc


def bundle_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert a ConnectorSpec v3 ``commands`` section to bundle data.

    The spec carries command templates as data; the agent executes the
    same ``{param}`` substitution it runs for the file bundle. The
    conversion validates shape (record codes, wire field ids, strict
    placeholders — the same rules as the bundle renderer) and fills the
    property timezone from the spec's ``timezone_source``. A release's own
    signature covers a spec-carried bundle, so no separate bundle
    signature is minted here — verify the release, not the fragment.
    """
    from autobridge.spec.models import load_spec_from_dict, spec_to_json_data  # noqa: PLC0415

    model = load_spec_from_dict(spec)
    if model.spec_version < 3 or not model.commands:
        raise BundleError("spec carries no v3 command templates (commands is empty)")
    data = spec_to_json_data(model)
    tz = ((data["source"].get("timezone_source") or {}).get("value")) or "UTC"
    commands: dict[str, Any] = {}
    for kind, template in data["commands"].items():
        entry: dict[str, Any] = {
            "record": template["record"],
            "fields": dict(template["fields"]),
            "expect": template.get("expect", "ack"),
        }
        if template.get("defaults"):
            entry["defaults"] = dict(template["defaults"])
        if template.get("now"):
            entry["now"] = list(template["now"])
        commands[kind] = entry
    bundle = {"version": model.spec_version, "property_timezone": tz, "commands": commands}
    # Same strictness as the file bundle: every kind must render with
    # sample-free shape checks (record codes, field ids, placeholders).
    for kind, entry in commands.items():
        if not isinstance(entry["record"], str) or len(entry["record"]) != 2:
            raise BundleError(f"spec command {kind!r} names a bad record {entry['record']!r}")
        for fid, template in entry["fields"].items():
            if not isinstance(fid, str) or len(fid) != 2 or not isinstance(template, str):
                raise BundleError(f"spec command {kind!r} has a bad field template {fid!r}")
    return bundle


def mint(bundle: dict[str, Any] | None = None) -> str:
    """Compute the signature to pin in ``EDGE_BUNDLE_SIGNATURE``."""
    return release_signature(bundle if bundle is not None else load_bundle())


def verify_bundle(bundle: dict[str, Any] | None = None, *, signature: str | None = None) -> dict[str, Any]:
    """Verify the bundle signature; return the bundle when accepted.

    Fail-closed whenever ``EDGE_RELEASE_SECRET`` is set (a keyed deployment
    must pin ``EDGE_BUNDLE_SIGNATURE``); dev-checksum mode otherwise.
    """
    import os

    data = bundle if bundle is not None else load_bundle()
    presented = signature if signature is not None else os.environ.get(BUNDLE_SIGNATURE_ENV_VAR, "")
    if not presented:
        if os.environ.get("EDGE_RELEASE_SECRET", ""):
            raise BundleError(
                "command bundle is unsigned: set EDGE_BUNDLE_SIGNATURE"
                " (a keyed deployment must pin the bundle)"
            )
        return data  # local-dev mode: integrity checksum only, reported by the caller
    try:
        verify_release_signature(data, presented)
    except TargetConfigError as exc:
        raise BundleError(f"command bundle signature mismatch: refusing to run ({exc})") from exc
    return data


def is_pinned() -> bool:
    """True when a bundle signature is pinned via the environment."""
    import os

    return bool((os.environ.get(BUNDLE_SIGNATURE_ENV_VAR, "") or "").strip())


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _render_template(kind: str, fid: str, template: str, params: dict[str, Any]) -> str:
    """Substitute ``{name}`` placeholders strictly (no partial renders)."""

    def _lookup(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise BundleError(f"command kind {kind!r} is missing parameter {name!r}")
        return str(params[name])

    value = _PLACEHOLDER.sub(_lookup, template)
    if "{" in value or "}" in value:
        raise BundleError(f"command kind {kind!r} leaves a placeholder unrendered in {fid}")
    if not value.isascii() or any(c in _WIRE_FORBIDDEN for c in value):
        raise BundleError(f"command kind {kind!r} has a wire-unsafe value for {fid}")
    return value


def render_command(bundle: dict[str, Any], kind: str, params: dict[str, Any]) -> tuple[str, list[tuple[str, str]], str]:
    """Render a command kind to ``(record_type, fields, expect)``.

    ``defaults`` fill missing params; ``now`` names params filled with the
    current property-local wire date/time (``date`` → ``YYMMDD``,
    ``time`` → ``HHMMSS``). Raises :class:`BundleError` for unknown kinds,
    missing placeholders or wire-unsafe values — the command is
    ``rejected`` before anything reaches the link, never guessed.
    """
    import datetime
    import os
    from zoneinfo import ZoneInfo

    commands = bundle.get("commands", {})
    if not isinstance(commands, dict) or kind not in commands:
        raise BundleError(f"unknown command kind {kind!r}")
    entry = commands[kind]
    try:
        record_type = entry["record"]
        templates = entry["fields"]
        expect = entry.get("expect", "ack")
    except (KeyError, TypeError) as exc:
        raise BundleError(f"command kind {kind!r} is malformed in the bundle") from exc
    if not isinstance(record_type, str) or len(record_type) != 2:
        raise BundleError(f"command kind {kind!r} names a bad record {record_type!r}")
    if not isinstance(templates, dict):
        raise BundleError(f"command kind {kind!r} has no field mapping")
    merged: dict[str, Any] = dict(params)
    defaults = entry.get("defaults", {})
    if isinstance(defaults, dict):
        for key, value in defaults.items():
            merged.setdefault(key, value)
    for name in entry.get("now", []) or []:
        if name not in merged:
            tz_name = (os.environ.get(PROPERTY_TZ_ENV_VAR, "") or "").strip()
            if not tz_name and isinstance(bundle.get("property_timezone"), str):
                tz_name = bundle["property_timezone"]
            try:
                now = datetime.datetime.now(ZoneInfo(tz_name or "UTC"))
            except Exception:
                now = datetime.datetime.now(datetime.UTC)
            merged[name] = now.strftime("%y%m%d") if name == "date" else now.strftime("%H%M%S")
    fields: list[tuple[str, str]] = []
    for fid, template in templates.items():
        if not isinstance(fid, str) or len(fid) != 2 or not isinstance(template, str):
            raise BundleError(f"command kind {kind!r} has a bad field template {fid!r}")
        fields.append((fid, _render_template(kind, fid, template, merged)))
    if expect != "ack" and (not isinstance(expect, str) or len(expect) != 2):
        raise BundleError(f"command kind {kind!r} expects a bad answer {expect!r}")
    return record_type, fields, str(expect)
