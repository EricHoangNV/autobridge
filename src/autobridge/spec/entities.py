"""Canonical entity models (P1.W1.4, plan §2.2, §2.3).

The engine is domain-independent; hospitality is only the demo domain. These
pydantic models are the engine-side view of the canonical records every
connector emits, mirroring the committed JSON Schemas in ``schemas/``:

- ``reservation.schema.json`` ↔ :class:`Reservation`
- ``folio-charge.schema.json`` ↔ :class:`FolioCharge`
- ``runtime-target.json`` ↔ :class:`RuntimeTarget`
- ``discovery-result.v0.json`` ↔ :class:`DiscoveryResult` (reserved, Phase 2 S3)

Standing rules enforced here (DIRECTIVES Quick Reference 4–5):

- Money is :class:`~decimal.Decimal` end to end — exact decimal strings with
  2 fraction digits in JSON, never float. Float (and bool) inputs are rejected.
- Dates are property-local business dates (``datetime.date``, ``YYYY-MM-DD``),
  never naive UTC datetimes. Instants (``updated_at``/``posted_at``) must be
  timezone-aware.
- Status vocabularies are closed: an unknown vendor value Blocks downstream,
  it is never guessed (plan §2.4 ``enum_map`` with ``on_unknown: block``).
"""

from __future__ import annotations

import datetime
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic.functional_validators import BeforeValidator

#: Canonical entity names every connector may emit (plan §2.4 capabilities).
CANONICAL_ENTITIES = ("Reservation", "FolioCharge")

#: Entity name → committed JSON Schema filename in ``schemas/``.
ENTITY_SCHEMA_FILES = {
    "Reservation": "reservation.schema.json",
    "FolioCharge": "folio-charge.schema.json",
}

#: Committed schema filenames for the non-entity contracts.
RUNTIME_TARGET_SCHEMA_FILE = "runtime-target.json"
DISCOVERY_RESULT_SCHEMA_FILE = "discovery-result.v0.json"

#: Runtime target kinds the engine refuses; empty since Phase 2 S2 shipped Edge.
RESERVED_RUNTIME_KINDS = ()

#: Discovery lifecycle states (Phase 2 S3); transitions only via endpoints.
DISCOVERY_STATUSES = (
    "discovered",
    "needs_confirmation",
    "authorized",
    "connected",
    "healing",
)

_BUSINESS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_money(value: Any) -> Decimal:
    """Coerce a canonical money value to ``Decimal``; reject float/bool."""
    if isinstance(value, bool):
        raise ValueError("money must be an exact decimal string or Decimal, never bool")
    if isinstance(value, float):
        raise ValueError("money must be an exact decimal string or Decimal, never float")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"money {value!r} is not a valid decimal") from None
    else:
        raise ValueError(
            f"money must be a decimal string or Decimal, got {type(value).__name__}"
        )
    if not result.is_finite():
        raise ValueError(f"money {value!r} must be finite")
    if result.as_tuple().exponent != -2:
        raise ValueError(
            f"money {value!r} must carry exactly 2 fraction digits (e.g. '19.99')"
        )
    return result


def _parse_business_date(value: Any) -> datetime.date:
    """Coerce a property-local ``YYYY-MM-DD`` business date; reject datetimes."""
    if isinstance(value, datetime.datetime):
        raise ValueError(
            f"business date {value!r} must be a property-local YYYY-MM-DD date, "
            "never a datetime"
        )
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and _BUSINESS_DATE_RE.match(value):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"business date {value!r} is not a real calendar date") from None
    raise ValueError(
        f"business date {value!r} must be a property-local YYYY-MM-DD date, "
        "never a datetime or naive UTC value"
    )


Money = Annotated[Decimal, BeforeValidator(_parse_money)]
BusinessDate = Annotated[datetime.date, BeforeValidator(_parse_business_date)]

ReservationStatus = Literal["confirmed", "checked_in", "checked_out", "cancelled", "no_show"]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
EntityKey = Annotated[str, Field(min_length=1, max_length=128)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Reservation(_Strict):
    """Canonical reservation every connector emits (plan §2.4)."""

    reservation_id: EntityKey = Field(description="Canonical key for keys/types fingerprinting")
    property_id: EntityKey = Field(description="Owning property; selects the business-date calendar")
    guest_name: str | None = Field(default=None, max_length=256)
    arrival_date: BusinessDate
    departure_date: BusinessDate
    status: ReservationStatus
    total_amount: Money
    currency: CurrencyCode
    updated_at: AwareDatetime | None = Field(default=None)

    @model_validator(mode="after")
    def _date_order(self) -> Reservation:
        if self.departure_date < self.arrival_date:
            raise ValueError(
                f"departure_date {self.departure_date} is before arrival_date {self.arrival_date}"
            )
        return self


class FolioCharge(_Strict):
    """Canonical folio charge; ``business_date`` is the posting business date."""

    charge_id: EntityKey = Field(description="Canonical key for keys/types fingerprinting")
    reservation_id: EntityKey = Field(description="Parent reservation's canonical key")
    property_id: EntityKey = Field(description="Owning property; selects the business-date calendar")
    business_date: BusinessDate
    amount: Money = Field(description="Negative values are refunds/adjustments")
    currency: CurrencyCode
    description: str | None = Field(default=None, max_length=512)
    posted_at: AwareDatetime | None = Field(default=None)


class LocalProcessTarget(_Strict):
    """In-process execution for tests and local runs."""

    kind: Literal["local_process"]
    timeout_s: int = Field(default=60, ge=1, le=3600)
    workdir: str | None = Field(default=None, min_length=1)


class DockerSandboxTarget(_Strict):
    """Isolated container with allowlist, timeout and resource limits (W3.6)."""

    kind: Literal["docker_sandbox"]
    image: str | None = Field(default=None, min_length=1)
    network_allowlist: list[str] = Field(default_factory=list)
    timeout_s: int = Field(default=60, ge=1, le=3600)
    memory_mb: int = Field(default=512, ge=64)


class EdgeTarget(_Strict):
    """Simulated edge worker (Phase 2 S2): executes a verified signed release
    on an isolated network; verify centrally, execute locally."""

    kind: Literal["edge"]
    endpoint: str | None = Field(default=None, min_length=1)
    network_allowlist: list[str] = Field(default_factory=list)
    timeout_s: int = Field(default=60, ge=1, le=3600)
    memory_mb: int = Field(default=512, ge=64)


RuntimeTarget = Annotated[
    LocalProcessTarget | DockerSandboxTarget | EdgeTarget,
    Field(discriminator="kind"),
]

DiscoveryStatus = Literal[
    "discovered", "needs_confirmation", "authorized", "connected", "healing"
]
CandidateEntity = Literal["Reservation", "FolioCharge"]


class DiscoveryResult(_Strict):
    """Reserved v0 record for Phase 2 S3 static import; unused by the v1 engine."""

    discovery_id: Annotated[str, Field(min_length=1, max_length=128)]
    status: DiscoveryStatus
    vendor: Annotated[str, Field(min_length=1, max_length=256)]
    base_url: str | None = Field(default=None, min_length=1)
    candidate_entities: list[CandidateEntity] = Field(default_factory=list)
    evidence_refs: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
    created_at: AwareDatetime | None = Field(default=None)


def schema_dir() -> Path:
    """Repo ``schemas/`` directory resolved from this file."""
    return Path(__file__).resolve().parents[3] / "schemas"


def schema_path(filename: str) -> Path:
    """Resolve one committed schema file, guarding against path traversal."""
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError(f"schema filename {filename!r} must be a bare filename")
    return schema_dir() / filename


def validate_against_schema(data: dict[str, Any], schema_filename: str) -> None:
    """Validate JSON-compatible data against a committed schema file.

    Raises :class:`jsonschema.ValidationError` naming the offending path.
    """
    import jsonschema

    schema = json.loads(schema_path(schema_filename).read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)


def validate_entity(entity_name: str, data: dict[str, Any]) -> None:
    """Validate a canonical record against its entity schema file."""
    try:
        filename = ENTITY_SCHEMA_FILES[entity_name]
    except KeyError:
        raise ValueError(f"unknown canonical entity {entity_name!r}") from None
    validate_against_schema(data, filename)


#: Reference reservation: validates against both the model and the schema file.
REFERENCE_RESERVATION: dict[str, Any] = {
    "reservation_id": "RES-1001",
    "property_id": "prop-001",
    "guest_name": "Ada Lovelace",
    "arrival_date": "2026-10-27",
    "departure_date": "2026-10-29",
    "status": "checked_in",
    "total_amount": "289.99",
    "currency": "USD",
    "updated_at": "2026-10-27T14:05:00+00:00",
}

#: Reference folio charge: a late posting — ``posted_at`` falls after midnight
#: UTC while ``business_date`` stays on the prior property-local business date.
REFERENCE_FOLIO_CHARGE: dict[str, Any] = {
    "charge_id": "CHG-9001",
    "reservation_id": "RES-1001",
    "property_id": "prop-001",
    "business_date": "2026-10-26",
    "amount": "42.50",
    "currency": "USD",
    "description": "Mini-bar",
    "posted_at": "2026-10-27T00:12:00+00:00",
}

#: Reference runtime targets, one per kind.
REFERENCE_RUNTIME_TARGETS: dict[str, dict[str, Any]] = {
    "local_process": {"kind": "local_process"},
    "docker_sandbox": {
        "kind": "docker_sandbox",
        "network_allowlist": ["atlas.example.com"],
    },
    "edge": {"kind": "edge", "network_allowlist": ["edgepos.example.com"]},
}

#: Reference discovery record: reserved v0, Phase 2 S3 static import only.
REFERENCE_DISCOVERY_RESULT: dict[str, Any] = {
    "discovery_id": "disc-harbor-001",
    "status": "discovered",
    "vendor": "HarborPMS",
    "base_url": "https://harbor.example.com",
    "candidate_entities": ["Reservation", "FolioCharge"],
    "evidence_refs": [],
    "created_at": "2026-10-27T02:00:00+00:00",
}


def load_reference_examples() -> dict[str, Any]:
    """Return copies of every reference example keyed by schema filename."""
    return {
        "reservation.schema.json": dict(REFERENCE_RESERVATION),
        "folio-charge.schema.json": dict(REFERENCE_FOLIO_CHARGE),
        "runtime-target.json": {k: dict(v) for k, v in REFERENCE_RUNTIME_TARGETS.items()},
        "discovery-result.v0.json": dict(REFERENCE_DISCOVERY_RESULT),
    }
