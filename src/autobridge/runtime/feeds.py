"""Event-feed readers (REQ-022 sub-step C, spec v3).

``EVENT`` operations declare records pushed to the on-premise agent instead
of polled over HTTP. The control plane drains them through the edge relay
outbox — the generic edge transport, not a vendor or protocol branch: the
relay operator endpoint returns stored postings, the reader filters by
record type, folds resends on the feed's dedupe keys, and hands plain
record dicts to the same mapping pipeline HTTP records go through.

Secrets: the relay operator token is read from the environment at call
time via the spec's ``credential_ref`` (never stored, logged or returned).
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from autobridge.spec.models import FeedSpec


class FeedError(Exception):
    """An event feed cannot be read (no reader, missing env, relay down).

    The sync engine maps this to a ``Blocked`` run naming
    ``feed_unavailable`` — a transport failure, never a guessed patch.
    """


#: The agent heartbeat detail carries the reconnect counter as
#: ``accepted=N`` (``links_accepted`` on the agent, incremented on each
#: re-established link). REQ-071 records it on the SyncRun so a short
#: link drop between two syncs still shows in the sync-runs table, driven
#: by the counter rather than the link state at sync time.
_ACCEPTED_RE = re.compile(r"accepted=(\d+)")

#: Cap on stored idempotency keys per edge-link snapshot (REQ-071). PABX
#: volumes are tens of keys; above the cap only counts are stored and the
#: row note falls back to count-based lost/duplicated, still from keys.
_MAX_SNAPSHOT_KEYS = 10_000

#: Timeout for the best-effort link snapshot (REQ-071). The relay was
#: just contacted by the feed read, so this stays off the sync's
#: critical path; a dead relay is a missing snapshot, never a slow sync.
_SNAPSHOT_TIMEOUT_S = 2.0


def parse_reconnects(link_detail: Any) -> int:
    """Parse the ``accepted=N`` reconnect counter from heartbeat detail.

    Missing or unparseable detail reads 0 (old heartbeats carry none);
    the counter only ever moves up, so the first snapshot is the baseline.
    """
    try:
        match = _ACCEPTED_RE.search(str(link_detail or ""))
    except Exception:
        return 0
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


class FeedReader(Protocol):
    """Something that drains records for an EVENT operation."""

    def read(self, feed: FeedSpec | dict[str, Any], operation_name: str) -> list[dict[str, Any]]:
        """Return record dicts for *feed*, oldest first."""
        ...  # pragma: no cover - protocol


def _feed_attr(feed: FeedSpec | dict[str, Any], name: str, default: Any = None) -> Any:
    if isinstance(feed, dict):
        return feed.get(name, default)
    return getattr(feed, name, default)


def fold_resends(
    records: list[dict[str, Any]], dedupe_keys: list[str] | tuple[str, ...]
) -> list[dict[str, Any]]:
    """Fold resent records: first occurrence wins per dedupe-key tuple.

    A framed peer resends the same record until it is answered (FIAS ``PS``
    until ``PA``), so the same instance routinely arrives twice. Folding on
    the declared idempotency keys keeps each posting once; records missing
    a key field are never folded (fail open toward duplication, which the
    ``unique_key`` check then catches loudly instead of silently dropping).
    """
    if not dedupe_keys:
        return list(records)
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    for rec in records:
        try:
            key = tuple(str(rec[k]) for k in dedupe_keys)
        except KeyError:
            out.append(rec)
            continue
        if any(rec.get(k) is None for k in dedupe_keys):
            out.append(rec)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


class RelayFeedReader:
    """Drain event feeds from the edge relay outbox (operator side).

    ``GET {relay_url}/v1/postings`` with the operator bearer token returns
    ``{"postings": [{record_type, fields_json, ...}]}``; this reader keeps
    the feed's record type and returns the parsed field dicts.
    """

    def __init__(self, relay_url: str, token: str) -> None:
        if not relay_url:
            raise FeedError("edge relay URL is missing (EDGE_RELAY_URL)")
        if not token:
            raise FeedError("edge relay operator token is missing (credential_ref env is unset)")
        self.relay_url = relay_url.rstrip("/")
        self._token = token

    @classmethod
    def from_env(
        cls,
        auth: Any,
        relay_url: str | None = None,
    ) -> RelayFeedReader:
        """Build from a spec ``auth`` mapping (``edge_relay`` + credential_ref).

        Reads ``EDGE_RELAY_URL`` (or the explicit *relay_url*) and the env
        key named by ``credential_ref``. Raises :class:`FeedError` naming
        what is missing — never the secret value.
        """
        ref = auth.get("credential_ref") if isinstance(auth, dict) else getattr(auth, "credential_ref", None)
        url = (relay_url or os.environ.get("EDGE_RELAY_URL", "") or "").strip()
        if not url:
            raise FeedError("EDGE_RELAY_URL is not configured for this edge_relay feed")
        if not ref:
            raise FeedError("edge_relay auth requires credential_ref (env key of the relay token)")
        token = os.environ.get(ref, "")
        if not token:
            raise FeedError(f"relay token env {ref!r} is unset or empty")
        return cls(url, token)

    @property
    def token_configured(self) -> bool:
        """Whether a token was supplied (never the value)."""
        return bool(self._token)

    def link_snapshot(self, agent_candidates: list[str]) -> dict[str, Any] | None:
        """Best-effort edge link state at sync time (REQ-071).

        Matches the agent the way the PABX panel does (by connector id,
        then connector name, then the first known agent — one relay, one
        PABX), parses the ``accepted=N`` reconnect counter from its
        heartbeat detail, and counts the relay's idempotency keys for
        that agent (total vs unique, plus the sorted key list up to
        ``_MAX_SNAPSHOT_KEYS``). Lost/duplicated counts downstream are
        computed from these keys, never assumed.

        Any failure (relay down, unexpected shape) returns None — a
        missing snapshot, never a failed sync. The token value is never
        stored or returned.
        """
        import httpx

        try:
            headers = {"Authorization": f"Bearer {self._token}"}
            # trust_env=False: relay traffic is loopback/cluster-local and
            # must never consult system proxy settings (agent-thread fork
            # safety, same as read()).
            with httpx.Client(trust_env=False, timeout=_SNAPSHOT_TIMEOUT_S) as client:
                agents_resp = client.get(f"{self.relay_url}/v1/agents", headers=headers)
                agents_resp.raise_for_status()
                agents_payload = agents_resp.json()
            agents = agents_payload.get("agents", []) if isinstance(agents_payload, dict) else []
            if not isinstance(agents, list):
                return None
            by_id = {a.get("agent_id"): a for a in agents if isinstance(a, dict)}
            match: Any = None
            for candidate in agent_candidates or []:
                if candidate and candidate in by_id:
                    match = by_id[candidate]
                    break
            if match is None and agents:
                match = agents[0]
            if not isinstance(match, dict):
                return None
            agent_id = str(match.get("agent_id") or "")
            link_up = bool(match.get("link_up", False))
            reconnects = parse_reconnects(match.get("link_detail"))
            with httpx.Client(trust_env=False, timeout=_SNAPSHOT_TIMEOUT_S) as client:
                postings_resp = client.get(
                    f"{self.relay_url}/v1/postings",
                    params={"agent_id": agent_id} if agent_id else None,
                    headers=headers,
                )
                postings_resp.raise_for_status()
                postings_payload = postings_resp.json()
            postings = postings_payload.get("postings", []) if isinstance(postings_payload, dict) else []
            if not isinstance(postings, list):
                postings = []
            keys = [str(p.get("idempotency_key")) for p in postings if isinstance(p, dict) and p.get("idempotency_key")]
            unique = len(set(keys))
            truncated = len(keys) > _MAX_SNAPSHOT_KEYS
            return {
                "agent_id": agent_id,
                "link_up": link_up,
                "reconnects": reconnects,
                "posting_keys_total": len(keys),
                "posting_keys_unique": unique,
                "posting_keys": sorted(set(keys)) if not truncated else [],
                "posting_keys_truncated": truncated,
            }
        except Exception:
            return None

    def read(self, feed: FeedSpec | dict[str, Any], operation_name: str) -> list[dict[str, Any]]:
        """Fetch, filter by record type, and fold resends."""
        import httpx

        record_type = _feed_attr(feed, "record")
        if not record_type:
            raise FeedError(f"feed for operation {operation_name!r} names no record type")
        # trust_env=False: relay traffic is loopback/cluster-local and must
        # never consult system proxy settings (agent-thread fork safety).
        try:
            with httpx.Client(trust_env=False, timeout=10.0) as client:
                resp = client.get(
                    f"{self.relay_url}/v1/postings",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except FeedError:
            raise
        except Exception as exc:
            raise FeedError(f"edge relay postings fetch failed for {operation_name!r}: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("postings"), list):
            raise FeedError(f"edge relay answered postings with an unexpected shape for {operation_name!r}")
        records: list[dict[str, Any]] = []
        for row in payload["postings"]:
            if not isinstance(row, dict) or row.get("record_type") != record_type:
                continue
            fields_json = row.get("fields_json", "{}")
            try:
                fields = json.loads(fields_json) if isinstance(fields_json, str) else dict(fields_json)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(fields, dict):
                records.append({str(k): v for k, v in fields.items()})
        dedupe_keys = _feed_attr(feed, "dedupe_keys", []) or []
        return fold_resends(records, list(dedupe_keys))
