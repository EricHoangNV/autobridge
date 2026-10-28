"""Docs reader (P1.W5.1, plan §2.6/§4 W5).

Parses OpenAPI 3.x (JSON/YAML) and Markdown narrative docs into a
normalized :class:`InterfaceDescription` with provenance (which doc line).

Provenance is file + line number so every claim can be traced back to
its doc line — required by the W5 passing criteria and used by later
classifiers/hypotheses to create evidence records.

No network, no LLM, no imports of verifier/simulators.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# provenance + normalized model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a fact came from: file + line (1-indexed) + snippet."""

    source: str
    line: int
    snippet: str = ""

    def __str__(self) -> str:
        return f"{self.source}:{self.line}"


def _line_for_pattern(lines: list[str], pattern: str, start: int = 0) -> int:
    """Find first line containing pattern (case-insensitive substring), 1-indexed."""
    low = pattern.lower()
    for i in range(start, len(lines)):
        if low in lines[i].lower():
            return i + 1
    return 1


def _snippet_for_line(lines: list[str], lineno: int) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()[:200]
    return ""


# ---------------------------------------------------------------------------
# Normalized description
# ---------------------------------------------------------------------------


@dataclass
class AuthDescription:
    kind: str  # bearer | api_key_header | basic | oauth2 | body_fields | none | unknown
    scheme: str = ""
    header_name: str | None = None
    description: str = ""
    provenance: Provenance | None = None
    # body_fields auth (spec v2): required JSON-body field names carrying
    # credentials (e.g. ClientToken, AccessToken). Empty for header auth.
    body_fields: tuple[str, ...] = ()


@dataclass
class ParamDescription:
    name: str
    location: str  # query | path | header
    schema_type: str = ""
    required: bool = False
    description: str = ""
    provenance: Provenance | None = None


@dataclass
class ResponseField:
    name: str
    type: str = ""
    description: str = ""


@dataclass
class BodyField:
    """One flattened JSON-body property of an operation (spec v2 POST APIs).

    ``required`` mirrors the request schema's required list; credential-like
    required fields (token/secret/password) drive ``body_fields`` auth.
    """

    name: str
    required: bool = False
    schema_type: str = ""
    description: str = ""
    provenance: Provenance | None = None


@dataclass
class LinkDescription:
    """Framed-transport contract parsed from docs (spec v3 ``link`` data).

    ``announced_records`` are the record codes the host's ``LR`` lines in
    the documented handshake announce interest in; ``listen_port`` is the
    port the docs say the integration host listens on.
    """

    ack_mode: bool = True
    max_frame_bytes: int = 65536
    announced_records: tuple[str, ...] = ()
    listen_port: int | None = None
    provenance: Provenance | None = None


@dataclass
class OperationDescription:
    operation_id: str
    method: str
    path: str
    summary: str = ""
    description: str = ""
    params: list[ParamDescription] = field(default_factory=list)
    response_fields: list[ResponseField] = field(default_factory=list)
    pagination_hint: str = ""  # offset | cursor | page | body_cursor | none
    provenance: Provenance | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # POST JSON-body surface (spec v2): flattened requestBody properties
    # (allOf-resolved) plus the names the request schema marks required.
    body_fields: list[BodyField] = field(default_factory=list)
    body_required: list[str] = field(default_factory=list)
    # Body-cursor paging signal (spec v2): dotted request paths for the
    # cursor and page size, the documented page-size maximum, and the
    # response fields holding the item array and the next cursor.
    body_cursor_path: str | None = None
    body_count_path: str | None = None
    body_count_max: int | None = None
    response_items_field: str | None = None
    response_cursor_field: str | None = None


@dataclass
class SchemaDescription:
    name: str
    fields: list[ResponseField] = field(default_factory=list)
    required: list[str] = field(default_factory=list)
    provenance: Provenance | None = None


@dataclass
class InterfaceDescription:
    """Normalized interface extracted from docs."""

    source: str
    doc_type: str  # openapi | markdown
    protocol: str = "http"  # http (polled vendor API) | fias_tcp (framed, event-driven)
    title: str = ""
    version: str = ""
    base_url: str = ""
    base_url_provenance: Provenance | None = None
    auth: AuthDescription | None = None
    operations: list[OperationDescription] = field(default_factory=list)
    schemas: list[SchemaDescription] = field(default_factory=list)
    link: LinkDescription | None = None
    # This installation's site facts (framed-protocol docs): the property
    # code (e.g. prop-001) and property currency (e.g. USD) the docs state
    # as site settings, each with provenance.
    property_code: str | None = None
    currency_code: str | None = None
    site_provenance: Provenance | None = None
    # Free-form notes with provenance (status vocab, date semantics, etc.)
    status_notes: list[tuple[str, Provenance]] = field(default_factory=list)
    date_notes: list[tuple[str, Provenance]] = field(default_factory=list)
    pagination_notes: list[tuple[str, Provenance]] = field(default_factory=list)
    # All provenance entries for audit
    provenances: list[Provenance] = field(default_factory=list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_OPENAPI_VERSION_RE = re.compile(r"^\s*3\.\d+")


def _resolve_response_properties(schema_ref: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """Resolve a response schema to a flat properties dict.

    Follows ``$ref`` into ``#/components/schemas/<Name>`` and merges
    ``allOf`` branches (envelope + payload pattern). Returns {} when
    nothing resolvable is found.
    """
    schemas = components.get("schemas", {}) if isinstance(components.get("schemas"), dict) else {}

    def _lookup(ref: str) -> dict[str, Any]:
        if not ref.startswith("#/components/schemas/"):
            return {}
        node = schemas.get(ref.rsplit("/", 1)[-1], {})
        return node if isinstance(node, dict) else {}

    def _collect(node: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        if depth > 3 or not isinstance(node, dict):
            return {}
        if "$ref" in node and isinstance(node["$ref"], str):
            return _collect(_lookup(node["$ref"]), depth + 1)
        merged: dict[str, Any] = {}
        all_of = node.get("allOf")
        if isinstance(all_of, list):
            for branch in all_of:
                if isinstance(branch, dict):
                    merged.update(_collect(branch, depth + 1))
        props = node.get("properties")
        if isinstance(props, dict):
            merged.update(props)
        return merged

    return _collect(schema_ref)


def _nested_leaf_fields(
    fdef: Any, components: dict[str, Any]
) -> list[tuple[str, str, str]]:
    """One level of dotted sub-fields for an object-typed property.

    Resolves inline ``properties`` or a ``$ref`` into components (e.g.
    ``UnitAmount: {$ref: Money}`` yields ``UnitAmount.GrossValue``,
    ``UnitAmount.Currency``). Returns [(sub-name, type, description)].
    Arrays and scalars yield []. One level only: deeper nesting stays
    unexpressed, like the rest of the v2 vocabulary.
    """
    if not isinstance(fdef, dict):
        return []
    target = fdef
    ref = fdef.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        schemas = components.get("schemas", {}) if isinstance(components.get("schemas"), dict) else {}
        node = schemas.get(ref.rsplit("/", 1)[-1], {})
        target = node if isinstance(node, dict) else {}
    if target.get("type", "object") != "object":
        return []
    props = target.get("properties")
    if not isinstance(props, dict):
        return []
    out: list[tuple[str, str, str]] = []
    for sub, sdef in props.items():
        if not isinstance(sub, str) or not sub:
            continue
        stype = str(sdef.get("type", "")) if isinstance(sdef, dict) else ""
        sdesc = str(sdef.get("description", "")) if isinstance(sdef, dict) else ""
        out.append((sub, stype, sdesc))
    return out


def _merge_schema_props(
    sdef: dict[str, Any], components: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Flatten a schema definition to (properties, required names).

    Follows ``$ref`` into ``#/components/schemas/<Name>`` and merges
    ``allOf`` branches (base + extension pattern, e.g. shared auth fields
    plus per-operation filters). Returns ({}, []) when nothing resolvable
    is found. Depth-capped so self-referential schemas terminate.
    """
    schemas = components.get("schemas", {}) if isinstance(components.get("schemas"), dict) else {}

    def _lookup(ref: str) -> dict[str, Any]:
        if not ref.startswith("#/components/schemas/"):
            return {}
        node = schemas.get(ref.rsplit("/", 1)[-1], {})
        return node if isinstance(node, dict) else {}

    def _collect(node: dict[str, Any], depth: int = 0) -> tuple[dict[str, Any], list[str]]:
        if depth > 3 or not isinstance(node, dict):
            return {}, []
        if "$ref" in node and isinstance(node["$ref"], str):
            return _collect(_lookup(node["$ref"]), depth + 1)
        merged: dict[str, Any] = {}
        required: list[str] = []
        for branch in node.get("allOf", []) if isinstance(node.get("allOf"), list) else []:
            if isinstance(branch, dict):
                b_props, b_req = _collect(branch, depth + 1)
                merged.update(b_props)
                for r in b_req:
                    if r not in required:
                        required.append(r)
        inline = node.get("properties", {})
        if isinstance(inline, dict):
            merged.update(inline)
        for r in node.get("required", []) if isinstance(node.get("required"), list) else []:
            if isinstance(r, str) and r not in required:
                required.append(r)
        return merged, required

    return _collect(sdef)


# ---------------------------------------------------------------------------
# POST JSON-body signals (spec v2: body_fields auth, body_cursor pagination)
# ---------------------------------------------------------------------------

#: Required body field names carrying credentials (generic, vendor-neutral).
_CRED_BODY_RE = re.compile(r"token|secret|password|apikey|api_key", re.IGNORECASE)

#: Leaf names (lowercased) identifying the paging cursor / page size inside
#: a JSON-body paging envelope (e.g. Limitation: {Count, Cursor}).
_CURSOR_LEAF_NAMES = frozenset({"cursor", "nextcursor", "next_cursor", "pagetoken", "page_token"})
_COUNT_LEAF_NAMES = frozenset({"count", "limit", "pagesize", "page_size", "maxresults", "max_results"})

#: Response property names (lowercased) holding the next-page cursor.
_RESPONSE_CURSOR_NAMES = frozenset({"cursor", "next_cursor"})


def is_credential_body_field(name: str) -> bool:
    """True when a body field name generically signals a credential."""
    return bool(_CRED_BODY_RE.search(name or ""))


def _body_paging_envelope(
    body_props: dict[str, Any], components: dict[str, Any]
) -> tuple[str | None, str | None, int | None]:
    """Find (cursor_path, count_path, count_max) for a JSON-body page envelope.

    Looks for an object-typed body property (or flat cursor/count fields)
    whose leaves name both a cursor and a page size. Returns dotted request
    paths using the documented spellings, e.g. ``("Limitation.Cursor",
    "Limitation.Count", 1000)``, or ``(None, None, None)`` when the request
    body carries no paging signal.
    """
    # Envelope objects first (e.g. Limitation: {Count, Cursor}).
    for pname, pdef in body_props.items():
        sub_props, _ = _merge_schema_props(pdef if isinstance(pdef, dict) else {}, components)
        if not sub_props:
            continue
        by_lower = {str(k).lower(): str(k) for k in sub_props}
        cursor_leaf = next((by_lower[k] for k in _CURSOR_LEAF_NAMES if k in by_lower), None)
        count_leaf = next((by_lower[k] for k in _COUNT_LEAF_NAMES if k in by_lower), None)
        if cursor_leaf is not None and count_leaf is not None:
            count_max: int | None = None
            cdef = sub_props.get(count_leaf)
            if isinstance(cdef, dict):
                maximum = cdef.get("maximum")
                if isinstance(maximum, int) and not isinstance(maximum, bool) and maximum >= 1:
                    count_max = maximum
            return f"{pname}.{cursor_leaf}", f"{pname}.{count_leaf}", count_max
    # Flat fallback: cursor/count as direct body fields.
    by_lower = {str(k).lower(): str(k) for k in body_props}
    cursor_flat = next((by_lower[k] for k in _CURSOR_LEAF_NAMES if k in by_lower), None)
    count_flat = next((by_lower[k] for k in _COUNT_LEAF_NAMES if k in by_lower), None)
    if cursor_flat is not None and count_flat is not None:
        return cursor_flat, count_flat, None
    return None, None, None


def _response_array_and_cursor(
    response_props: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Find (items_field, cursor_field) in a resolved response schema.

    The cursor is the property whose name matches the known cursor names;
    the items field is the first array-typed property that is not the
    cursor (e.g. Reservations alongside Cursor).
    """
    cursor_field: str | None = None
    for name in response_props:
        if str(name).lower() in _RESPONSE_CURSOR_NAMES:
            cursor_field = str(name)
            break
    items_field: str | None = None
    for name, fdef in response_props.items():
        if cursor_field is not None and str(name) == cursor_field:
            continue
        if isinstance(fdef, dict) and fdef.get("type") == "array":
            items_field = str(name)
            break
    return items_field, cursor_field


def _is_openapi_dict(data: dict[str, Any]) -> bool:
    v = data.get("openapi", "")
    return isinstance(v, str) and bool(_OPENAPI_VERSION_RE.match(v))


def _auth_from_security_schemes(
    schemes: dict[str, Any], lines: list[str], source: str
) -> AuthDescription | None:
    for name, defn in schemes.items():
        if not isinstance(defn, dict):
            continue
        typ = defn.get("type", "")
        scheme = defn.get("scheme", "")
        line = _line_for_pattern(lines, name)
        prov = Provenance(source=source, line=line, snippet=_snippet_for_line(lines, line))
        if typ == "http" and scheme == "bearer":
            return AuthDescription(kind="bearer", scheme="bearer", description=str(defn.get("description", "")), provenance=prov)
        if typ == "apiKey":
            return AuthDescription(
                kind="api_key_header",
                header_name=defn.get("name"),
                description=str(defn.get("description", "")),
                provenance=prov,
            )
        if typ == "oauth2":
            return AuthDescription(kind="oauth2", description=str(defn.get("description", "")), provenance=prov)
        if typ == "http" and scheme == "basic":
            return AuthDescription(kind="basic", scheme="basic", provenance=prov)
    return None


def _pagination_hint_for_operation(
    op: dict[str, Any],
    *,
    body_cursor: bool = False,
    response_cursor: bool = False,
) -> str:
    # A JSON-body paging envelope plus a response cursor is the body_cursor
    # contract (spec v2 POST APIs) — stronger than any query-param signal.
    # The envelope only exists when a requestBody was parsed, so no method
    # check is needed here.
    if body_cursor and response_cursor:
        return "body_cursor"
    param_names = {p.get("name", "").lower() for p in op.get("parameters", []) if isinstance(p, dict)}
    desc = (op.get("description", "") + " " + op.get("summary", "")).lower()
    resp_text = json.dumps(op.get("responses", {})).lower()
    combined = " ".join(param_names) + " " + desc + " " + resp_text
    # cursor signals stronger than offset when both present
    if "cursor" in combined or "next_cursor" in combined:
        return "cursor"
    if "offset" in combined or "next_offset" in combined:
        return "offset"
    if "page" in combined:
        return "page"
    return "none"


# ---------------------------------------------------------------------------
# OpenAPI reader
# ---------------------------------------------------------------------------


def parse_openapi_dict(
    data: dict[str, Any], source: str, lines: list[str] | None = None
) -> InterfaceDescription:
    """Parse an already-loaded OpenAPI dict into InterfaceDescription."""
    if not isinstance(data, dict):
        raise ValueError(f"{source}: OpenAPI spec must be a mapping")
    if not _is_openapi_dict(data):
        raise ValueError(f"{source}: missing or unsupported openapi version (expected 3.x)")

    raw_lines = lines if lines is not None else json.dumps(data, indent=2).splitlines()
    iface = InterfaceDescription(source=source, doc_type="openapi")

    info = data.get("info", {}) if isinstance(data.get("info"), dict) else {}
    iface.title = str(info.get("title", ""))
    iface.version = str(info.get("version", ""))

    # base_url from servers[0].url
    servers = data.get("servers", [])
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url", ""))
        iface.base_url = url
        lnum = _line_for_pattern(raw_lines, url) if url else 1
        iface.base_url_provenance = Provenance(source=source, line=lnum, snippet=_snippet_for_line(raw_lines, lnum))
        iface.provenances.append(iface.base_url_provenance)

    # auth
    components = data.get("components", {}) if isinstance(data.get("components"), dict) else {}
    schemes = components.get("securitySchemes", {}) if isinstance(components.get("securitySchemes"), dict) else {}
    if schemes:
        auth = _auth_from_security_schemes(schemes, raw_lines, source)
        if auth is not None:
            iface.auth = auth
            iface.provenances.append(auth.provenance)  # type: ignore[arg-type]
    else:
        # check security requirement mentioning bearerAuth etc.
        sec = data.get("security", [])
        if sec:
            lnum = _line_for_pattern(raw_lines, "security")
            prov = Provenance(source=source, line=lnum, snippet=_snippet_for_line(raw_lines, lnum))
            iface.auth = AuthDescription(kind="unknown", description=str(sec), provenance=prov)
            iface.provenances.append(prov)

    # operations
    paths = data.get("paths", {}) if isinstance(data.get("paths"), dict) else {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.startswith("x-") or not isinstance(op, dict):
                continue
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head"):
                continue
            op_id = str(op.get("operationId", f"{method}_{path}"))
            lnum = _line_for_pattern(raw_lines, path)
            prov = Provenance(source=source, line=lnum, snippet=_snippet_for_line(raw_lines, lnum))

            params: list[ParamDescription] = []
            for p in op.get("parameters", []) if isinstance(op.get("parameters"), list) else []:
                if not isinstance(p, dict):
                    continue
                pname = str(p.get("name", ""))
                ploc = str(p.get("in", "query"))
                schema = p.get("schema", {}) if isinstance(p.get("schema"), dict) else {}
                stype = str(schema.get("type", ""))
                req = bool(p.get("required", False))
                desc = str(p.get("description", ""))
                pline = _line_for_pattern(raw_lines, pname, lnum - 1)
                pprov = Provenance(source=source, line=pline, snippet=_snippet_for_line(raw_lines, pline))
                params.append(ParamDescription(name=pname, location=ploc, schema_type=stype, required=req, description=desc, provenance=pprov))

            # requestBody (POST JSON-body APIs, spec v2): flatten the schema
            # (allOf-resolved) into body fields with required flags.
            body_fields: list[BodyField] = []
            body_required: list[str] = []
            body_props: dict[str, Any] = {}
            req_body = op.get("requestBody", {}) if isinstance(op.get("requestBody"), dict) else {}
            rb_content = req_body.get("content", {}) if isinstance(req_body.get("content"), dict) else {}
            rb_json = rb_content.get("application/json", {}) if isinstance(rb_content.get("application/json"), dict) else {}
            rb_schema = rb_json.get("schema", {}) if isinstance(rb_json.get("schema"), dict) else {}
            if rb_schema:
                b_props, b_req = _merge_schema_props(rb_schema, components)
                body_props = b_props
                body_required = [r for r in b_req if isinstance(r, str)]
                for bname, bdef in b_props.items():
                    btype = str(bdef.get("type", "")) if isinstance(bdef, dict) else ""
                    bdesc = str(bdef.get("description", "")) if isinstance(bdef, dict) else ""
                    bline = _line_for_pattern(raw_lines, str(bname), lnum - 1)
                    bprov = Provenance(source=source, line=bline, snippet=_snippet_for_line(raw_lines, bline))
                    body_fields.append(
                        BodyField(
                            name=str(bname),
                            required=str(bname) in body_required,
                            schema_type=btype,
                            description=bdesc,
                            provenance=bprov,
                        )
                    )

            # response fields from 200 schema (resolve $ref / allOf envelopes)
            resp_fields: list[ResponseField] = []
            responses = op.get("responses", {}) if isinstance(op.get("responses"), dict) else {}
            ok200 = responses.get("200", {}) if isinstance(responses.get("200"), dict) else {}
            content = ok200.get("content", {}) if isinstance(ok200.get("content"), dict) else {}
            app_json = content.get("application/json", {}) if isinstance(content.get("application/json"), dict) else {}
            schema_ref = app_json.get("schema", {}) if isinstance(app_json.get("schema"), dict) else {}
            # inline properties
            props = schema_ref.get("properties", {}) if isinstance(schema_ref.get("properties"), dict) else {}
            if not props:
                props = _resolve_response_properties(schema_ref, components)
            for fname, fdef in props.items():
                ftype = str(fdef.get("type", "")) if isinstance(fdef, dict) else ""
                fdesc = str(fdef.get("description", "")) if isinstance(fdef, dict) else ""
                resp_fields.append(ResponseField(name=fname, type=ftype, description=fdesc))
                for sub, stype, sdesc in _nested_leaf_fields(fdef, components):
                    resp_fields.append(
                        ResponseField(name=f"{fname}.{sub}", type=stype, description=sdesc)
                    )

            # body-cursor paging signal + response items/cursor fields (v2)
            cursor_path, count_path, count_max = _body_paging_envelope(body_props, components)
            items_field, cursor_field = _response_array_and_cursor(props)
            hint = _pagination_hint_for_operation(
                op,
                body_cursor=cursor_path is not None,
                response_cursor=cursor_field is not None,
            )
            if hint != "none":
                note_prov = Provenance(source=source, line=lnum, snippet=_snippet_for_line(raw_lines, lnum))
                iface.pagination_notes.append((f"{path} {method}: {hint} pagination", note_prov))

            # status vocabulary from enum in schemas
            for _fname, fdef in props.items():
                if isinstance(fdef, dict) and "enum" in fdef:
                    enum_vals = fdef.get("enum", [])
                    if isinstance(enum_vals, list) and enum_vals:
                        note_prov = Provenance(source=source, line=lnum, snippet=_snippet_for_line(raw_lines, lnum))
                        iface.status_notes.append((f"{path} status enum: {enum_vals}", note_prov))

            op_desc = OperationDescription(
                operation_id=op_id,
                method=method.upper(),
                path=str(path),
                summary=str(op.get("summary", "")),
                description=str(op.get("description", "")),
                params=params,
                response_fields=resp_fields,
                pagination_hint=hint,
                provenance=prov,
                raw=op,
                body_fields=body_fields,
                body_required=body_required,
                body_cursor_path=cursor_path,
                body_count_path=count_path,
                body_count_max=count_max,
                response_items_field=items_field,
                response_cursor_field=cursor_field,
            )
            iface.operations.append(op_desc)
            iface.provenances.append(prov)

        # Body-carried credentials (spec v2): when no header auth scheme is
        # declared, required body fields with credential-like names mean the
        # API authenticates in the JSON body, never in a header.
        if iface.auth is None:
            cred_by_op: list[tuple[str, Provenance | None]] = []
            for op_desc in iface.operations:
                for bname in op_desc.body_required:
                    if is_credential_body_field(bname):
                        cred_by_op.append((bname, op_desc.provenance))
            if cred_by_op:
                seen: list[str] = []
                for bname, _ in cred_by_op:
                    if bname not in seen:
                        seen.append(bname)
                first_prov = next((p for _, p in cred_by_op if p is not None), None)
                if first_prov is None:
                    first_prov = Provenance(source=source, line=1, snippet="body_fields auth from requestBody")
                iface.auth = AuthDescription(
                    kind="body_fields",
                    description=f"credentials travel in the JSON body ({', '.join(seen)})",
                    provenance=first_prov,
                    body_fields=tuple(seen),
                )
                iface.provenances.append(first_prov)

    # schemas
    schemas = components.get("schemas", {}) if isinstance(components.get("schemas"), dict) else {}
    for sname, sdef in schemas.items():
        if not isinstance(sdef, dict):
            continue
        sline = _line_for_pattern(raw_lines, sname)
        sprov = Provenance(source=source, line=sline, snippet=_snippet_for_line(raw_lines, sline))
        sfields: list[ResponseField] = []
        # allOf-resolved so base+extension request schemas (e.g. shared auth
        # fields plus per-operation filters) expose every property.
        props, resolved_required = _merge_schema_props(sdef, components)
        required = [str(r) for r in resolved_required]
        for fname, fdef in props.items():
            ftype = str(fdef.get("type", "")) if isinstance(fdef, dict) else ""
            fdesc = str(fdef.get("description", "")) if isinstance(fdef, dict) else ""
            if isinstance(fdef, dict) and "enum" in fdef:
                fdesc = (fdesc + f" enum={fdef['enum']}").strip()
            sfields.append(ResponseField(name=fname, type=ftype, description=fdesc))
            for sub, stype, sdesc in _nested_leaf_fields(fdef, components):
                sfields.append(
                    ResponseField(name=f"{fname}.{sub}", type=stype, description=sdesc)
                )
            # date/status notes per field
            low = (fname + " " + fdesc).lower()
            if any(k in low for k in ("date", "arrival", "departure", "business_date", "posted_at", "updated_at")):
                iface.date_notes.append((f"schema {sname}.{fname}: {fdesc or ftype}", sprov))
            if "status" in low or "enum" in low:
                iface.status_notes.append((f"schema {sname}.{fname}: {fdesc or ftype}", sprov))
        iface.schemas.append(SchemaDescription(name=sname, fields=sfields, required=required, provenance=sprov))
        iface.provenances.append(sprov)

    # also collect pagination/date/status notes from top-level description
    desc_text = str(info.get("description", "")) + " " + str(data.get("description", ""))
    low_desc = desc_text.lower()
    if "pagination" in low_desc or "cursor" in low_desc or "offset" in low_desc:
        lnum = _line_for_pattern(raw_lines, "pagination")
        prov = Provenance(source=source, line=lnum, snippet=_snippet_for_line(raw_lines, lnum))
        iface.pagination_notes.append((desc_text.strip()[:300], prov))

    return iface


def parse_openapi_text(text: str, source: str) -> InterfaceDescription:
    """Parse OpenAPI JSON or YAML text."""
    lines = text.splitlines()
    # Try JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return parse_openapi_dict(data, source, lines)
    except (json.JSONDecodeError, ValueError):
        pass
    # YAML
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{source}: invalid YAML/JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{source}: OpenAPI spec must be a mapping")
    return parse_openapi_dict(data, source, lines)


# ---------------------------------------------------------------------------
# Markdown reader
# ---------------------------------------------------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_MD_CODE_FENCE_RE = re.compile(r"^```")
_MD_ENDPOINT_RE = re.compile(r"`GET\s+([^`]+)`", re.IGNORECASE)
_MD_PARAM_RE = re.compile(r"^\s*[-*]\s+`?(\w+)`?\s*[—\-:]*\s*(.*)", re.IGNORECASE)


def parse_markdown(text: str, source: str) -> InterfaceDescription:
    """Parse Harbor-style narrative Markdown into InterfaceDescription."""
    lines = text.splitlines()
    iface = InterfaceDescription(source=source, doc_type="markdown")

    # Title = first heading
    for i, line in enumerate(lines):
        m = _MD_HEADING_RE.match(line)
        if m:
            iface.title = m.group(2).strip()
            iface.provenances.append(Provenance(source=source, line=i + 1, snippet=line.strip()[:200]))
            break

    # base_url: look for Base URL / localhost
    for i, line in enumerate(lines):
        if "base url" in line.lower() or "localhost" in line.lower():
            m = re.search(r"https?://[^\s`\)]+", line)
            if m:
                iface.base_url = m.group(0).rstrip("`")
                prov = Provenance(source=source, line=i + 1, snippet=line.strip()[:200])
                iface.base_url_provenance = prov
                iface.provenances.append(prov)
                break

    # auth: prefer non-heading content lines first, then fallback to headings
    def _auth_kind_for_line(low: str) -> str | None:
        # Only consider lines that actually discuss authentication mechanism
        # (not every occurrence of "auth" substring)
        if any(k in low for k in ("authenticat", "authorization", "bearer", "api key", "api-key", "x-api", "permissive")):
            if "permissive" in low or ("no" in low and "required" in low):
                return "none"
            if "bearer" in low:
                return "bearer"
            if "api key" in low or "api-key" in low or "x-api" in low:
                return "api_key_header"
            return "unknown"
        return None

    found_auth = False
    for i, line in enumerate(lines):
        if _MD_HEADING_RE.match(line):
            continue
        auth_kind = _auth_kind_for_line(line.lower())
        if auth_kind is not None:
            prov = Provenance(source=source, line=i + 1, snippet=line.strip()[:200])
            iface.auth = AuthDescription(kind=auth_kind, description=line.strip()[:300], provenance=prov)
            iface.provenances.append(prov)
            found_auth = True
            break
    if not found_auth:
        for i, line in enumerate(lines):
            auth_kind = _auth_kind_for_line(line.lower())
            if auth_kind is not None:
                prov = Provenance(source=source, line=i + 1, snippet=line.strip()[:200])
                iface.auth = AuthDescription(kind=auth_kind, description=line.strip()[:300], provenance=prov)
                iface.provenances.append(prov)
                break

    # operations: scan for GET endpoints
    seen_paths: set[str] = set()
    for i, line in enumerate(lines):
        for m in _MD_ENDPOINT_RE.finditer(line):
            path = m.group(1).strip()
            # strip query string / fragment artifacts
            path = path.split("?")[0].split("#")[0].split("`")[0].strip()
            # filter to API paths
            if not path.startswith("/"):
                continue
            if path in seen_paths:
                continue
            seen_paths.add(path)
            prov = Provenance(source=source, line=i + 1, snippet=line.strip()[:200])
            # collect params from following lines until next heading/endpoint
            params: list[ParamDescription] = []
            # detect if this section has a query-parameters block
            window_end = min(i + 80, len(lines))
            has_param_block = any("param" in lines[k].lower() for k in range(i + 1, window_end))
            if has_param_block:
                for j in range(i + 1, window_end):
                    nxt = lines[j]
                    if _MD_HEADING_RE.match(nxt):
                        if "get" in nxt.lower() and "/" in nxt:
                            break
                        continue
                    if _MD_ENDPOINT_RE.search(nxt) and j != i:
                        break
                    # stop at next major section that is not param list (e.g. status codes heading)
                    if _MD_HEADING_RE.match(nxt) and "param" not in nxt.lower():
                        # but we already continue for non-GET headings; keep scanning until GET
                        pass
                    pm = _MD_PARAM_RE.match(nxt)
                    if pm:
                        pname = pm.group(1)
                        # skip status code items H1..H5 which are not query params
                        if re.fullmatch(r"H[1-5]", pname):
                            continue
                        pdesc = pm.group(2).strip()
                        loc = "query"
                        if "{" in path and pname in path:
                            loc = "path"
                        pprov = Provenance(source=source, line=j + 1, snippet=nxt.strip()[:200])
                        params.append(ParamDescription(name=pname, location=loc, description=pdesc, provenance=pprov))
            # fallback: inline Query parameters like "`property_id`, `reservation_id`"
            if not params:
                for j in range(i + 1, window_end):
                    nxt = lines[j]
                    if _MD_HEADING_RE.match(nxt) and "get" in nxt.lower() and "/" in nxt:
                        break
                    if "query param" in nxt.lower():
                        # extract all backtick names
                        for pname in re.findall(r"`(\w+)`", nxt):
                            if re.fullmatch(r"H[1-5]", pname):
                                continue
                            loc = "query"
                            if "{" in path and pname in path:
                                loc = "path"
                            pprov = Provenance(source=source, line=j + 1, snippet=nxt.strip()[:200])
                            params.append(ParamDescription(name=pname, location=loc, description="", provenance=pprov))
                        break
            # stop collecting after we passed the param block and hit a heading without param
            # (no-op, window already limited)
            # pagination hint: params first, then bounded ctx until next op heading
            param_names_lower = {p.name.lower() for p in params}
            # ctx only until next operation heading (exclusive) to avoid cross-contamination
            ctx_end = window_end
            for k in range(i + 1, window_end):
                if _MD_HEADING_RE.match(lines[k]) and "get" in lines[k].lower() and "/" in lines[k]:
                    ctx_end = k
                    break
                if _MD_ENDPOINT_RE.search(lines[k]) and k != i:
                    ctx_end = k
                    break
            ctx = "\n".join(lines[max(0, i - 1): ctx_end]).lower()
            hint = "none"
            if "cursor" in param_names_lower or "next_cursor" in param_names_lower:
                hint = "cursor"
            elif "offset" in param_names_lower or "next_offset" in param_names_lower:
                hint = "offset"
            elif "page" in param_names_lower:
                hint = "page"
            elif "pagination" in ctx or "cursor" in ctx or "offset" in ctx:
                if "cursor" in ctx or "next_cursor" in ctx:
                    hint = "cursor"
                elif "offset" in ctx or "next_offset" in ctx:
                    hint = "offset"
            if hint != "none":
                iface.pagination_notes.append((f"{path}: {hint} pagination", prov))

            op = OperationDescription(
                operation_id=path.strip("/").replace("/", "_").replace("{", "").replace("}", "") or "root",
                method="GET",
                path=path,
                summary=line.strip()[:300],
                params=params,
                pagination_hint=hint,
                provenance=prov,
                raw={"markdown_line": line},
            )
            iface.operations.append(op)
            iface.provenances.append(prov)

    # pagination notes: any paragraph mentioning pagination
    for i, line in enumerate(lines):
        low = line.lower()
        if "pagination" in low:
            prov = Provenance(source=source, line=i + 1, snippet=line.strip()[:200])
            iface.pagination_notes.append((line.strip()[:300], prov))

    # status notes
    for i, line in enumerate(lines):
        low = line.lower()
        if "status" in low and ("h1" in low or "h2" in low or "enum" in low or "vocabulary" in low or "opaque" in low):
            prov = Provenance(source=source, line=i + 1, snippet=line.strip()[:200])
            iface.status_notes.append((line.strip()[:300], prov))
        # also capture H1..H5 definitions like "- `H1` —"
        if re.search(r"`H[1-5]`", line):
            prov = Provenance(source=source, line=i + 1, snippet=line.strip()[:200])
            iface.status_notes.append((line.strip()[:300], prov))

    # date notes
    for i, line in enumerate(lines):
        low = line.lower()
        if any(k in low for k in ("business_date", "arrival_date", "business date", "property-local", "cutoff", 'field named `date`', "ambiguous")):
            prov = Provenance(source=source, line=i + 1, snippet=line.strip()[:200])
            iface.date_notes.append((line.strip()[:300], prov))

    # schemas: try to extract JSON examples inside code fences
    in_fence = False
    fence_start = 0
    fence_lang = ""
    for i, line in enumerate(lines):
        if _MD_CODE_FENCE_RE.match(line):
            if not in_fence:
                in_fence = True
                fence_start = i + 1
                fence_lang = line.strip()[3:].strip().lower()
            else:
                in_fence = False
                block = "\n".join(lines[fence_start:i])
                if "json" in fence_lang and "{" in block:
                    try:
                        obj = json.loads(block)
                        # unwrap {data: [...]} or {data: {}}
                        sample = None
                        if isinstance(obj, dict) and "data" in obj:
                            d = obj["data"]
                            if isinstance(d, list) and d and isinstance(d[0], dict):
                                sample = d[0]
                            elif isinstance(d, dict):
                                sample = d
                        elif isinstance(obj, dict):
                            sample = obj
                        if isinstance(sample, dict):
                            sname = f"example_{fence_start}"
                            prov = Provenance(source=source, line=fence_start, snippet=lines[fence_start - 1].strip()[:200] if fence_start <= len(lines) else "")
                            sfields = [ResponseField(name=k, type=type(v).__name__, description="") for k, v in sample.items()]
                            iface.schemas.append(SchemaDescription(name=sname, fields=sfields, provenance=prov))
                            # also infer status/date notes from sample fields
                            for _k in sample:
                                lk = _k.lower()
                                if lk == "status":
                                    iface.status_notes.append((f"example field status: {sample[_k]}", prov))
                                if "date" in lk:
                                    iface.date_notes.append((f"example field {_k}: {sample[_k]}", prov))
                    except (json.JSONDecodeError, ValueError):
                        pass

    # Framed, record-oriented protocol docs (FIAS): record tables, link
    # contract and site facts become EVENT operations + link data.
    _parse_framed_protocol(lines, iface)

    return iface


# ---------------------------------------------------------------------------
# Framed, record-oriented protocols (FIAS — spec v3 event feeds)
# ---------------------------------------------------------------------------

_FIAS_MARKER_RE = re.compile(r"\bFIAS\b", re.IGNORECASE)
_FIAS_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s*(.*)")
_FIAS_RECORD_HEADING_RE = re.compile(r"^###\s+(.*)")
_FIAS_CODE_RE = re.compile(r"`([A-Z]{2})`")
_FIAS_LR_RE = re.compile(r"LR\|RI([A-Z]{2})")
_FIAS_ACK_RE = re.compile(r"ACK.?mode.?is (on|off)", re.IGNORECASE)
_FIAS_MAXFRAME_RE = re.compile(r"[Mm]aximum frame[^0-9]*(\d+)\s*KB")
_FIAS_PORT_RE = re.compile(r"listens.{0,8}?on port `?(\d{1,5})`?")
_FIAS_CURRENCY_RE = re.compile(r"property currency \((\w{3})", re.IGNORECASE)
_FIAS_PROPERTY_LINE_RE = re.compile(r"property|hotel|installation|site setting", re.IGNORECASE)
_FIAS_PROPCODE_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_-]{0,30}\d)`?")
_FIAS_ANSWERED_RE = re.compile(r"answered with (?:an? )?`([A-Z]{2})`")


def _is_framed_protocol_doc(text: str) -> bool:
    """Detect a framed, record-oriented interface doc (FIAS family).

    Signal: the doc names FIAS and shows framed bytes (STX … ETX). This is
    the record-protocol analog of OpenAPI auto-detection above — it keys on
    the framing contract both this PABX and any FIAS-speaking peer document,
    not on any vendor name.
    """
    return bool(_FIAS_MARKER_RE.search(text)) and "STX" in text


def _parse_md_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or stripped.count("|") < 2:
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    # Separator rows are all dashes/colons.
    if cells and all(re.fullmatch(r":?-{1,}:?", c or "---") for c in cells):
        return None
    return cells


def _parse_framed_protocol(lines: list[str], iface: InterfaceDescription) -> None:
    """Parse FIAS-family docs into EVENT operations + link/site data.

    Sets ``iface.protocol = "fias_tcp"``; PABX→host business records become
    EVENT operations (feed candidates), host→PABX business records become
    command candidates, answers/resync/link records are marked as such and
    skipped by the compiler. Framing facts (ACK mode, max frame, the host's
    ``LR`` announcements, listen port) become ``iface.link``; the stated
    property code and currency become site facts. Every value carries the
    doc line it came from.
    """
    text = "\n".join(lines)
    if not _is_framed_protocol_doc(text):
        return
    source = iface.source
    iface.protocol = "fias_tcp"

    def _prov(lineno: int) -> Provenance:
        snippet = lines[lineno - 1].strip()[:200] if 1 <= lineno <= len(lines) else ""
        return Provenance(source=source, line=lineno, snippet=snippet)

    # Link contract + site facts (first signal wins, each with provenance).
    # Property codes live in front matter ("at **Grand Plaza** (`prop-001`)")
    # or beside a property/hotel/installation mention — check both, in order.
    ack_mode = True
    max_frame_bytes = 65536
    listen_port: int | None = None
    link_prov: Provenance | None = None

    def _propcode_from(line: str, lineno: int) -> bool:
        for cand in _FIAS_PROPCODE_RE.findall(line):
            if any(ch.isdigit() for ch in cand) and any(ch.isalpha() for ch in cand):
                if iface.property_code is None:
                    iface.property_code = cand
                    iface.site_provenance = _prov(lineno)
                return True
        return False

    for i, line in enumerate(lines[:8]):
        _propcode_from(line, i + 1)
    for i, line in enumerate(lines):
        plain = line.replace("*", "").replace("`", "")
        if link_prov is None:
            m = _FIAS_ACK_RE.search(plain)
            if m:
                ack_mode = m.group(1).lower() == "on"
                link_prov = _prov(i + 1)
        m = _FIAS_MAXFRAME_RE.search(line)
        if m:
            max_frame_bytes = int(m.group(1)) * 1024
            link_prov = link_prov or _prov(i + 1)
        m = _FIAS_PORT_RE.search(line)
        if m and listen_port is None:
            listen_port = int(m.group(1))
            link_prov = link_prov or _prov(i + 1)
        if _FIAS_PROPERTY_LINE_RE.search(line):
            _propcode_from(line, i + 1)
        m = _FIAS_CURRENCY_RE.search(line)
        if m and iface.currency_code is None:
            iface.currency_code = m.group(1).upper()
            iface.site_provenance = iface.site_provenance or _prov(i + 1)

    announced: list[str] = []
    for m in _FIAS_LR_RE.finditer(text):
        if m.group(1) not in announced:
            announced.append(m.group(1))
    iface.link = LinkDescription(
        ack_mode=ack_mode,
        max_frame_bytes=max_frame_bytes,
        announced_records=tuple(announced),
        listen_port=listen_port,
        provenance=link_prov,
    )

    # Record sections: track the ## N direction, parse ### `XX` tables.
    section: int | None = None
    direction: str | None = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        sm = _FIAS_SECTION_RE.match(line)
        if sm:
            section = int(sm.group(1))
            rest = sm.group(2).lower()
            if "from the pabx" in rest or "(to the host)" in rest:
                direction = "pabx_to_host"
            elif "from the host" in rest or "(to the pabx)" in rest:
                direction = "host_to_pabx"
            else:
                direction = None
            i += 1
            continue
        hm = _FIAS_RECORD_HEADING_RE.match(line)
        if hm and section in (3, 4) and direction:
            codes = [c for c in _FIAS_CODE_RE.findall(hm.group(1)) if len(c) == 2]
            codes = list(dict.fromkeys(codes))
            name = hm.group(1).strip()[:200]
            if codes:
                low_name = name.lower()
                if "answer" in low_name:
                    kind = "answer"
                elif "resync" in low_name:
                    kind = "control"
                elif section == 3:
                    kind = "feed"
                else:
                    kind = "command"
                fields_per_code: dict[str, dict[str, str]] = {c: {} for c in codes}
                formats_per_code: dict[str, dict[str, str]] = {c: {} for c in codes}
                answers: dict[str, str] = {}
                prose: list[str] = []
                j = i + 1
                header: list[str] | None = None
                record_cols: list[str] = []
                while j < n and not lines[j].startswith("#"):
                    cells = _parse_md_table_row(lines[j])
                    if cells is None:
                        if lines[j].strip():
                            prose.append(lines[j].strip())
                        j += 1
                        continue
                    if header is None:
                        if len(cells) >= 2 and cells[0].lower().lstrip("`").rstrip("`") == "field" and "meaning" in cells[1].lower():
                            header = cells
                            # Multi-record tables (host commands): each extra
                            # column names the record codes it applies to.
                            for hcell in cells[2:]:
                                found = _FIAS_CODE_RE.findall(hcell)
                                record_cols.append(found[0] if found else "")
                        j += 1
                        continue
                    if len(cells) >= 2:
                        fid_cell = cells[0].strip().strip("`")
                        meaning = cells[1].strip()[:300]
                        # Combined ids ("DA, TI") name one field each.
                        fids = [
                            f.strip().strip("`").strip()
                            for f in re.split(r"\s*,\s*", fid_cell)
                        ]
                        fmt = cells[2].strip()[:200] if len(header or []) == 3 and len(cells) >= 3 else ""
                        # A combined field row ("DA, TI") usually pairs with a
                        # combined format ("YYMMDD, HHMMSS"): align parts.
                        fmt_parts = [p.strip().strip("`").strip() for p in re.split(r"\s*,\s*", fmt)] if fmt else []
                        if len(codes) == 1 or not record_cols:
                            # Shared table: the field set holds for every
                            # record in the heading (e.g. WR and WC).
                            for idx, fid in enumerate(fids):
                                if len(fid) == 2 and meaning:
                                    for code in codes:
                                        fields_per_code[code][fid] = meaning
                                        if idx < len(fmt_parts) and fmt_parts[idx]:
                                            formats_per_code[code][fid] = fmt_parts[idx]
                        else:
                            for col_code, cell in zip(record_cols, cells[2:], strict=False):
                                if col_code in fields_per_code and cell.strip() not in ("", "-", "—", "–"):
                                    for fid in fids:
                                        if len(fid) == 2 and meaning:
                                            fields_per_code[col_code][fid] = meaning
                    j += 1
                # Answer correlation ("always answered with `KA`"): scan the
                # section prose sentence by sentence. A code named before
                # "answered with `XX`" in the same sentence takes XX as its
                # expected answer — but only for unconditional answers:
                # "always", or a self-answer (a `KZ` read answered by a
                # `KZ`). Conditional answers (a past-dated `WR` answered
                # with `WA`) stay `ack`; the scenario tracks them.
                for sentence in re.split(r"\.\s+", " ".join(prose)):
                    am = _FIAS_ANSWERED_RE.search(sentence)
                    if not am:
                        continue
                    if "always" not in sentence.lower() and am.group(1) not in codes:
                        continue
                    before = sentence[: am.start()]
                    for c in _FIAS_CODE_RE.findall(before):
                        if c in fields_per_code:
                            answers[c] = am.group(1)
                for code in codes:
                    prov = _prov(i + 1)
                    response_fields = [
                        ResponseField(name=fid, description=meaning)
                        for fid, meaning in fields_per_code[code].items()
                    ]
                    raw: dict[str, Any] = {
                        "record": code,
                        "direction": direction,
                        "kind": kind,
                        "fields": dict(fields_per_code[code]),
                        "formats": dict(formats_per_code[code]),
                    }
                    if code in answers:
                        raw["answer"] = answers[code]
                    iface.operations.append(
                        OperationDescription(
                            operation_id=f"fias_{code.lower()}",
                            method="EVENT",
                            path="",
                            summary=f"{code} — {name}",
                            response_fields=response_fields,
                            pagination_hint="none",
                            provenance=prov,
                            raw=raw,
                        )
                    )
                    iface.provenances.append(prov)
                i = j
                continue
        i += 1

    # Wire posting instant for the date classifier: without a date-like
    # field the generic fallback would invent an ambiguous `date` note, so
    # name the derived instant explicitly (non-blocking either way).
    if any(op.method == "EVENT" for op in iface.operations):
        line_no = next(
            (idx + 1 for idx, ln in enumerate(lines) if "property-local" in ln.lower()),
            1,
        )
        iface.date_notes.append(
            (
                "wire `posted_at`: property-local `DA` YYMMDD plus `TI` HHMMSS; "
                "UTC instant derived in the source timezone",
                _prov(line_no),
            )
        )


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def read_docs_text(text: str, source: str) -> InterfaceDescription:
    """Auto-detect doc type and parse text."""
    stripped = text.lstrip()
    # JSON openapi detection
    if stripped.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and _is_openapi_dict(data):
                return parse_openapi_text(text, source)
        except json.JSONDecodeError:
            pass
        # still try generic openapi parse (may raise)
        try:
            return parse_openapi_text(text, source)
        except ValueError:
            pass
    # YAML openapi detection: starts with openapi:
    if re.match(r"openapi\s*:", stripped, re.IGNORECASE):
        return parse_openapi_text(text, source)
    # YAML openapi via safe_load check
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict) and _is_openapi_dict(data):
            return parse_openapi_text(text, source)
    except yaml.YAMLError:
        pass
    return parse_markdown(text, source)


def read_docs_file(path: str | Path) -> InterfaceDescription:
    """Read a docs file from disk (JSON/YAML/Markdown auto-detected)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return read_docs_text(text, source=str(p))
