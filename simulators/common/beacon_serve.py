"""Beacon instance serving for the simulator admin plane (REQ-025 item 5).

The bench harness passes only the eval seed (hex) plus an instance index;
this module — simulators-side, where generator internals live — builds the
instance, stores it as active beacon state, and serves the mutation:

* record-shape families (rename, nesting, code_list, semantic, dates) are
  applied to the app's baseline dataset in
  :func:`apply_active_to_dataset` (called from
  ``helpers.get_dataset_for_current_state``);
* behaviour families (pagination, transport_auth, fias_layout, fias_link)
  are exposed via :func:`active_behavior` for the app-level hooks
  (``faults.py`` predicates, pagination overrides, FIAS PABX).

The public descriptor returned to the harness carries ``{index, vendor,
family, params}`` — the observable fault, like a public scenario catalog
entry. It never carries ``expected``, ``seed_hex`` or ``dataset_seed``:
the answer key stays verifier-side (``verifier/beacon.py``).

Trust boundary: simulators-side only. Never imported by ``autobridge``.
"""

from __future__ import annotations

from typing import Any

import simulators.common.faultgen as faultgen
from simulators.common.state import get_state, set_beacon_state


def set_beacon(eval_seed_hex: str, index: int) -> dict[str, Any]:
    """Activate a beacon instance from its eval seed + index.

    Validates strictly (fail closed: bad hex, bad index, or a combination
    the generator rejects all raise); stores ``(eval_seed_hex, index)`` in
    simulator state; returns the public descriptor.
    """
    if not isinstance(eval_seed_hex, str):
        raise ValueError("eval_seed_hex must be a string")
    try:
        eval_seed = bytes.fromhex(eval_seed_hex.strip())
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"eval_seed_hex must be hex: {exc}") from exc
    if len(eval_seed) != 32:
        raise ValueError(f"eval_seed must be 32 bytes, got {len(eval_seed)}")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > 10_000:
        raise ValueError("index must be an int in [0, 10000]")
    instance = faultgen.generate_instance(eval_seed, index)
    set_beacon_state(eval_seed_hex.strip(), index)
    return public_descriptor(instance)


def clear_beacon() -> None:
    """Deactivate any beacon instance (reset to scenario-driven serving)."""
    set_beacon_state(None, None)


def active_instance() -> dict[str, Any] | None:
    """Regenerate the active beacon instance, or None when no beacon set."""
    state = get_state()
    slot = state.get("beacon")
    if not isinstance(slot, dict):
        return None
    eval_seed_hex = slot.get("eval_seed_hex")
    index = slot.get("index")
    if not isinstance(eval_seed_hex, str) or not isinstance(index, int):
        return None
    try:
        return faultgen.generate_instance(bytes.fromhex(eval_seed_hex), index)
    except (ValueError, KeyError):
        return None


def active_behavior(vendor: str | None = None) -> dict[str, Any] | None:
    """Behaviour contract for the active beacon instance, if any.

    When *vendor* is given, returns the behaviour only if the instance
    targets that vendor (each simulator serves its own vendor).
    """
    instance = active_instance()
    if instance is None:
        return None
    if vendor is not None and str(instance["vendor"]) != vendor:
        return None
    return faultgen.behavior_for_instance(instance)


def apply_active_to_dataset(
    dataset: dict[str, list[dict[str, Any]]], vendor: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Apply the active beacon record-shape mutation to a baseline dataset.

    Behaviour families leave the dataset untouched (the app honors the
    behaviour contract separately). No active beacon — or a beacon for a
    different vendor — returns the dataset unchanged.
    """
    instance = active_instance()
    if instance is None:
        return dataset
    if vendor is not None and str(instance["vendor"]) != vendor:
        return dataset
    return faultgen.apply_instance_records(dataset, instance)


def public_descriptor(instance: dict[str, Any]) -> dict[str, Any]:
    """Public fault description (observable; never the answer key)."""
    return {
        "index": int(instance["index"]),
        "vendor": str(instance["vendor"]),
        "family": str(instance["family"]),
        "params": dict(instance["params"]),
    }


# ---------------------------------------------------------------------------
# Pagination contract serving (shared by the four REST vendors)
# ---------------------------------------------------------------------------


def pagination_contract() -> dict[str, Any] | None:
    """Active beacon pagination contract, or None.

    Each simulator process holds its own state, so the active instance is
    inherently per-vendor: the harness pushes instance ``i`` only to the
    admin plane of ``descriptor["vendor"]``.
    """
    behavior = active_behavior()
    if behavior is None or behavior.get("family") != "pagination":
        return None
    return behavior


def _parse_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (ValueError, TypeError):
        return default
    if isinstance(value, bool):
        return default
    return max(minimum, min(maximum, value))


def paginate_by_contract(
    items: list[dict[str, Any]],
    query: Any,
    contract: dict[str, Any],
    default_limit: int = 50,
) -> dict[str, Any]:
    """Slice *items* per a beacon pagination contract; build its envelope.

    ``query`` is a read mapping (``request.query_params`` or a plain dict).
    Only the contract's parameter names are read — baseline names fall back
    to defaults, so an unfixed client pages the default first page over and
    over (truncated fetch, graded as a loss). The envelope nests the page at
    ``items_path`` and, for cursor strategies, the next token at
    ``next_path`` (``None`` terminates). Offset/page strategies carry no
    server cursor: the client advances the offset/page itself.
    """
    strategy = str(contract.get("strategy", ""))
    get = query.get if hasattr(query, "get") else (lambda k, d=None: query.get(k, d))
    if strategy == "offset":
        offset = _parse_int(get(str(contract.get("offset_param", ""))), 0, 0, 10**9)
        limit = _parse_int(get(str(contract.get("limit_param", ""))), default_limit, 1, 100)
        page_items = items[offset : offset + limit]
        return _contract_envelope(page_items, None, contract)
    if strategy == "page":
        page = _parse_int(get(str(contract.get("page_param", ""))), 1, 1, 10**9)
        per_page = _parse_int(get(str(contract.get("per_page_param", ""))), default_limit, 1, 100)
        start = (page - 1) * per_page
        return _contract_envelope(items[start : start + per_page], None, contract)
    if strategy in ("cursor", "body_cursor"):
        limit = _parse_int(get(str(contract.get("limit_param", "limit"))), default_limit, 1, 100)
        raw_cursor = get(str(contract.get("cursor_param", "cursor")))
        start = cursor_index_for(raw_cursor if isinstance(raw_cursor, str) else None)
        page_items = items[start : start + limit]
        next_index = start + limit
        token: str | None = None
        if next_index < len(items):
            token = faultgen.cursor_token_for(next_index, contract)
        return _contract_envelope(page_items, token, contract)
    raise ValueError(f"unknown pagination strategy: {strategy}")


def maybe_serve_contract(request: Any, data: list[dict[str, Any]], default_limit: int = 50) -> dict[str, Any] | None:
    """Serve *data* under the active beacon pagination contract, if any.

    List endpoints call this with their (already vendor-transformed) items
    before their own pagination dispatch; ``None`` means no beacon contract
    is active and the app serves its baseline/scenario shape unchanged.
    """
    contract = pagination_contract()
    if contract is None:
        return None
    query = request.query_params if hasattr(request, "query_params") else request
    return paginate_by_contract(data, query, contract, default_limit)


def _contract_envelope(
    page_items: list[dict[str, Any]], token: str | None, contract: dict[str, Any]
) -> dict[str, Any]:
    """Build the contract envelope: items at ``items_path``, cursor at ``next_path``."""
    envelope: dict[str, Any] = {}
    _set_path(envelope, list(contract.get("items_path", ["items"])), page_items)
    next_path = contract.get("next_path")
    if isinstance(next_path, list) and next_path:
        _set_path(envelope, [str(p) for p in next_path], token)
    return envelope


def _set_path(obj: dict[str, Any], path: list[str], value: Any) -> None:
    """Set ``value`` at nested ``path``, building intermediate dicts."""
    node = obj
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value


def cursor_index_for(token: str | None) -> int:
    """Decode a contract cursor token to its start index (0 on missing/garbage)."""
    if not token:
        return 0
    import re as _re

    match = _re.match(r"^c(\d{6})_", token)
    if not match:
        return 0
    try:
        return max(0, int(match.group(1)))
    except ValueError:
        return 0
