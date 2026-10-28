"""Thin ``ab`` CLI wrapper over the control API (P2.S7.1, plan §5 S7).

Scope: the same journey the UI drives (``connectors create → build →
sync → repairs approve``) as CI-friendly subcommands over HTTP. The CLI
is intentionally thin: it owns no state machine, applies no patches and
imports no engine/verifier/simulator code — every decision lives in the
control plane. Only ``credential_ref`` env-key names travel; the CLI
never accepts, prints or logs a credential value (DIRECTIVES §6).

Exit codes: ``0`` success, ``1`` API/connection failure, ``2`` usage
error (argparse).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from typing import Any

import httpx

DEFAULT_CONTROL_URL = "http://localhost:8101"
CONTROL_URL_ENV = "AUTOBRIDGE_CONTROL_URL"

#: Env var carrying the control-plane write token (REQ-012). Sent as
#: ``Authorization: Bearer <token>`` when set; the CLI never prints it.
CONTROL_TOKEN_ENV_VAR = "AUTOBRIDGE_CONTROL_TOKEN"

TERMINAL_JOB_STATES = frozenset({"succeeded", "failed"})


def resolve_base_url(explicit: str | None) -> str:
    """Flag wins, then env, then the local default."""
    return (explicit or os.environ.get(CONTROL_URL_ENV) or DEFAULT_CONTROL_URL).rstrip("/")


def auth_headers() -> dict[str, str]:
    """Auth headers for control API calls (empty when no token is set)."""
    token = os.environ.get(CONTROL_TOKEN_ENV_VAR, "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _make_client(base_url: str, timeout: float) -> httpx.Client:
    """Client factory (module-level so tests can monkeypatch it)."""
    return httpx.Client(base_url=base_url, timeout=timeout, headers=auth_headers() or None)


def _fail(msg: str) -> int:
    print(f"ab: error: {msg}", file=sys.stderr)
    return 1


def _emit(payload: Any, output: str) -> None:
    if output == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        _emit_text(payload)


def _emit_text(payload: Any) -> None:
    if isinstance(payload, dict):
        for key in ("id", "job_id", "build_run_id", "sync_run_id", "state",
                    "outcome", "decision", "name", "status", "kind"):
            if key in payload and payload[key] is not None:
                print(f"{key}: {payload[key]}")
        if not any(k in payload for k in ("id", "job_id", "build_run_id",
                                          "sync_run_id", "state", "outcome",
                                          "decision", "name", "status", "kind")):
            print(json.dumps(payload, default=str))
    elif isinstance(payload, list):
        print(f"count: {len(payload)}")
        for item in payload:
            if isinstance(item, dict) and "id" in item:
                extra = ""
                for k in ("name", "state", "status", "outcome", "decision", "kind"):
                    if item.get(k) is not None:
                        extra += f" {k}={item[k]}"
                print(f"- {item['id']}{extra}")
            else:
                print(f"- {json.dumps(item, default=str)}")
    else:
        print(str(payload))


def _check(resp: httpx.Response, action: str) -> Any | None:
    """Return the JSON body, or print the failure and return None."""
    if resp.status_code in (200, 201):
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}
    detail = resp.text
    try:
        body = resp.json()
        if isinstance(body, dict) and "detail" in body:
            detail = str(body["detail"])
    except Exception:
        pass
    print(f"ab: error: {action} failed (HTTP {resp.status_code}): {detail}", file=sys.stderr)
    return None


def _wait_for_job(client: httpx.Client, job_id: str, timeout_s: float) -> Any | None:
    """Poll GET /jobs/{id} until the job reaches a terminal state."""
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while True:
        try:
            resp = client.get(f"/jobs/{job_id}")
        except httpx.HTTPError as exc:
            print(f"ab: error: polling job {job_id!r} failed: {exc}", file=sys.stderr)
            return None
        body = _check(resp, f"poll job {job_id}")
        if body is None:
            return None
        last = body
        state = body.get("state") if isinstance(body, dict) else None
        if state in TERMINAL_JOB_STATES:
            return last
        if time.monotonic() >= deadline:
            print(f"ab: error: timed out waiting for job {job_id!r} (state={state})", file=sys.stderr)
            return None
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Subcommand handlers (each returns a process exit code)
# ---------------------------------------------------------------------------


def cmd_connectors_create(client: httpx.Client, args: argparse.Namespace) -> int:
    targets = [t.strip() for t in (args.target_entities or "").split(",") if t.strip()]
    body: dict[str, Any] = {
        "name": args.name,
        "base_url": args.base_url,
        "credential_ref": args.credential_ref,
        "target_entities": targets,
    }
    if args.docs_ref:
        body["vendor_docs_ref"] = args.docs_ref
    try:
        resp = client.post("/connectors", json=body)
    except httpx.HTTPError as exc:
        return _fail(f"connectors create failed: {exc}")
    payload = _check(resp, "connectors create")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def cmd_connectors_list(client: httpx.Client, args: argparse.Namespace) -> int:
    try:
        resp = client.get("/connectors")
    except httpx.HTTPError as exc:
        return _fail(f"connectors list failed: {exc}")
    payload = _check(resp, "connectors list")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def cmd_connectors_get(client: httpx.Client, args: argparse.Namespace) -> int:
    try:
        resp = client.get(f"/connectors/{args.connector_id}")
    except httpx.HTTPError as exc:
        return _fail(f"connectors get failed: {exc}")
    payload = _check(resp, "connectors get")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def _build_body(args: argparse.Namespace) -> dict[str, Any] | None:
    sources = [bool(args.docs_text), bool(args.docs_file), bool(args.docs_url)]
    if sum(sources) > 1:
        print("ab: error: --docs-text, --docs-file and --docs-url are mutually exclusive",
              file=sys.stderr)
        return None
    body: dict[str, Any] = {"scenario_id": args.scenario}
    if args.vendor:
        body["vendor"] = args.vendor
    if args.credential_ref:
        body["credential_ref"] = args.credential_ref
    if args.base_url_override:
        body["base_url_override"] = args.base_url_override
    if args.docs_text:
        body["docs_text"] = args.docs_text
    elif args.docs_file:
        try:
            with open(args.docs_file, encoding="utf-8") as fh:
                body["docs_text"] = fh.read()
        except OSError as exc:
            print(f"ab: error: cannot read --docs-file: {exc}", file=sys.stderr)
            return None
    elif args.docs_url:
        body["docs_url"] = args.docs_url
    else:
        body["docs_path"] = "inline-ci-docs"
    return body


def cmd_build(client: httpx.Client, args: argparse.Namespace) -> int:
    body = _build_body(args)
    if body is None:
        return 2
    try:
        resp = client.post(f"/connectors/{args.connector_id}/build", json=body)
    except httpx.HTTPError as exc:
        return _fail(f"build failed: {exc}")
    payload = _check(resp, "build")
    if payload is None:
        return 1
    if args.wait and isinstance(payload, dict) and payload.get("job_id"):
        job = _wait_for_job(client, payload["job_id"], args.wait_timeout)
        if job is None:
            return 1
        payload = {**payload, "job": job}
    _emit(payload, args.output)
    return 0


def cmd_sync(client: httpx.Client, args: argparse.Namespace) -> int:
    body: dict[str, Any] = {}
    if args.release:
        body["release_id"] = args.release
    try:
        resp = client.post(f"/connectors/{args.connector_id}/sync", json=body)
    except httpx.HTTPError as exc:
        return _fail(f"sync failed: {exc}")
    payload = _check(resp, "sync")
    if payload is None:
        return 1
    if args.wait and isinstance(payload, dict) and payload.get("job_id"):
        job = _wait_for_job(client, payload["job_id"], args.wait_timeout)
        if job is None:
            return 1
        payload = {**payload, "job": job}
    _emit(payload, args.output)
    return 0


def cmd_runs_get(client: httpx.Client, args: argparse.Namespace) -> int:
    path = f"/runs/{args.run_id}" + ("/detail" if args.detail else "")
    try:
        resp = client.get(path)
    except httpx.HTTPError as exc:
        return _fail(f"runs get failed: {exc}")
    payload = _check(resp, "runs get")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def cmd_repairs_list(client: httpx.Client, args: argparse.Namespace) -> int:
    params: dict[str, str] = {}
    if args.connector:
        params["connector_id"] = args.connector
    try:
        resp = client.get("/repairs", params=params)
    except httpx.HTTPError as exc:
        return _fail(f"repairs list failed: {exc}")
    payload = _check(resp, "repairs list")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def cmd_repairs_get(client: httpx.Client, args: argparse.Namespace) -> int:
    try:
        resp = client.get(f"/repairs/{args.repair_id}")
    except httpx.HTTPError as exc:
        return _fail(f"repairs get failed: {exc}")
    payload = _check(resp, "repairs get")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def _repair_decision(client: httpx.Client, args: argparse.Namespace, verb: str) -> int:
    body: dict[str, Any] = {}
    if args.connector:
        body["connector_id"] = args.connector
    if args.from_release:
        body["from_release_id"] = args.from_release
    if args.to_release:
        body["to_release_id"] = args.to_release
    try:
        resp = client.post(f"/repairs/{args.repair_id}/{verb}", json=body)
    except httpx.HTTPError as exc:
        return _fail(f"repairs {verb} failed: {exc}")
    payload = _check(resp, f"repairs {verb}")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def cmd_repairs_approve(client: httpx.Client, args: argparse.Namespace) -> int:
    return _repair_decision(client, args, "approve")


def cmd_repairs_reject(client: httpx.Client, args: argparse.Namespace) -> int:
    return _repair_decision(client, args, "reject")


def cmd_drift_list(client: httpx.Client, args: argparse.Namespace) -> int:
    params: dict[str, str] = {}
    if args.connector:
        params["connector_id"] = args.connector
    if args.status:
        params["status"] = args.status
    try:
        resp = client.get("/drift", params=params)
    except httpx.HTTPError as exc:
        return _fail(f"drift list failed: {exc}")
    payload = _check(resp, "drift list")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def cmd_drift_get(client: httpx.Client, args: argparse.Namespace) -> int:
    try:
        resp = client.get(f"/drift/{args.drift_id}")
    except httpx.HTTPError as exc:
        return _fail(f"drift get failed: {exc}")
    payload = _check(resp, "drift get")
    if payload is None:
        return 1
    _emit(payload, args.output)
    return 0


def cmd_health(client: httpx.Client, args: argparse.Namespace) -> int:
    try:
        hz = client.get("/healthz")
        rz = client.get("/readyz")
    except httpx.HTTPError as exc:
        return _fail(f"health check failed: {exc}")
    payload = {"healthz": _check(hz, "healthz"), "readyz": _check(rz, "readyz")}
    if payload["healthz"] is None or payload["readyz"] is None:
        return 1
    _emit(payload, args.output)
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ab", description="Thin CI wrapper over the AutoBridge control API.")
    p.add_argument("--control-url", default=None,
                   help=f"Control plane base URL (default: ${CONTROL_URL_ENV} or {DEFAULT_CONTROL_URL})")
    p.add_argument("--output", choices=("text", "json"), default="text",
                   help="Output format: human text (default) or JSON for CI")
    p.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds (default: 30)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("connectors", help="Manage connectors")
    csub = c.add_subparsers(dest="connectors_cmd", required=True)
    cc = csub.add_parser("create", help="Create a connector (credential_ref is an env-key name, never a value)")
    cc.add_argument("--name", required=True)
    cc.add_argument("--base-url", required=True)
    cc.add_argument("--credential-ref", required=True)
    cc.add_argument("--target-entities", default="", help="Comma-separated canonical entities")
    cc.add_argument("--docs-ref", default=None, help="Vendor docs URL or file ref")
    cc.set_defaults(func=cmd_connectors_create)
    cl = csub.add_parser("list", help="List connectors")
    cl.set_defaults(func=cmd_connectors_list)
    cg = csub.add_parser("get", help="Get a connector with releases and recent runs")
    cg.add_argument("connector_id")
    cg.set_defaults(func=cmd_connectors_get)

    b = sub.add_parser("build", help="Build a connector spec from docs (POST /connectors/{id}/build)")
    b.add_argument("connector_id")
    docs = b.add_mutually_exclusive_group()
    docs.add_argument("--docs-text", default=None, help="Inline vendor docs")
    docs.add_argument("--docs-file", default=None, help="Path to a local docs file")
    docs.add_argument("--docs-url", default=None, help="URL to fetch vendor docs from")
    b.add_argument("--scenario", default="baseline")
    b.add_argument("--vendor", default=None)
    b.add_argument("--credential-ref", default=None, help="Env-key name (never a value)")
    b.add_argument("--base-url-override", default=None)
    b.add_argument("--wait", action="store_true", help="Poll the job until it succeeds or fails")
    b.add_argument("--wait-timeout", type=float, default=120.0)
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("sync", help="Run a sync (POST /connectors/{id}/sync)")
    s.add_argument("connector_id")
    s.add_argument("--release", default=None, help="Release id (default: active release)")
    s.add_argument("--wait", action="store_true", help="Poll the job until it succeeds or fails")
    s.add_argument("--wait-timeout", type=float, default=120.0)
    s.set_defaults(func=cmd_sync)

    r = sub.add_parser("runs", help="Inspect runs")
    rsub = r.add_subparsers(dest="runs_cmd", required=True)
    rg = rsub.add_parser("get", help="Get a build/sync/repair run (or job)")
    rg.add_argument("run_id")
    rg.add_argument("--detail", action="store_true", help="Full typed run detail")
    rg.set_defaults(func=cmd_runs_get)

    rp = sub.add_parser("repairs", help="List, inspect, approve or reject repairs")
    rpsub = rp.add_subparsers(dest="repairs_cmd", required=True)
    rpl = rpsub.add_parser("list", help="List repair runs")
    rpl.add_argument("--connector", default=None)
    rpl.set_defaults(func=cmd_repairs_list)
    rpg = rpsub.add_parser("get", help="Get a repair run")
    rpg.add_argument("repair_id")
    rpg.set_defaults(func=cmd_repairs_get)
    for verb, func in (("approve", cmd_repairs_approve), ("reject", cmd_repairs_reject)):
        v = rpsub.add_parser(verb, help=f"{verb.capitalize()} a repair (actor comes from the server)")
        v.add_argument("repair_id")
        v.add_argument("--connector", default=None)
        v.add_argument("--from-release", default=None)
        v.add_argument("--to-release", default=None)
        v.set_defaults(func=func)

    d = sub.add_parser("drift", help="List or inspect drift events")
    dsub = d.add_subparsers(dest="drift_cmd", required=True)
    dl = dsub.add_parser("list", help="List drift events")
    dl.add_argument("--connector", default=None)
    dl.add_argument("--status", default=None)
    dl.set_defaults(func=cmd_drift_list)
    dg = dsub.add_parser("get", help="Get a drift event")
    dg.add_argument("drift_id")
    dg.set_defaults(func=cmd_drift_get)

    h = sub.add_parser("health", help="Check /healthz and /readyz")
    h.set_defaults(func=cmd_health)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base_url = resolve_base_url(args.control_url)
    try:
        client = _make_client(base_url, args.timeout)
    except Exception as exc:
        return _fail(f"cannot create client for {base_url!r}: {exc}")
    try:
        return int(args.func(client, args))
    finally:
        with contextlib.suppress(Exception):
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
