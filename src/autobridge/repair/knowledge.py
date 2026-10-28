"""L0 knowledge lookup — profile cache keyed by vendor + fingerprint delta (P1.W4.4).

The cache stores successful patches keyed by (vendor, fingerprint delta signature).
A hit returns the same patch without model calls, producing frontier_tokens == 0.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from autobridge.drift.fingerprint import FingerprintDelta


def _delta_to_dict(delta: FingerprintDelta | dict[str, Any] | None) -> dict[str, Any]:
    if delta is None:
        return {}
    if isinstance(delta, dict):
        # Already dict form; normalize keys
        return dict(delta)
    if isinstance(delta, FingerprintDelta):
        return {
            "fingerprint_changed": delta.fingerprint_changed,
            "pagination_changed": delta.pagination_changed,
            "keys_changed": delta.keys_changed,
            "types_changed": delta.types_changed,
            "changed_entities": list(delta.changed_entities),
            "pagination_details": dict(delta.pagination_details),
            "keys_details": dict(delta.keys_details),
            "types_details": dict(delta.types_details),
        }
    return {}


def delta_signature(delta: FingerprintDelta | dict[str, Any] | None) -> str:
    """Deterministic short hash of fingerprint delta content."""
    d = _delta_to_dict(delta)
    # Use only stable keys; ignore empty
    canonical = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def cache_key(vendor: str, delta: FingerprintDelta | dict[str, Any] | None) -> str:
    """Key for L0 lookup: vendor + delta signature."""
    vendor_clean = (vendor or "unknown").strip().lower()
    sig = delta_signature(delta)
    return f"{vendor_clean}::{sig}"


def _canonical_patch(patch: list[dict[str, Any]] | None) -> str:
    """Canonical form for patch equality (order-sensitive, RFC 6902)."""
    return json.dumps(list(patch or []), sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """One cached successful repair."""

    vendor: str
    delta_key: str
    patch: list[dict[str, Any]]
    rule_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    drift_class: str = ""
    confidence: float = 0.0


class KnowledgeCache:
    """In-memory profile cache for L0 knowledge lookup.

    Keyed by vendor + fingerprint delta signature (plan §2.5).
    The cache is process-local for W4.4; later it can be backed by DB
    (e.g. a ConnectorRelease lookup by spec_signature + drift_class).
    """

    def __init__(self) -> None:
        self._store: dict[str, KnowledgeEntry] = {}

    def put(
        self,
        vendor: str,
        delta: FingerprintDelta | dict[str, Any] | None,
        patch: list[dict[str, Any]],
        rule_id: str,
        *,
        drift_class: str = "",
        confidence: float = 0.0,
    ) -> KnowledgeEntry:
        """Store a successful patch for (vendor, delta). Returns the entry."""
        key = cache_key(vendor, delta)
        entry = KnowledgeEntry(
            vendor=vendor,
            delta_key=key,
            patch=list(patch),
            rule_id=rule_id,
            drift_class=drift_class,
            confidence=confidence,
        )
        self._store[key] = entry
        return entry

    def lookup(
        self,
        vendor: str,
        delta: FingerprintDelta | dict[str, Any] | None,
    ) -> KnowledgeEntry | None:
        """Return cached entry for (vendor, delta) or None on miss."""
        key = cache_key(vendor, delta)
        return self._store.get(key)

    def contains(self, vendor: str, delta: FingerprintDelta | dict[str, Any] | None) -> bool:
        return cache_key(vendor, delta) in self._store

    def evict(self, vendor: str, delta: FingerprintDelta | dict[str, Any] | None) -> bool:
        """Drop the entry for (vendor, delta). Returns True when one existed.

        AR-2-3: reject/rollback paths use this (via ``evict_patch``) so a
        human-rejected or rolled-back patch is never re-proposed at L0.
        """
        return self._store.pop(cache_key(vendor, delta), None) is not None

    def evict_patch(self, patch: list[dict[str, Any]] | None) -> int:
        """Drop every entry whose patch equals *patch*. Returns evicted count.

        Patch identity (not the delta key) is used because reject/rollback
        know the repair run's candidate patch but not the (vendor, delta)
        key it was cached under; the same rejected patch cached under
        several delta keys must all go.
        """
        if not patch:
            return 0
        target = _canonical_patch(patch)
        doomed = [k for k, e in self._store.items() if _canonical_patch(e.patch) == target]
        for k in doomed:
            del self._store[k]
        return len(doomed)

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)

    def all_entries(self) -> list[KnowledgeEntry]:
        return list(self._store.values())

    # Convenience for vendor extracted from spec
    def lookup_for_spec(
        self,
        spec: dict[str, Any],
        delta: FingerprintDelta | dict[str, Any] | None,
    ) -> KnowledgeEntry | None:
        vendor = ""
        try:
            vendor = spec.get("connector", {}).get("vendor", "") if isinstance(spec.get("connector"), dict) else ""
        except Exception:
            vendor = ""
        return self.lookup(vendor or "unknown", delta)

    def put_for_spec(
        self,
        spec: dict[str, Any],
        delta: FingerprintDelta | dict[str, Any] | None,
        patch: list[dict[str, Any]],
        rule_id: str,
        *,
        drift_class: str = "",
        confidence: float = 0.0,
    ) -> KnowledgeEntry:
        vendor = ""
        try:
            vendor = spec.get("connector", {}).get("vendor", "") if isinstance(spec.get("connector"), dict) else ""
        except Exception:
            vendor = ""
        return self.put(vendor or "unknown", delta, patch, rule_id, drift_class=drift_class, confidence=confidence)


# Module-level singleton used by ladder default path (tests inject their own)
_default_cache: KnowledgeCache | None = None


def get_default_cache() -> KnowledgeCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = KnowledgeCache()
    return _default_cache


def reset_default_cache() -> None:
    global _default_cache
    if _default_cache is not None:
        _default_cache.clear()
        _default_cache = None
