"""Tests for discovery/docs_reader.py (P1.W5.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from autobridge.discovery.docs_reader import (
    Provenance,
    parse_markdown,
    parse_openapi_dict,
    parse_openapi_text,
    read_docs_file,
    read_docs_text,
)

# ---------------------------------------------------------------------------
# OpenAPI fixtures
# ---------------------------------------------------------------------------

MINIMAL_OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "Test API", "version": "1.0.0", "description": "Test with pagination"},
    "servers": [{"url": "http://localhost:8105"}],
    "paths": {
        "/reservations": {
            "get": {
                "operationId": "listReservations",
                "summary": "List reservations",
                "description": "Offset pagination with limit/offset",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                    {"name": "offset", "in": "query", "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {"type": "array"},
                                        "total": {"type": "integer"},
                                        "next_offset": {"type": "integer"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/properties": {
            "get": {
                "operationId": "listProperties",
                "summary": "List properties",
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
    "components": {
        "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer", "description": "Bearer token"}
        },
        "schemas": {
            "Reservation": {
                "type": "object",
                "required": ["reservation_id", "arrival_date"],
                "properties": {
                    "reservation_id": {"type": "string"},
                    "arrival_date": {"type": "string", "format": "date", "description": "property-local date"},
                    "status": {"type": "string", "enum": ["confirmed", "cancelled"], "description": "status enum"},
                    "posted_at": {"type": "string", "format": "date-time"},
                },
            }
        },
    },
}

BEARERLESS_OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "NoAuth", "version": "1.0.0"},
    "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}},
}

SECURITY_ARRAY_OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "SecArray", "version": "1.0.0"},
    "security": [{"bearerAuth": []}],
    "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}},
}

APIKEY_OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "Key", "version": "1.0.0"},
    "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}},
    "components": {
        "securitySchemes": {"apiKey": {"type": "apiKey", "name": "X-API-Key", "in": "header"}}
    },
}

OAUTH_OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "OAuth", "version": "1.0.0"},
    "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}},
    "components": {
        "securitySchemes": {"oauth": {"type": "oauth2", "flows": {}}}
    },
}

BASIC_OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "Basic", "version": "1.0.0"},
    "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}},
    "components": {
        "securitySchemes": {"basic": {"type": "http", "scheme": "basic"}}
    },
}


# ---------------------------------------------------------------------------
# OpenAPI tests
# ---------------------------------------------------------------------------


def test_parse_minimal_openapi() -> None:
    iface = parse_openapi_dict(MINIMAL_OPENAPI, source="test.json")
    assert iface.doc_type == "openapi"
    assert iface.base_url == "http://localhost:8105"
    assert iface.base_url_provenance is not None
    assert iface.base_url_provenance.line >= 1
    assert iface.title == "Test API"
    assert len(iface.operations) == 2
    # pagination
    res = next(o for o in iface.operations if o.path == "/reservations")
    assert res.pagination_hint == "offset"
    assert any(p.name == "limit" for p in res.params)
    assert any(p.name == "offset" for p in res.params)
    # auth
    assert iface.auth is not None
    assert iface.auth.kind == "bearer"
    assert iface.auth.provenance is not None
    # schemas
    assert len(iface.schemas) == 1
    assert iface.schemas[0].name == "Reservation"
    assert iface.schemas[0].required == ["reservation_id", "arrival_date"]
    # every operation has provenance
    for op in iface.operations:
        assert op.provenance is not None
        assert op.provenance.line >= 1
        assert op.provenance.source == "test.json"
    # status/date notes
    assert any("status" in n[0].lower() for n in iface.status_notes)
    assert any("date" in n[0].lower() for n in iface.date_notes)
    assert any("pagination" in n[0].lower() for n in iface.pagination_notes)


def test_openapi_no_auth() -> None:
    iface = parse_openapi_dict(BEARERLESS_OPENAPI, source="noauth.json")
    assert iface.auth is None


def test_openapi_security_array() -> None:
    iface = parse_openapi_dict(SECURITY_ARRAY_OPENAPI, source="sec.json")
    assert iface.auth is not None
    assert iface.auth.kind == "unknown"


def test_openapi_apikey() -> None:
    iface = parse_openapi_dict(APIKEY_OPENAPI, source="k.json")
    assert iface.auth is not None
    assert iface.auth.kind == "api_key_header"
    assert iface.auth.header_name == "X-API-Key"


def test_openapi_oauth() -> None:
    iface = parse_openapi_dict(OAUTH_OPENAPI, source="o.json")
    assert iface.auth is not None
    assert iface.auth.kind == "oauth2"


def test_openapi_basic() -> None:
    iface = parse_openapi_dict(BASIC_OPENAPI, source="b.json")
    assert iface.auth is not None
    assert iface.auth.kind == "basic"


def test_openapi_cursor_pagination() -> None:
    data = {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1.0"},
        "paths": {
            "/folios": {
                "get": {
                    "parameters": [{"name": "cursor", "in": "query", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"properties": {"next_cursor": {"type": "string"}}}}}}},
                }
            }
        },
    }
    iface = parse_openapi_dict(data, source="c.json")
    assert iface.operations[0].pagination_hint == "cursor"


def test_openapi_invalid_version() -> None:
    with pytest.raises(ValueError, match="openapi"):
        parse_openapi_dict({"openapi": "2.0", "info": {}, "paths": {}}, source="bad.json")


def test_openapi_not_mapping() -> None:
    with pytest.raises(ValueError, match="mapping"):
        parse_openapi_dict("not a dict", source="bad.json")  # type: ignore[arg-type]


def test_openapi_empty_paths() -> None:
    data = {"openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": {}}
    iface = parse_openapi_dict(data, source="e.json")
    assert iface.operations == []


def test_openapi_x_extension_skipped() -> None:
    data = {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1"},
        "paths": {"/x": {"x-custom": {"foo": 1}, "get": {"responses": {"200": {"description": "ok"}}}}},
    }
    iface = parse_openapi_dict(data, source="x.json")
    assert len(iface.operations) == 1


def test_openapi_paths_non_dict_skipped() -> None:
    data = {"openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": {"/x": "bad"}}
    iface = parse_openapi_dict(data, source="b.json")
    assert iface.operations == []


def test_openapi_provenance_snippet() -> None:
    iface = parse_openapi_dict(MINIMAL_OPENAPI, source="prov.json")
    for prov in iface.provenances:
        assert prov.source == "prov.json"
        assert prov.line >= 1


def test_parse_openapi_text_json() -> None:
    text = json.dumps(MINIMAL_OPENAPI)
    iface = parse_openapi_text(text, source="j.json")
    assert iface.doc_type == "openapi"
    assert iface.title == "Test API"


def test_parse_openapi_text_yaml() -> None:
    text = yaml.safe_dump(MINIMAL_OPENAPI)
    iface = parse_openapi_text(text, source="y.yaml")
    assert iface.doc_type == "openapi"


def test_parse_openapi_text_invalid_yaml() -> None:
    with pytest.raises(ValueError, match="invalid"):
        parse_openapi_text("openapi: [unclosed", source="bad.yaml")


def test_parse_openapi_text_not_mapping() -> None:
    with pytest.raises(ValueError, match="mapping"):
        parse_openapi_text("- item\n- item2", source="list.yaml")


# ---------------------------------------------------------------------------
# Markdown tests
# ---------------------------------------------------------------------------

HARBOR_SNIPPET = """# HarborPMS API

Base URL: http://localhost:8106

## Authentication

HarborPMS is permissive in development — no `Authorization` header is required.

## Endpoints

### `GET /properties`

List properties. No pagination.

### `GET /reservations`

List reservations. Cursor pagination: pass `limit` and `cursor`.

**Query parameters**

- `property_id` — filter
- `limit` — max items
- `cursor` — opaque token

### `GET /folios`

List folios. Cursor pagination.

**Query parameters**: `property_id`, `reservation_id`, `limit`, `cursor`

## Status codes — opaque

- `H1` — confirmed
- `H2` — checked_in

## Dates — business dates

`arrival_date` is property-local, cutoff 04:00 local. Ambiguous field named `date` duplicates it.

## Pagination — cursor

HarborPMS uses cursor pagination with known issue.

```json
{
  "data": [
    {"reservation_id": "RES-1", "status": "confirmed", "arrival_date": "2026-10-05"}
  ],
  "total": 10,
  "limit": 10,
  "cursor": null,
  "next_cursor": "abc"
}
```
"""


def test_parse_markdown_basic() -> None:
    iface = parse_markdown(HARBOR_SNIPPET, source="harbor.md")
    assert iface.doc_type == "markdown"
    assert iface.title == "HarborPMS API"
    assert iface.base_url == "http://localhost:8106"
    assert iface.base_url_provenance is not None
    assert iface.auth is not None
    assert iface.auth.kind == "none"
    # operations
    assert len(iface.operations) >= 3
    paths = {o.path for o in iface.operations}
    assert "/properties" in paths
    assert "/reservations" in paths
    assert "/folios" in paths
    # params for reservations
    res = next(o for o in iface.operations if o.path == "/reservations")
    assert any(p.name == "limit" for p in res.params)
    assert any(p.name == "cursor" for p in res.params)
    assert res.pagination_hint == "cursor"
    # properties has no pagination
    props = next(o for o in iface.operations if o.path == "/properties")
    assert props.pagination_hint == "none"
    # provenance on every operation
    for op in iface.operations:
        assert op.provenance is not None
        assert op.provenance.line >= 1
        assert op.provenance.snippet != ""
    # notes
    assert len(iface.status_notes) > 0
    assert len(iface.date_notes) > 0
    assert len(iface.pagination_notes) > 0
    # every provenance has source
    for prov in iface.provenances:
        assert prov.source == "harbor.md"


def test_markdown_properties_no_params() -> None:
    iface = parse_markdown(HARBOR_SNIPPET, source="h.md")
    props = next(o for o in iface.operations if o.path == "/properties")
    assert props.params == []


def test_markdown_folios_inline_params() -> None:
    iface = parse_markdown(HARBOR_SNIPPET, source="h.md")
    folios = next(o for o in iface.operations if o.path == "/folios")
    assert any(p.name == "property_id" for p in folios.params)


def test_markdown_schemas_from_json() -> None:
    iface = parse_markdown(HARBOR_SNIPPET, source="h.md")
    assert len(iface.schemas) >= 1
    assert any("reservation_id" in [f.name for f in s.fields] for s in iface.schemas)


def test_markdown_status_H1() -> None:
    iface = parse_markdown(HARBOR_SNIPPET, source="h.md")
    assert any("H1" in n[0] for n in iface.status_notes)


def test_markdown_query_string_stripped() -> None:
    text = "# T\nBase URL http://localhost:8000\n### `GET /folios?reservation_id=123`\n"
    iface = parse_markdown(text, source="q.md")
    assert len(iface.operations) == 1
    assert iface.operations[0].path == "/folios"


def test_markdown_deduplicates_paths() -> None:
    text = "# T\n### `GET /reservations`\n### `GET /reservations`\n"
    iface = parse_markdown(text, source="d.md")
    assert len(iface.operations) == 1


def test_markdown_non_slash_path_ignored() -> None:
    text = "# T\n### `GET not_a_path`\n"
    iface = parse_markdown(text, source="n.md")
    assert len(iface.operations) == 0


def test_markdown_bearer_auth() -> None:
    text = "# T\nThis API uses Bearer token auth.\n"
    iface = parse_markdown(text, source="b.md")
    assert iface.auth is not None
    assert iface.auth.kind == "bearer"


def test_markdown_apikey_auth() -> None:
    text = "# T\nUse X-API-Key header for api key auth.\n"
    iface = parse_markdown(text, source="k.md")
    assert iface.auth is not None
    assert iface.auth.kind == "api_key_header"


def test_markdown_no_auth() -> None:
    text = "# T\nNo auth here, just docs.\n"
    iface = parse_markdown(text, source="na.md")
    assert iface.auth is None


def test_markdown_invalid_json_block_ignored() -> None:
    text = "# T\n```json\n{ invalid json\n```\n"
    iface = parse_markdown(text, source="ij.md")
    # should not crash
    assert iface.doc_type == "markdown"


# ---------------------------------------------------------------------------
# Unified reader
# ---------------------------------------------------------------------------


def test_read_docs_text_openapi_json() -> None:
    text = json.dumps(MINIMAL_OPENAPI)
    iface = read_docs_text(text, source="j.json")
    assert iface.doc_type == "openapi"


def test_read_docs_text_openapi_yaml() -> None:
    text = "openapi: 3.1.0\ninfo:\n  title: Y\n  version: 1.0\npaths: {}\n"
    iface = read_docs_text(text, source="y.yaml")
    assert iface.doc_type == "openapi"


def test_read_docs_text_yaml_via_safeload() -> None:
    # yaml that is openapi but not starting with openapi: stripped edge (leading whitespace)
    text = yaml.safe_dump(MINIMAL_OPENAPI)
    iface = read_docs_text("   \n" + text, source="s.yaml")
    assert iface.doc_type == "openapi"


def test_read_docs_text_markdown() -> None:
    iface = read_docs_text(HARBOR_SNIPPET, source="h.md")
    assert iface.doc_type == "markdown"


def test_read_docs_text_invalid_json_falls_to_markdown() -> None:
    # invalid json object that is not openapi should still be tried as openapi then fallback to markdown
    text = '{"not": "openapi"}'
    iface = read_docs_text(text, source="fallback.md")
    # not openapi, so treated as markdown (no crash)
    assert iface.doc_type == "markdown"


def test_read_docs_file_atlas(tmp_path: Path) -> None:
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(MINIMAL_OPENAPI), encoding="utf-8")
    iface = read_docs_file(p)
    assert iface.doc_type == "openapi"
    assert iface.source == str(p)


def test_read_docs_file_harbor_real() -> None:
    # real fixtures
    p = Path("simulators/docs/atlaspms-openapi.json")
    if not p.exists():
        pytest.skip("no simulator docs on this path")
    iface = read_docs_file(p)
    assert iface.doc_type == "openapi"
    assert len(iface.operations) >= 4
    assert iface.auth is not None
    assert iface.auth.kind == "api_key_header"
    assert iface.auth.header_name == "x-api-key"
    for op in iface.operations:
        assert op.provenance is not None
        assert op.provenance.line >= 1


def test_read_docs_file_harbor_markdown_real() -> None:
    p = Path("simulators/docs/harbor.md")
    if not p.exists():
        pytest.skip("no harbor docs")
    iface = read_docs_file(p)
    assert iface.doc_type == "markdown"
    assert iface.base_url == "http://localhost:8106"
    assert iface.auth is not None
    # provenance every operation
    for op in iface.operations:
        assert op.provenance is not None
    # must have pagination/date/status notes
    assert len(iface.pagination_notes) > 0
    assert len(iface.date_notes) > 0
    assert len(iface.status_notes) > 0


def test_provenance_str() -> None:
    p = Provenance(source="x.md", line=5, snippet="hello")
    assert str(p) == "x.md:5"


def test_auth_description_fields() -> None:
    iface = parse_markdown(HARBOR_SNIPPET, source="h.md")
    assert iface.auth is not None
    assert iface.auth.provenance is not None
    assert iface.auth.provenance.source == "h.md"


def test_openapi_yaml_file(tmp_path: Path) -> None:
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(MINIMAL_OPENAPI), encoding="utf-8")
    iface = read_docs_file(p)
    assert iface.doc_type == "openapi"


def test_openapi_text_with_leading_whitespace() -> None:
    text = "\n  \n" + json.dumps(MINIMAL_OPENAPI)
    iface = read_docs_text(text, source="ws.json")
    assert iface.doc_type == "openapi"


def test_markdown_heading_auth_fallback() -> None:
    # only heading contains auth, no content line
    text = "# Title\n## Authentication\nUse bearer token here but not in content? Actually content has it.\n"
    iface = parse_markdown(text, source="ah.md")
    assert iface.auth is not None


# ---------------------------------------------------------------------------
# POST JSON-body APIs (spec v2 discovery: body_fields auth, body_cursor)
# ---------------------------------------------------------------------------

POST_BODY_OPENAPI = {
    "openapi": "3.0.1",
    "info": {"title": "Example Connector API", "version": "2023-06-06"},
    "servers": [{"url": "https://api.example.test/api/connector/v1"}],
    "paths": {
        "/widgets/getAll/2023-06-06": {
            "post": {
                "operationId": "widgetsGetAll",
                "summary": "Get all widgets",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "allOf": [
                                    {"$ref": "#/components/schemas/ApiBase"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "Limitation": {"$ref": "#/components/schemas/Limitation"},
                                        },
                                    },
                                ]
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "One page of widgets plus the paging cursor.",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/WidgetsGetAllResponse"}
                            }
                        },
                    }
                },
            }
        },
        "/configuration/get/2023-06-06": {
            "post": {
                "operationId": "configurationGet",
                "summary": "Get configuration",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ApiBase"}
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Configuration rows.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "Enterprises": {"type": "array"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
    },
    "components": {
        "schemas": {
            "ApiBase": {
                "type": "object",
                "required": ["ClientToken", "AccessToken", "Client"],
                "properties": {
                    "ClientToken": {"type": "string", "description": "Identifies your integration."},
                    "AccessToken": {"type": "string", "description": "Identifies the enterprise."},
                    "Client": {"type": "string", "description": "Your application name and version."},
                },
            },
            "Limitation": {
                "type": "object",
                "required": ["Count"],
                "properties": {
                    "Count": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "Cursor": {"type": "string", "nullable": True},
                },
            },
            "WidgetsGetAllResponse": {
                "type": "object",
                "required": ["Widgets", "Cursor"],
                "properties": {
                    "Widgets": {"type": "array"},
                    "Cursor": {"type": "string", "nullable": True},
                },
            },
        }
    },
}


def test_post_body_operations_capture_request_body() -> None:
    iface = parse_openapi_dict(POST_BODY_OPENAPI, source="post.json")
    assert len(iface.operations) == 2
    widgets = next(o for o in iface.operations if o.operation_id == "widgetsGetAll")
    assert widgets.method == "POST"
    assert {b.name for b in widgets.body_fields} >= {"ClientToken", "AccessToken", "Client", "Limitation"}
    token_field = next(b for b in widgets.body_fields if b.name == "ClientToken")
    assert token_field.required is True
    assert widgets.body_required == ["ClientToken", "AccessToken", "Client"]
    assert widgets.body_cursor_path == "Limitation.Cursor"
    assert widgets.body_count_path == "Limitation.Count"
    assert widgets.body_count_max == 1000
    assert widgets.response_items_field == "Widgets"
    assert widgets.response_cursor_field == "Cursor"
    assert widgets.pagination_hint == "body_cursor"


def test_post_body_config_operation_has_no_paging_signal() -> None:
    iface = parse_openapi_dict(POST_BODY_OPENAPI, source="post.json")
    config = next(o for o in iface.operations if o.operation_id == "configurationGet")
    assert config.method == "POST"
    assert config.body_cursor_path is None
    assert config.response_cursor_field is None
    assert config.response_items_field == "Enterprises"
    assert config.pagination_hint == "none"


def test_post_body_auth_kind_from_required_body_fields() -> None:
    iface = parse_openapi_dict(POST_BODY_OPENAPI, source="post.json")
    assert iface.auth is not None
    assert iface.auth.kind == "body_fields"
    assert tuple(iface.auth.body_fields) == ("ClientToken", "AccessToken")
    assert iface.auth.provenance is not None


def test_allof_request_schemas_expose_base_fields() -> None:
    iface = parse_openapi_dict(POST_BODY_OPENAPI, source="post.json")
    req = next(s for s in iface.schemas if s.name == "ApiBase")
    assert {f.name for f in req.fields} == {"ClientToken", "AccessToken", "Client"}


def test_credential_body_field_names_generic() -> None:
    from autobridge.discovery.docs_reader import is_credential_body_field

    assert is_credential_body_field("ClientToken")
    assert is_credential_body_field("AccessToken")
    assert is_credential_body_field("api_secret")
    assert not is_credential_body_field("Client")
    assert not is_credential_body_field("Limitation")
    assert not is_credential_body_field("")


def test_openapi_nested_object_fields_are_dotted() -> None:
    """REQ-020 5c: object-typed schema properties expose one dotted level.

    Meridian's OrderItem.UnitAmount ($ref Money) surfaces as
    UnitAmount.GrossValue / UnitAmount.Currency so join detection and field
    mapping can address money leaves without new vocabulary.
    """
    from pathlib import Path

    from autobridge.discovery.docs_reader import read_docs_text

    yaml_text = (Path(__file__).resolve().parents[2] / "simulators" / "docs" / "meridian-openapi.yaml").read_text(
        encoding="utf-8"
    )
    iface = read_docs_text(yaml_text, source="meridian-openapi.yaml")
    by_name = {s.name: s for s in iface.schemas}
    order_fields = {f.name for f in by_name["OrderItem"].fields}
    assert "UnitAmount.GrossValue" in order_fields
    assert "UnitAmount.Currency" in order_fields
    assert "UnitAmount" in order_fields
    customer_fields = {f.name for f in by_name["Customer"].fields}
    assert "FirstName" in customer_fields
