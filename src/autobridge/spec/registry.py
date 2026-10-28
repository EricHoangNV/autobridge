"""On-disk release registry for ConnectorSpecs (P1.W1.3).

Releases live under ``generated/connectors/<connector_id>/<release_id>/`` as
``spec.yaml`` plus ``build-report.json``. Directories are written once:
storing over an existing release raises :class:`ReleaseExistsError`, which is
the file-level counterpart of ``ConnectorRelease`` immutability (plan §2.3).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from autobridge.spec.models import (
    ConnectorSpec,
    load_spec_from_dict,
    signature_canonical_data,
    spec_to_json_data,
)

CONNECTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

SPEC_FILENAME = "spec.yaml"
REPORT_FILENAME = "build-report.json"


class ReleaseExistsError(FileExistsError):
    """A release already exists at this id — releases are write-once."""


class ReleaseNotFoundError(FileNotFoundError):
    """No release stored at this id."""


class ReleaseCorruptError(ValueError):
    """Stored release files are missing or fail validation."""


def default_base_dir() -> Path:
    """Repo ``generated/connectors`` resolved from this file."""
    return Path(__file__).resolve().parents[3] / "generated" / "connectors"


def _checked_id(value: str, pattern: re.Pattern[str], what: str) -> str:
    if value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError(f"{what} {value!r} must not contain path separators")
    if not pattern.match(value):
        raise ValueError(f"{what} {value!r} is not a valid id")
    return value


def spec_signature(spec: dict[str, Any] | ConnectorSpec) -> str:
    """sha256 over the canonical JSON form (sorted keys, compact separators).

    V1 specs hash byte-identically to before spec v2 existed: v2-only
    fields at their defaults are omitted via
    :func:`signature_canonical_data` (REQ-020 owner check 2026-10-28).
    """
    data = spec_to_json_data(spec) if isinstance(spec, ConnectorSpec) else spec
    canonical = json.dumps(
        signature_canonical_data(data), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _release_dir(
    base_dir: str | Path, connector_id: str, release_id: str, *, must_exist: bool
) -> Path:
    _checked_id(connector_id, CONNECTOR_ID_RE, "connector_id")
    _checked_id(release_id, RELEASE_ID_RE, "release_id")
    base = Path(base_dir).resolve()
    target = (base / connector_id / release_id).resolve()
    if not target.is_relative_to(base):  # pragma: no cover - regex ids cannot escape; backstop
        raise ValueError(f"release path escapes base dir: {target}")
    if must_exist and not target.is_dir():
        raise ReleaseNotFoundError(f"no release {connector_id}/{release_id} in {base}")
    return target


def release_exists(
    base_dir: str | Path, connector_id: str, release_id: str
) -> bool:
    """Return True when this release is already stored."""
    target = _release_dir(base_dir, connector_id, release_id, must_exist=False)
    return target.is_dir()


def store_release(
    base_dir: str | Path,
    connector_id: str,
    release_id: str,
    spec: dict[str, Any] | ConnectorSpec,
    build_report: dict[str, Any],
) -> Path:
    """Validate *spec*, then write the release directory exactly once.

    The stored ``build-report.json`` records connector/release ids plus the
    spec signature. Raises :class:`ReleaseExistsError` when the release
    already exists and ``ValidationError`` when the spec is invalid —
    in both cases nothing is written.
    """
    parsed = spec if isinstance(spec, ConnectorSpec) else load_spec_from_dict(spec)
    try:
        report_extra = json.loads(json.dumps(build_report, sort_keys=True))
    except (TypeError, ValueError) as e:
        raise ValueError(f"build_report is not JSON-serializable: {e}") from e
    target = _release_dir(base_dir, connector_id, release_id, must_exist=False)
    if target.exists():
        raise ReleaseExistsError(f"release {connector_id}/{release_id} already stored (immutable)")
    report = {
        "connector_id": connector_id,
        "release_id": release_id,
        "signature": spec_signature(parsed),
        **report_extra,
    }
    target.mkdir(parents=True, exist_ok=False)
    try:
        (target / SPEC_FILENAME).write_text(
            yaml.safe_dump(spec_to_json_data(parsed), sort_keys=False), encoding="utf-8"
        )
        (target / REPORT_FILENAME).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except Exception:
        # Never leave a half-written release behind.
        for child in (target / SPEC_FILENAME, target / REPORT_FILENAME):
            with contextlib.suppress(OSError):
                child.unlink()
        with contextlib.suppress(OSError):
            target.rmdir()
        raise
    return target


def load_release(
    base_dir: str | Path, connector_id: str, release_id: str
) -> tuple[ConnectorSpec, dict[str, Any]]:
    """Load and re-validate a stored release; fail when files are corrupt."""
    target = _release_dir(base_dir, connector_id, release_id, must_exist=True)
    spec_file = target / SPEC_FILENAME
    report_file = target / REPORT_FILENAME
    if not spec_file.is_file() or not report_file.is_file():
        raise ReleaseCorruptError(f"release {connector_id}/{release_id} is missing files")
    try:
        data = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (yaml.YAMLError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ReleaseCorruptError(f"release {connector_id}/{release_id} is unreadable: {e}") from e
    if not isinstance(data, dict) or not isinstance(report, dict):
        raise ReleaseCorruptError(f"release {connector_id}/{release_id} has malformed files")
    try:
        parsed = load_spec_from_dict(data)
    except Exception as e:
        raise ReleaseCorruptError(f"release {connector_id}/{release_id} spec invalid: {e}") from e
    if report.get("signature") != spec_signature(parsed):
        raise ReleaseCorruptError(f"release {connector_id}/{release_id} signature mismatch")
    return parsed, report


def list_releases(base_dir: str | Path, connector_id: str) -> list[str]:
    """Sorted release ids stored for a connector (empty when none)."""
    _checked_id(connector_id, CONNECTOR_ID_RE, "connector_id")
    connector_dir = Path(base_dir) / connector_id
    if not connector_dir.is_dir():
        return []
    return sorted(p.name for p in connector_dir.iterdir() if p.is_dir())
