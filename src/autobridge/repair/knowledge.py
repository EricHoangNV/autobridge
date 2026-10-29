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


def delta_to_json(delta: FingerprintDelta | dict[str, Any] | None) -> dict[str, Any] | None:
    """JSON-safe copy of a fingerprint delta for persistence.

    Returns ``None`` when there is no delta to store, so RepairRun rows
    for content-free drifts stay null instead of holding ``{}`` (an empty
    object would be indistinguishable from an observed-but-empty delta).
    """
    if delta is None:
        return None
    d = _delta_to_dict(delta)
    if not d:
        return None
    return json.loads(json.dumps(d, sort_keys=True, separators=(",", ":"), default=str))


def delta_signature(delta: FingerprintDelta | dict[str, Any] | None) -> str:
    """Deterministic short hash of fingerprint delta content."""
    d = _delta_to_dict(delta)
    # Use only stable keys; ignore empty
    canonical = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def cache_key(vendor: str, drift_class: str, delta: FingerprintDelta | dict[str, Any] | None) -> str:
    """Key for L0 lookup: vendor + drift class + delta signature.

    REQ-028: the class is part of the key so a fix learned for one drift
    class can never fire on another (a no-patch drift matching a cached
    delta must miss). Callers must pass the classifier's drift class.
    """
    vendor_clean = (vendor or "unknown").strip().lower()
    class_clean = (drift_class or "unknown").strip().upper()
    sig = delta_signature(delta)
    return f"{vendor_clean}::{class_clean}::{sig}"


def delta_has_content(delta: FingerprintDelta | dict[str, Any] | None) -> bool:
    """True when *delta* carries an observed change worth keying on.

    REQ-028: an empty delta (no flags, no details — e.g. a transport fault
    that left no observable trace) has a constant signature shared by every
    such drift, so it must never consult or populate L0: any hit on it
    would be a cross-scenario collision, not a learned fix.
    """
    if delta is None:
        return False
    if isinstance(delta, FingerprintDelta):
        return bool(
            delta.fingerprint_changed
            or delta.pagination_changed
            or delta.keys_changed
            or delta.types_changed
            or delta.changed_entities
            or delta.pagination_details
            or delta.keys_details
            or delta.types_details
        )
    if isinstance(delta, dict):
        return any(v not in (None, False, [], {}, "") for v in delta.values())
    return False


#: Drift classes that never consult or populate L0 (REQ-028). Transport,
#: auth and capability drifts carry no spec fix: learning or reusing a
#: patch for them would poison unrelated scenarios. The ladder returns
#: before L0 for these classes; this set pins the invariant explicitly.
NO_LEARN_CLASSES = frozenset(
    {
        "RATE_LIMIT",
        "TRANSIENT",
        "AUTH_EXPIRED",
        "AUTH_REVOKED",
        "CAPABILITY_REMOVED",
    }
)


def _canonical_patch(patch: list[dict[str, Any]] | None) -> str:
    """Canonical form for patch equality (order-sensitive, RFC 6902)."""
    return json.dumps(list(patch or []), sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """One cached successful repair.

    REQ-030 2b: a fix is learned only once it is approved — entries record
    the approval that taught them (approval id, actor) and the release the
    approval activated, so the knowledge page can say "learned from
    approval <id> by <actor>".
    """

    vendor: str
    delta_key: str
    patch: list[dict[str, Any]]
    rule_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    drift_class: str = ""
    confidence: float = 0.0
    approval_id: str = ""
    actor: str = ""
    release_id: str = ""


class KnowledgeCache:
    """In-memory profile cache for L0 knowledge lookup.

    Keyed by vendor + drift class + fingerprint delta signature (plan
    §2.5, REQ-028: the class is part of the key).
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
        approval_id: str = "",
        actor: str = "",
        release_id: str = "",
    ) -> KnowledgeEntry:
        """Store a successful patch for (vendor, drift class, delta). Returns the entry."""
        key = cache_key(vendor, drift_class, delta)
        entry = KnowledgeEntry(
            vendor=vendor,
            delta_key=key,
            patch=list(patch),
            rule_id=rule_id,
            drift_class=drift_class,
            confidence=confidence,
            approval_id=approval_id,
            actor=actor,
            release_id=release_id,
        )
        self._store[key] = entry
        return entry

    def lookup(
        self,
        vendor: str,
        delta: FingerprintDelta | dict[str, Any] | None,
        *,
        drift_class: str = "",
    ) -> KnowledgeEntry | None:
        """Return cached entry for (vendor, drift class, delta) or None on miss."""
        key = cache_key(vendor, drift_class, delta)
        return self._store.get(key)

    def contains(
        self,
        vendor: str,
        delta: FingerprintDelta | dict[str, Any] | None,
        *,
        drift_class: str = "",
    ) -> bool:
        return cache_key(vendor, drift_class, delta) in self._store

    def evict(
        self,
        vendor: str,
        delta: FingerprintDelta | dict[str, Any] | None,
        *,
        drift_class: str = "",
    ) -> bool:
        """Drop the entry for (vendor, drift class, delta). Returns True when one existed.

        AR-2-3: reject/rollback paths use this (via ``evict_patch``) so a
        human-rejected or rolled-back patch is never re-proposed at L0.
        """
        return self._store.pop(cache_key(vendor, drift_class, delta), None) is not None

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
        *,
        drift_class: str = "",
    ) -> KnowledgeEntry | None:
        vendor = ""
        try:
            vendor = spec.get("connector", {}).get("vendor", "") if isinstance(spec.get("connector"), dict) else ""
        except Exception:
            vendor = ""
        return self.lookup(vendor or "unknown", delta, drift_class=drift_class)

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
