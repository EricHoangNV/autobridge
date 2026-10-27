"""Repair context — inputs to deterministic rules (P1.W4.3).

Each rule's ``precondition(ctx) -> bool`` and ``generate_patch(ctx) -> list``
receive a :class:`RepairContext` that bundles the spec, drift signals,
classification and fingerprint delta. No model calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RepairContext:
    """Inputs for a deterministic repair rule.

    Attributes:
        spec: ConnectorSpec as a plain dict (JSON-friendly).
        drift_class: Canonical drift class from classifier (e.g. SCHEMA_RENAME).
        signals: List of DriftSignal dicts or objects (may be empty).
        fingerprint_delta: FingerprintDelta or dict with types_details etc.
        classification: Full DriftClassification if available (optional).
        extra: Free-form evidence (raw pages sample, http status etc).
    """

    spec: dict[str, Any]
    drift_class: str = "UNKNOWN"
    signals: list[Any] = field(default_factory=list)
    fingerprint_delta: Any | None = None
    classification: Any | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        spec: dict[str, Any],
        drift_class: str = "UNKNOWN",
        *,
        signals: list[Any] | None = None,
        fingerprint_delta: Any | None = None,
        classification: Any | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RepairContext:
        return cls(
            spec=spec,
            drift_class=drift_class,
            signals=list(signals or []),
            fingerprint_delta=fingerprint_delta,
            classification=classification,
            extra=dict(extra or {}),
        )
