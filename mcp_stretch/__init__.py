"""P2.S4.1 MCP Guardian — mock HotelTools V1/V2 + compatibility gateway.

Trust zone: engine (plan §2.1). This package is standalone: it imports only
the stdlib plus FastAPI/pydantic, never ``verifier/`` or ``simulators/``,
and no Phase-1 engine code imports it, so the stretch item cannot
destabilize Phase 1 (plan §5 isolation rule).

Contents:
  catalog.py  — declarative V1/V2 tool catalogs, snapshot + diff (data, not code)
  gateway.py  — pinned V1→V2 mapping table + Gateway.invoke with
                Complete | Degraded | Blocked outcomes (money is Decimal,
                dates are property-local business dates)
  replay.py   — pinned-consumer workflow replay + plain-rediscovery comparison
  app.py      — FastAPI mock server (/healthz, /readyz, /v1/*, /v2/*,
                /gateway/*, /replay/*)
"""
