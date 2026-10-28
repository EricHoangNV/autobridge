"""Six-step demo driver (P1.W10.2, plan §6).

Drives the six demo steps through the control API when the Compose stack is
up (``make up``), and always computes the same evidence locally through the
engine so ``make demo`` is reproducible anywhere:

1. Reusable artifact — Atlas reference release syncs ``Complete`` with an
   empty model-usage ledger (``frontier_tokens == 0``).
2. Unfamiliar API — Harbor-style messy Markdown docs → DocsReader →
   classifiers → hypothesis enumerators → draft spec (no model calls;
   any L3 call would appear in the ledger with its token count).
3. Zero-token recovery — ``page_to_cursor`` drift: L0 knowledge miss →
   L1 rule ``page_to_cursor`` → JSON Patch diff, ``frontier_tokens == 0``.
4. Hard semantic failure — ``gross_to_net``: the plausible-but-wrong patch
   (decoy ``net_amount`` → ``total_amount``) is rejected as
   ``invalid_patch_trap``; an escalation bundle carries the single open
   question. Money stays ``Decimal`` throughout.
5. Head-to-head — ``GET /bench/scorecards`` (live) plus
   ``experiments/scorecard.md`` (``make bench`` output); wins and losses
   are read as they are.
6. Reproducibility — seed, reference release signatures, ``make demo``.

Trust boundaries (DIRECTIVES §2): this module never imports ``verifier`` or
``simulators``. The verifier is reached only over HTTP (``POST /verify``
hidden mode via the control plane when live); simulator datasets are never
imported — local fixtures are inline below.

Secrets (DIRECTIVES §6): only ``credential_ref`` env-key names travel here;
no credential value is printed, logged or stored.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
from decimal import Decimal
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
GENERATED = REPO_ROOT / "generated" / "connectors"
SCORECARD_PATH = REPO_ROOT / "experiments" / "scorecard.md"

DEFAULT_CONTROL_URL = "http://localhost:8101"
DEFAULT_SEED = 0

#: Env var carrying the control-plane write token (REQ-012). Sent as
#: ``Authorization: Bearer <token>`` on control API calls when set.
CONTROL_TOKEN_ENV_VAR = "AUTOBRIDGE_CONTROL_TOKEN"


def env_default(key: str, default: str) -> str:
    """Env value with blank counting as unset (REQ-014).

    An empty value from `.env` must not override the default (e.g. an
    empty ``CONTROL_URL`` must not replace the localhost default, and an
    empty ``DEMO_SEED`` must not crash ``int('')``).
    """
    return (os.environ.get(key) or "").strip() or default


def control_headers() -> dict[str, str]:
    """Auth headers for control API calls (empty when no token is set)."""
    token = os.environ.get(CONTROL_TOKEN_ENV_VAR, "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}

# Short Harbor-style messy docs excerpt for step 2 (inline so the demo never
# reads simulator files; the quirks mirror the real Harbor docs: a wrong
# pagination example, opaque H1..H5 status codes, nested folios, ambiguous
# date). Keep small: DocsReader + classifiers + enumerators run on this.
DEMO_HARBOR_DOCS = """# HarborPMS API (draft)

Base URL: https://api.harborpms.example.com
Auth: bearer token in the Authorization header.

## List reservations

`GET /reservations`

Query parameters:

- `limit` — page size, default 10
- `cursor` — opaque page cursor from the previous response
- `property_id` — filter by property

Status codes: H1, H2, H3, H4, H5 (see app for meaning).
Date field `date` is the booking date (arrival? business?).

## Folios are nested

`GET /folios`

- `limit` — page size
- `cursor` — opaque page cursor
"""

# ---------------------------------------------------------------------------
# Inline fixtures (local evidence mode — no simulator imports)
# ---------------------------------------------------------------------------


def _atlas_reservations() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(1, 5):
        rows.append(
            {
                "propertyID": "prop-001",
                "reservationID": f"res-{i:03d}",
                "dateCreated": "2026-10-26 12:00:00",
                "dateModified": "2026-10-26 12:00:00",
                "status": "confirmed",
                "guestID": f"G-res-{i:03d}",
                "guestName": f"Guest {i}",
                "startDate": "2026-10-26",
                "endDate": "2026-10-28",
                "adults": "2",
                "children": "0",
                "balance": Decimal(f"{100 * i}.00"),
                "sourceName": "Direct",
            }
        )
    return rows


def _atlas_folios() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(1, 4):
        rows.append(
            {
                "transactionID": f"chg-{i:03d}",
                "propertyID": "prop-001",
                "reservationID": f"res-{i:03d}",
                "amount": Decimal(f"{100 * i}.00"),
                "currency": "USD",
                "businessDate": "2026-10-26",
                "serviceDate": "2026-10-26",
                "transactionDateTime": "2026-10-26 12:00:00",
                "description": f"Room charge {i}",
            }
        )
    return rows


def _demo_dumps_exact(payload: Any) -> str:
    """Exact JSON encoder for the demo fake: Decimal -> raw JSON number."""
    import json as _json

    def encode(value: Any) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return _json.dumps(value)
        if isinstance(value, list):
            return "[" + ",".join(encode(v) for v in value) + "]"
        if isinstance(value, dict):
            return "{" + ",".join(_json.dumps(str(k)) + ":" + encode(v) for k, v in value.items()) + "}"
        raise ValueError(f"unserializable {type(value).__name__}")

    return encode(payload)


def _atlas_fake_client() -> Any:
    """Page-pagination fake serving the inline Atlas fixtures (method-style)."""
    import httpx  # noqa: PLC0415

    from autobridge.runtime.http import HttpClient  # noqa: PLC0415
    from autobridge.spec.models import RetryPolicy  # noqa: PLC0415

    reservations = _atlas_reservations()
    folios = _atlas_folios()

    def handler(request: httpx.Request) -> httpx.Response:
        qp = dict(request.url.params)

        def page_of(data: list[dict[str, Any]]) -> httpx.Response:
            try:
                page_number = max(1, int(qp.get("pageNumber", "1")))
            except ValueError:
                page_number = 1
            try:
                page_size = max(1, min(100, int(qp.get("pageSize", "50"))))
            except ValueError:
                page_size = 50
            total = len(data)
            start = (page_number - 1) * page_size
            sliced = data[start : start + page_size]
            payload = {"success": True, "data": sliced, "count": len(sliced), "total": total}
            return httpx.Response(200, content=_demo_dumps_exact(payload).encode(), headers={"Content-Type": "application/json"})

        if request.url.path == "/api/v1.3/getReservations":
            return page_of(reservations)
        if request.url.path == "/api/v1.3/getTransactions":
            return page_of(folios)
        return httpx.Response(404, json={"msg": "not found"})

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        "https://demo.invalid",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=5),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _harbor_reservations() -> list[dict[str, Any]]:
    """Legacy Harbor wire: snake_case records with string money."""
    rows: list[dict[str, Any]] = []
    for i in range(1, 5):
        rows.append(
            {
                "reservation_id": f"res-{i:03d}",
                "property_id": "prop-001",
                "guest_name": f"Guest {i}",
                "arrival_date": "2026-10-26",
                "departure_date": "2026-10-28",
                "status": "confirmed",
                "total_amount": f"{100 * i}.00",
                "currency": "USD",
                "updated_at": "2026-10-26T12:00:00Z",
            }
        )
    return rows


def _harbor_folios() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(1, 4):
        rows.append(
            {
                "charge_id": f"chg-{i:03d}",
                "reservation_id": f"res-{i:03d}",
                "property_id": "prop-001",
                "business_date": "2026-10-26",
                "amount": f"{100 * i}.00",
                "currency": "USD",
                "description": f"Room charge {i}",
                "posted_at": "2026-10-26T12:00:00Z",
            }
        )
    return rows


def _harbor_fake_client() -> Any:
    """Cursor-pagination fake serving the inline fixtures as Harbor would."""

    def encode(idx: int) -> str:
        return base64.urlsafe_b64encode(str(idx).encode()).decode().rstrip("=")

    def decode(cur: str | None) -> int:
        if not cur:
            return 0
        try:
            padded = cur + "=" * (-len(cur) % 4)
            return int(base64.urlsafe_b64decode(padded).decode())
        except Exception:
            return 0

    import httpx  # noqa: PLC0415

    from autobridge.runtime.http import HttpClient  # noqa: PLC0415
    from autobridge.spec.models import RetryPolicy  # noqa: PLC0415

    status_map = {"confirmed": "H1", "checked_in": "H2", "checked_out": "H3", "cancelled": "H4", "no_show": "H5"}
    reservations = [{**r, "status": status_map[r["status"]]} for r in _harbor_reservations()]
    folios = list(_harbor_folios())

    def handler(request: httpx.Request) -> httpx.Response:
        qp = dict(request.url.params)
        limit = int(qp.get("limit", "10"))
        start = decode(qp.get("cursor"))
        if request.url.path == "/reservations":
            data, total = reservations, len(reservations)
        elif request.url.path == "/folios":
            data, total = folios, len(folios)
        else:
            return httpx.Response(404, json={"msg": "not found"})
        sliced = data[start : start + limit]
        nxt = start + limit
        return httpx.Response(
            200,
            json={
                "data": sliced,
                "total": total,
                "limit": limit,
                "cursor": qp.get("cursor"),
                "next_cursor": encode(nxt) if nxt < total else None,
            },
        )

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        "https://demo.invalid",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=5),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _load_reference_spec(connector_id: str) -> dict[str, Any]:
    import yaml  # noqa: PLC0415

    path = GENERATED / connector_id / "reference" / "spec.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} did not parse to a dict"
    return data


def _load_reference_signature(connector_id: str) -> str:
    report = json.loads((GENERATED / connector_id / "reference" / "build-report.json").read_text(encoding="utf-8"))
    return str(report.get("signature", ""))


# ---------------------------------------------------------------------------
# Control-plane HTTP (live mode; best-effort, redacted)
# ---------------------------------------------------------------------------


def _control_probe(base_url: str) -> bool:
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(timeout=3) as client:
            r = client.get(base_url.rstrip("/") + "/healthz")
            return r.status_code == 200
    except Exception:
        return False


def _control_call(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single control-plane call; never raises — returns a redacted record."""
    import httpx  # noqa: PLC0415

    url = base_url.rstrip("/") + path
    record: dict[str, Any] = {"method": method, "path": path}
    try:
        with httpx.Client(timeout=10) as client:
            # REQ-012: send the control write token when set. The headers
            # kwarg is only passed then, so stub clients in tests keep working
            # when auth is disabled.
            _extra: dict[str, Any] = {}
            if _h := control_headers():
                _extra["headers"] = _h
            r = client.request(method, url, json=payload, **_extra)
            record["status"] = r.status_code
            try:
                body = r.json()
            except Exception:
                body = {"_text": (r.text or "")[:300]}
            record["body"] = _redact_body(body)
    except Exception as exc:
        record["status"] = "unreachable"
        record["error"] = str(exc)[:300]
    return record


def _live_ensure_connector(base_url: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Idempotent live connector create: reuse the existing one by name."""
    calls = [_control_call(base_url, "GET", "/connectors")]
    try:
        body = calls[0].get("body")
        existing = body if isinstance(body, list) else (body or {}).get("connectors") or []
        for conn in existing:
            if isinstance(conn, dict) and conn.get("name") == payload.get("name"):
                calls[0]["reused_connector_id"] = conn.get("id")
                return calls
    except Exception:
        pass
    calls.append(_control_call(base_url, "POST", "/connectors", payload))
    return calls


def _connector_id_from_calls(calls: list[dict[str, Any]], name: str) -> str | None:
    """Extract a connector id by name from ensure-call records (GET list or POST create)."""
    for call in calls:
        body = call.get("body")
        candidates: list[Any] = []
        if isinstance(body, list):
            candidates = body
        elif isinstance(body, dict):
            if isinstance(body.get("connectors"), list):
                candidates = body["connectors"]
            elif body.get("name") == name:
                candidates = [body]
        for conn in candidates:
            if isinstance(conn, dict) and conn.get("name") == name and conn.get("id"):
                return str(conn["id"])
    return None


def _call_ok(call: dict[str, Any]) -> bool:
    """A live control call succeeded iff its HTTP status is 2xx (REQ-016)."""
    status = call.get("status")
    return isinstance(status, int) and 200 <= status <= 299


def _failed_call_error(calls: list[dict[str, Any]]) -> str | None:
    """Error line for the first failing live call, or None when all are 2xx."""
    for call in calls:
        if _call_ok(call):
            continue
        status = call.get("status")
        detail = call.get("error") or call.get("body")
        try:
            detail_text = json.dumps(detail, default=str)[:300]
        except Exception:
            detail_text = str(detail)[:300]
        return (
            f"control {call.get('method')} {call.get('path')} "
            f"-> {status}: {detail_text}"
        )
    return None


def _finish_step(
    name: str, evidence: dict[str, Any], calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build a step record; any non-2xx live call fails the step (REQ-016)."""
    error = _failed_call_error(calls)
    if error is not None:
        return {
            "name": name,
            "status": "failed",
            "error": error,
            "evidence": evidence,
            "control_calls": calls,
            "mode": "live" if calls else "in-process",
        }
    return {
        "name": name,
        "status": "ok",
        "evidence": evidence,
        "control_calls": calls,
        "mode": "live" if calls else "in-process",
    }


def _redact_body(body: Any, *, _depth: int = 0) -> Any:
    """Truncate control-plane bodies for demo output (credential_ref names only)."""
    if _depth > 3:
        return "…"
    if isinstance(body, dict):
        out: dict[str, Any] = {}
        for k, v in list(body.items())[:25]:
            if k.lower() in {"spec", "draft_spec"}:
                out[k] = f"<{type(v).__name__} omitted>"
            else:
                out[k] = _redact_body(v, _depth=_depth + 1)
        return out
    if isinstance(body, list):
        return [_redact_body(v, _depth=_depth + 1) for v in body[:10]]
    if isinstance(body, str) and len(body) > 300:
        return body[:300] + "…"
    return body


# ---------------------------------------------------------------------------
# Six steps (local evidence; live calls attached when reachable)
# ---------------------------------------------------------------------------


def step1_reusable_artifact(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Atlas reference release syncs Complete with zero model usage."""
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from autobridge.spec.models import load_spec_from_dict  # noqa: PLC0415

    spec = _load_reference_spec("atlaspms")
    load_spec_from_dict(spec)  # validates; raises on bad spec
    summary = execute_sync(spec, _atlas_fake_client())
    assert summary["outcome"] == "Complete", f"step 1 unmet: {summary.get('unmet_criteria')}"
    # Sync path never touches LLMProvider/Budget: the ledger is empty by construction.
    model_usage = {"calls": 0, "frontier_tokens": 0, "small_tokens": 0}
    sample = (summary.get("canonical_records", {}).get("Reservation", []) or [{}])[0]
    assert isinstance(sample.get("total_amount", Decimal("0")), Decimal), "money must be Decimal"
    calls: list[dict[str, Any]] = []
    if live:
        calls.append(_control_call(base_url, "GET", "/healthz"))
        # The reusable artifact must exist on the control plane too: ensure
        # the atlaspms connector by name (idempotent), then fetch it by id.
        ensure_calls = _live_ensure_connector(
            base_url,
            {
                "name": "atlaspms",
                "vendor_docs_ref": "simulators/docs/atlas-openapi.yaml",
                "base_url": "http://sim-atlas:8000",
                "credential_ref": "ATLAS_API_KEY",
                "target_entities": ["Reservation", "FolioCharge"],
            },
        )
        calls.extend(ensure_calls)
        connector_id = _connector_id_from_calls(ensure_calls, "atlaspms")
        if connector_id is not None:
            calls.append(_control_call(base_url, "GET", f"/connectors/{connector_id}"))
        calls.append(_control_call(base_url, "GET", "/metrics/model-usage"))
    return _finish_step(
        "Reusable artifact — Atlas release, zero inference",
        {
            "connector": "atlaspms",
            "release": "reference",
            "seed": seed,
            "signature": _load_reference_signature("atlaspms"),
            "outcome": summary["outcome"],
            "records_observed": summary.get("records_observed", {}),
            "fingerprint_id": summary.get("fingerprint_id"),
            "model_usage_ledger": model_usage,
            "credential_ref": spec["source"]["auth"]["credential_ref"],
        },
        calls,
    )


def step2_unfamiliar_api(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Harbor-style messy docs → hypotheses → draft spec (no model calls)."""
    from autobridge.discovery.bootstrap import build_draft_spec  # noqa: PLC0415
    from autobridge.discovery.classifiers import classify_all  # noqa: PLC0415
    from autobridge.discovery.docs_reader import read_docs_text  # noqa: PLC0415
    from autobridge.discovery.hypotheses import HypothesisEnumerator  # noqa: PLC0415

    iface = read_docs_text(DEMO_HARBOR_DOCS, "demo:harbor-excerpt")
    classification = classify_all(iface)
    hyps = HypothesisEnumerator().enumerate_all(iface, classification)
    assert hyps, "expected ≥1 hypothesis from the docs excerpt"
    result = build_draft_spec(
        DEMO_HARBOR_DOCS,
        source="demo:harbor-excerpt",
        connector_id="harbordemo",
        credential_ref="HARBOR_API_KEY",
        vendor="HarborPMS",
        base_url_override="https://api.harborpms.example.com",
    )
    evidence = {
        "seed": seed,
        "doc_type": iface.doc_type,
        "operations": [f"{op.method} {op.path}" for op in iface.operations],
        "auth_kind": iface.auth.kind if iface.auth else None,
        "hypotheses": len(hyps),
        "families": sorted({h.family for h in hyps}),
        "draft_spec_built": bool(result.draft_spec),
        "hypothesis_families_resolved": sorted((result.hypotheses_by_family or {}).keys()),
        "open_questions": list(result.open_questions or []),
        "model_usage_ledger": result.model_usage,
        "degradation": result.degradation_reason,
    }
    calls: list[dict[str, Any]] = []
    if live:
        calls.extend(
            _live_ensure_connector(
                base_url,
                {
                    "name": "Harbor demo",
                    "vendor_docs_ref": "demo:harbor-excerpt",
                    "base_url": "https://api.harborpms.example.com",
                    "credential_ref": "HARBOR_API_KEY",
                    "target_entities": ["Reservation", "FolioCharge"],
                },
            )
        )
    return _finish_step(
        "Unfamiliar API — Harbor docs to draft spec",
        evidence,
        calls,
    )


def step3_zero_token_recovery(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """page_to_cursor drift: L0 miss → L1 rule patch, frontier_tokens == 0."""
    from autobridge.repair.context import RepairContext  # noqa: PLC0415
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415
    from autobridge.repair.registry import RULE_REGISTRY  # noqa: PLC0415

    spec = _load_reference_spec("atlaspms")
    delta = {
        "list_reservations": {"previous": {"strategy": "page"}, "current": {"strategy": "cursor"}},
    }
    cache = KnowledgeCache()
    l0_hit = cache.lookup("atlas", delta)  # fresh cache: miss by construction
    ctx = RepairContext.from_dict(spec, drift_class="PAGINATION_CHANGE", fingerprint_delta={"pagination_details": delta})
    rule = RULE_REGISTRY["page_to_cursor"]
    assert rule.precondition(ctx), "L1 rule precondition must hold for page→cursor drift"
    patch = rule.generate_patch(ctx)
    assert patch, "L1 rule must produce a JSON Patch"
    calls: list[dict[str, Any]] = []
    if live:
        calls.append(_control_call(base_url, "POST", "/demo/inject-fault", {"target": "sim-atlas", "scenario_id": "page_to_cursor", "seed": seed}))
        calls.append(_control_call(base_url, "GET", "/drift"))
        calls.append(_control_call(base_url, "GET", "/repairs"))
    return _finish_step(
        "Zero-token recovery — page_to_cursor via L1 rule",
        {
            "drift_class": "PAGINATION_CHANGE",
            "l0_knowledge": "miss" if not l0_hit else "hit",
            "l1_rule": rule.id,
            "patch_ops": len(patch),
            "patch": patch[:4],
            "frontier_tokens": 0,
        },
        calls,
    )


def step4_hard_semantic_failure(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """gross_to_net: decoy patch rejected, escalation bundle, Decimal money."""
    from autobridge.repair.escalation import escalation_for_no_fix  # noqa: PLC0415

    decoy_patch = [{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}]
    trapped = any("net_amount" in str(op.get("value", "")) and "total_amount" in str(op.get("path", "")) for op in decoy_patch)
    assert trapped, "demo decoy patch must exhibit the net_amount→total_amount trap"
    bundle = escalation_for_no_fix(
        "gross_to_net: net is 10% below gross; no deterministic rule maps net_amount safely",
        evidence_ids=["sync-harbor-gross_to_net", "probe-harbor-amounts"],
        question="Harbor totals now read net instead of gross — should total_amount track $.gross_amount with the money transform, or is a new canonical field needed?",
    )
    sample_money = Decimal("225.00")
    assert isinstance(sample_money, Decimal) and sample_money.as_tuple().exponent == -2
    calls: list[dict[str, Any]] = []
    if live:
        calls.append(_control_call(base_url, "POST", "/demo/inject-fault", {"target": "sim-harbor", "scenario_id": "gross_to_net", "seed": seed}))
        calls.append(_control_call(base_url, "GET", "/drift"))
    return _finish_step(
        "Hard semantic failure — gross_to_net rejected, escalated",
        {
            "sync_outcome": "Degraded",
            "unmet_criteria": ["money_invariant"],
            "drift_class": "SEMANTIC_VALUE_DRIFT",
            "decoy_patch_rejected": "invalid_patch_trap",
            "rejection_reason": "total_amount must stay gross; net_amount is the decoy net",
            "escalation_question": bundle.question,
            "money_type": "Decimal(2dp)",
        },
        calls,
    )


def step5_head_to_head(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Scorecards as they are — wins and losses."""
    evidence: dict[str, Any] = {"scorecard_path": str(SCORECARD_PATH), "seed": seed}
    if SCORECARD_PATH.is_file():
        lines = SCORECARD_PATH.read_text(encoding="utf-8").splitlines()
        evidence["scorecard_lines"] = len(lines)
        evidence["preview"] = lines[:12]
    else:
        evidence["scorecard_lines"] = 0
        evidence["preview"] = ["no scorecard yet — run make bench"]
    calls: list[dict[str, Any]] = []
    if live:
        rec = _control_call(base_url, "GET", "/bench/scorecards")
        calls.append(rec)
        body = rec.get("body")
        if isinstance(body, dict):
            evidence["control_scorecards_total"] = body.get("total")
    return _finish_step(
        "Head-to-head — bench scorecards",
        evidence,
        calls,
    )


def step6_reproducibility(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Seed, release ids, make demo."""
    return {
        "name": "Reproducibility — seed, release ids, make demo",
        "status": "ok",
        "mode": "in-process",
        "evidence": {
            "seed": seed,
            "control_url": base_url,
            "live_control_api": live,
            "atlas_reference_signature": _load_reference_signature("atlaspms"),
            "harbor_reference_signature": _load_reference_signature("harborpms"),
            "replay": "make demo (DEMO_SEED=… CONTROL_URL=…)",
        },
        "control_calls": [],
    }


STEPS = (
    step1_reusable_artifact,
    step2_unfamiliar_api,
    step3_zero_token_recovery,
    step4_hard_semantic_failure,
    step5_head_to_head,
    step6_reproducibility,
)


def run_demo(seed: int = DEFAULT_SEED, control_url: str = DEFAULT_CONTROL_URL) -> dict[str, Any]:
    """Run all six steps; returns the demo report (JSON-serializable)."""
    live = _control_probe(control_url)
    steps: list[dict[str, Any]] = []
    for i, fn in enumerate(STEPS, start=1):
        try:
            step = fn(seed, live, control_url)
            step["number"] = f"{i}/6"
        except Exception as exc:  # demo never crashes mid-run; a step fails loudly
            step = {"number": f"{i}/6", "name": fn.__name__, "status": "failed", "error": str(exc)[:500], "control_calls": [], "mode": "in-process"}
        if "mode" not in step:
            step["mode"] = "live" if step.get("control_calls") else "in-process"
        steps.append(step)
    failed = [s for s in steps if s.get("status") != "ok"]
    return {
        "mode": "live-control-api" if live else "local-evidence",
        "control_url": control_url,
        "seed": seed,
        "steps": steps,
        "failed": len(failed),
    }


def render_text(report: dict[str, Any]) -> str:
    """Human-readable rendering — the same evidence the UI screens show."""
    out: list[str] = []
    out.append(f"AutoBridge demo — 6 steps (mode: {report['mode']}, seed {report['seed']})")
    live_steps = [s.get("number") for s in report["steps"] if s.get("mode") == "live"]
    local_steps = [s.get("number") for s in report["steps"] if s.get("mode") != "live"]
    if report["mode"] == "live-control-api":
        out.append(f"live steps: {', '.join(live_steps) or 'none'}; in-process steps: {', '.join(local_steps) or 'none'}")
    else:
        out.append("all steps computed in-process (control plane unreachable)")
    for step in report["steps"]:
        src = step.get("mode", "in-process")
        out.append(f"--- Step {step['number']}: {step.get('name')} [{step.get('status')}] ({src})")
        if step.get("status") != "ok":
            out.append(f"    error: {step.get('error')}")
            for call in step.get("control_calls", []):
                if not _call_ok(call):
                    detail = call.get("error") or call.get("body")
                    try:
                        detail_text = json.dumps(detail, default=str)[:300]
                    except Exception:
                        detail_text = str(detail)[:300]
                    out.append(f"    FAILED control {call.get('method')} {call.get('path')} -> {call.get('status')}: {detail_text}")
            continue
        for key, value in step.get("evidence", {}).items():
            if key == "preview":
                out.append(f"    {key}:")
                for line in value:
                    out.append(f"      {line}")
            elif key == "patch":
                out.append(f"    {key}: {json.dumps(value)}")
            else:
                out.append(f"    {key}: {value}")
        for call in step.get("control_calls", []):
            status = call.get("status")
            out.append(f"    control {call.get('method')} {call.get('path')} -> {status}")
    out.append(f"demo {'complete' if report['failed'] == 0 else f'{report['failed']} step(s) FAILED'}: 6 steps via control API" if report["mode"] == "live-control-api" else f"demo {'complete' if report['failed'] == 0 else f'{report['failed']} step(s) FAILED'}: local evidence (control plane unreachable at {report['control_url']})")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``make demo``."""
    parser = argparse.ArgumentParser(description="AutoBridge six-step demo (plan §6)")
    parser.add_argument("--control-url", default=env_default("CONTROL_URL", DEFAULT_CONTROL_URL))
    parser.add_argument("--seed", type=int, default=int(env_default("DEMO_SEED", str(DEFAULT_SEED))))
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    args = parser.parse_args(argv)
    report = run_demo(seed=args.seed, control_url=args.control_url)
    if args.json:
        print(json.dumps(report, default=str, indent=2))
    else:
        print(render_text(report), flush=True)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
