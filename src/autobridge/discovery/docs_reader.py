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
    kind: str  # bearer | api_key_header | basic | oauth2 | none | unknown
    scheme: str = ""
    header_name: str | None = None
    description: str = ""
    provenance: Provenance | None = None


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
class OperationDescription:
    operation_id: str
    method: str
    path: str
    summary: str = ""
    description: str = ""
    params: list[ParamDescription] = field(default_factory=list)
    response_fields: list[ResponseField] = field(default_factory=list)
    pagination_hint: str = ""  # offset | cursor | page | none
    provenance: Provenance | None = None
    raw: dict[str, Any] = field(default_factory=dict)


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
    title: str = ""
    version: str = ""
    base_url: str = ""
    base_url_provenance: Provenance | None = None
    auth: AuthDescription | None = None
    operations: list[OperationDescription] = field(default_factory=list)
    schemas: list[SchemaDescription] = field(default_factory=list)
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


def _pagination_hint_for_operation(op: dict[str, Any]) -> str:
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

            # response fields from 200 schema
            resp_fields: list[ResponseField] = []
            responses = op.get("responses", {}) if isinstance(op.get("responses"), dict) else {}
            ok200 = responses.get("200", {}) if isinstance(responses.get("200"), dict) else {}
            content = ok200.get("content", {}) if isinstance(ok200.get("content"), dict) else {}
            app_json = content.get("application/json", {}) if isinstance(content.get("application/json"), dict) else {}
            schema_ref = app_json.get("schema", {}) if isinstance(app_json.get("schema"), dict) else {}
            # inline properties
            props = schema_ref.get("properties", {}) if isinstance(schema_ref.get("properties"), dict) else {}
            for fname, fdef in props.items():
                ftype = str(fdef.get("type", "")) if isinstance(fdef, dict) else ""
                fdesc = str(fdef.get("description", "")) if isinstance(fdef, dict) else ""
                resp_fields.append(ResponseField(name=fname, type=ftype, description=fdesc))

            hint = _pagination_hint_for_operation(op)
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
            )
            iface.operations.append(op_desc)
            iface.provenances.append(prov)

    # schemas
    schemas = components.get("schemas", {}) if isinstance(components.get("schemas"), dict) else {}
    for sname, sdef in schemas.items():
        if not isinstance(sdef, dict):
            continue
        sline = _line_for_pattern(raw_lines, sname)
        sprov = Provenance(source=source, line=sline, snippet=_snippet_for_line(raw_lines, sline))
        sfields: list[ResponseField] = []
        required = sdef.get("required", []) if isinstance(sdef.get("required"), list) else []
        props = sdef.get("properties", {}) if isinstance(sdef.get("properties"), dict) else {}
        for fname, fdef in props.items():
            ftype = str(fdef.get("type", "")) if isinstance(fdef, dict) else ""
            fdesc = str(fdef.get("description", "")) if isinstance(fdef, dict) else ""
            if isinstance(fdef, dict) and "enum" in fdef:
                fdesc = (fdesc + f" enum={fdef['enum']}").strip()
            sfields.append(ResponseField(name=fname, type=ftype, description=fdesc))
            # date/status notes per field
            low = (fname + " " + fdesc).lower()
            if any(k in low for k in ("date", "arrival", "departure", "business_date", "posted_at", "updated_at")):
                iface.date_notes.append((f"schema {sname}.{fname}: {fdesc or ftype}", sprov))
            if "status" in low or "enum" in low:
                iface.status_notes.append((f"schema {sname}.{fname}: {fdesc or ftype}", sprov))
        iface.schemas.append(SchemaDescription(name=sname, fields=sfields, required=[str(r) for r in required], provenance=sprov))
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

    return iface


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
