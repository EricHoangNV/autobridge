"""Vendor developer portals: page, rendered source and assets on each simulator."""

from __future__ import annotations

import re as _re

import pytest
from fastapi.testclient import TestClient

from simulators.atlaspms.app import app as atlas_app
from simulators.fias_pabx.app import app as fias_app
from simulators.harborpms.app import app as harbor_app
from simulators.legacystay.app import app as legacystay_app
from simulators.meridianpms.app import app as meridian_app

PORTALS = [
    (atlas_app, "AtlasPMS", "openapi.js"),
    (harbor_app, "HarborPMS Wiki", "markdown.js"),
    (legacystay_app, "LegacyStay Systems", "markdown.js"),
]


@pytest.mark.parametrize(("app", "brand", "script"), PORTALS)
def test_portal_page_names_vendor_and_loads_renderer(app, brand: str, script: str) -> None:  # type: ignore[no-untyped-def]
    r = TestClient(app).get("/developers")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert brand in r.text
    assert f"/developers/assets/{script}" in r.text
    assert "Simulated vendor documentation" in r.text


def test_atlas_source_is_the_yaml_parsed_to_json() -> None:
    r = TestClient(atlas_app).get("/developers/source")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"].startswith("3.1")
    assert "/api/v1.3/getReservations" in spec["paths"]
    assert "ReservationItem" in spec["components"]["schemas"]


@pytest.mark.parametrize(("app", "heading"), [(harbor_app, "# HarborPMS API"), (legacystay_app, "# LegacyStay API")])
def test_markdown_source_is_served_as_text(app, heading: str) -> None:  # type: ignore[no-untyped-def]
    r = TestClient(app).get("/developers/source")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith(heading)


@pytest.mark.parametrize("name", ["markdown.js", "openapi.js", "banner.css"])
def test_known_assets_are_served(name: str) -> None:
    r = TestClient(harbor_app).get(f"/developers/assets/{name}")
    assert r.status_code == 200
    assert r.content


@pytest.mark.parametrize("name", ["atlas.html", "..%2Fapp.py", "secrets.txt"])
def test_other_assets_are_404(name: str) -> None:
    assert TestClient(harbor_app).get(f"/developers/assets/{name}").status_code == 404


def test_missing_source_is_404(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import simulators.common.devportal as devportal

    monkeypatch.setattr(devportal, "DOCS_DIR", tmp_path)
    assert TestClient(harbor_app).get("/developers/source").status_code == 404


def test_raw_docs_mount_still_serves_files() -> None:
    """The /docs static mount AutoBridge's docs reader uses is unchanged."""
    assert TestClient(harbor_app).get("/docs/harbor.md").status_code == 200


def test_meridian_portal_is_guides_first_and_offline() -> None:
    """REQ-020 item 3: guides-first docs site, one HTML page, no CDN/fonts."""
    r = TestClient(meridian_app).get("/developers")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Meridian" in r.text
    assert "Simulated vendor documentation" in r.text
    # Guides sidebar (static) and product switcher / search / theme controls.
    for guide in ("Getting started", "Authentication", "Requests", "Responses", "Pagination", "Serialization"):
        assert guide in r.text
    assert "Connector API" in r.text and "Changelog" in r.text
    assert "⌘K" in r.text
    # Offline: no external stylesheets, fonts, scripts or images.
    assert "<link" not in r.text
    assert "fonts.googleapis" not in r.text and "cdn." not in r.text
    assert 'src="http' not in r.text and "src='http" not in r.text


def test_meridian_source_is_the_yaml_parsed_to_json() -> None:
    """The YAML is the single source: operations render from it at load time."""
    r = TestClient(meridian_app).get("/developers/source")
    assert r.status_code == 200
    spec = r.json()
    paths = spec["paths"]
    for op in (
        "/reservations/getAll/2023-06-06",
        "/customers/getAll/2023-06-06",
        "/orderItems/getAll/2023-06-06",
        "/configuration/get/2023-06-06",
    ):
        assert op in paths
        assert "post" in paths[op]
    assert "Limitation" in spec["components"]["schemas"]
    assert TestClient(meridian_app).get("/docs/meridian-openapi.yaml").status_code == 200


def test_meridian_portal_fetches_absolute_source() -> None:
    """Owner check (REQ-020 sub-step A): fetch('source') 404s from /developers.

    The page must use the absolute /developers/source like the other portals.
    """
    html = TestClient(meridian_app).get("/developers").text
    assert 'fetch("/developers/source"' in html
    assert 'fetch("source")' not in html


def test_meridian_portal_renders_at_least_one_operation() -> None:
    """Owner check: the Operations pages have data + renderer for every op.

    Mirrors the page's own mapping (paths -> post operations) and asserts each
    served POST operation carries the fields renderOperation needs
    (operationId, summary), and the HTML ships that renderer.
    """
    html = TestClient(meridian_app).get("/developers").text
    assert "function renderOperation" in html
    assert "On this page" in html
    spec = TestClient(meridian_app).get("/developers/source").json()
    ops = [(path, item.get("post", {})) for path, item in spec["paths"].items()]
    assert len(ops) >= 1
    for path, op in ops:
        assert op.get("operationId"), f"{path} has no operationId"
        assert op.get("summary"), f"{path} has no summary"
    assert "#/operations/" in html


# ---------------------------------------------------------------------------
# REQ-022 sub-step A: FIAS PABX document viewer
# ---------------------------------------------------------------------------


def _fias_md() -> str:
    from pathlib import Path as _Path

    return (_Path(__file__).resolve().parents[2] / "simulators" / "docs" / "fias-pabx.md").read_text(
        encoding="utf-8"
    )


def test_fias_portal_is_a_document_viewer_and_offline() -> None:
    """The PABX spec is viewed as an uploaded document, not a docs site."""
    r = TestClient(fias_app).get("/developers")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "Grand Plaza" in html
    assert "Simulated vendor documentation" in html
    assert "CONFIDENTIAL" in html
    # Viewer chrome: file header bar, thumbnail rail, zoom, page count.
    assert "Grand_Plaza_PABX_Interface_Specification_FIAS_v1.0.pdf" in html
    assert 'id="rail"' in html and 'id="zin"' in html
    # Offline: no external stylesheets, fonts, scripts or images — only
    # same-origin viewer assets and document links.
    assert 'src="http' not in html and "src='http" not in html
    assert 'href="http' not in html and "href='http" not in html
    assert "fonts.googleapis" not in html and "cdn." not in html


def test_fias_portal_fetches_absolute_source() -> None:
    """Same owner check as Meridian: fetch('source') 404s from /developers."""
    html = TestClient(fias_app).get("/developers").text
    assert 'fetch("/developers/source"' in html
    assert 'fetch("source")' not in html


def test_fias_source_is_the_spec_markdown() -> None:
    r = TestClient(fias_app).get("/developers/source")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith("# Grand Plaza")
    assert TestClient(fias_app).get("/docs/fias-pabx.md").status_code == 200


def _fias_records_from_spec(md: str) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Mirror of the page's commandIndex: record -> {dirs}, record -> fields.

    Directions come from the document structure (§3 PABX-side, §4
    host-side, §4.5 KA a PABX answer, §2.1 link rows by their Sent-by
    cell — "either side" lands in both); fields from each record's own
    detail table (per-record ✓-columns for multi-record tables, first
    column otherwise). DS/DE live only in §3.4 prose and are listed by
    the page explicitly.
    """
    direction: dict[str, set[str]] = {}
    fields: dict[str, list[str]] = {}

    def table_rows(block: list[str]) -> list[list[str]]:
        rows = []
        for line in block:
            if _re.match(r"^\s*\|?[\s:|-]+\|?[\s:|.-]*$", line) and "-" in line:
                continue
            rows.append([c.strip().strip("`") for c in line.strip().strip("|").split("|")])
        return rows

    section = 0
    current: list[str] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        heading = _re.match(r"^(#{2,3})\s+(.*)$", line)
        if heading:
            if len(heading.group(1)) == 2:
                m = _re.match(r"^(\d+)\.", heading.group(2))
                section = int(m.group(1)) if m else 0
            current = []
            if len(heading.group(1)) == 3:
                for tok in _re.findall(r"`([A-Z#]{2})`", heading.group(2)):
                    if tok not in current:
                        current.append(tok)
                        if section == 3 or tok == "KA":
                            direction.setdefault(tok, set()).add("pabx")
                        else:
                            direction.setdefault(tok, set()).add("host")
            i += 1
            continue
        if line.strip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            rows = table_rows(block)
            if rows and rows[0] == ["Record", "Name", "Fields", "Sent by"]:
                for cells in rows[1:]:
                    sent = cells[3].lower()
                    if "pabx" in sent or "either side" in sent:
                        direction.setdefault(cells[0], set()).add("pabx")
                    if "host" in sent or "either side" in sent:
                        direction.setdefault(cells[0], set()).add("host")
                    # The Fields cell names the ids the record carries
                    # ("`DA` date, `TI` time"; parenthesised values like
                    # (`PB` = PABX) are enum values, not fields).
                    bare = _re.sub(r"\([^)]*\)", "", cells[2])
                    for fid in _re.findall(r"`([A-Z#]{2})`", bare):
                        fields.setdefault(cells[0], [])
                        if fid not in fields[cells[0]]:
                            fields[cells[0]].append(fid)
                continue
            if current and len(rows) > 1:
                head, rest = rows[0], rows[1:]
                multi = len(head) > 2 and all(_re.fullmatch(r"[A-Z#]{2}", c) for c in head[2:])
                for cells in rest:
                    ids = _re.findall(r"[A-Z#]{2}", cells[0].replace("`", ""))
                    targets = list(current)
                    if multi:
                        targets = [t for t in current if head.index(t) < len(cells) and cells[head.index(t)]]
                    for t in targets:
                        fields.setdefault(t, [])
                        for fid in ids:
                            if fid not in fields[t]:
                                fields[t].append(fid)
            continue
        i += 1
    return direction, fields


def test_fias_command_list_covers_every_record() -> None:
    """Mirror check: the page's command list has every record type with fields.

    Re-derives record -> direction/fields from fias-pabx.md independently
    of the page JS, then asserts the page knows each one (ANSWERS map) and
    renders both direction tables.
    """
    html = TestClient(fias_app).get("/developers").text
    assert "Command list" in html
    # Direction-table headings (JS source form: \u escapes in the file).
    assert "PABX \\u2192 host" in html and "host \\u2192 PABX" in html
    assert "function commandIndex" in html and "function renderCommandList" in html

    direction, fields = _fias_records_from_spec(_fias_md())
    assert set(direction) >= {
        "PS", "RE", "WA", "DR", "GI", "GC", "GO", "XL", "XD", "WR", "WC",
        "KR", "KD", "KZ", "KA", "PA", "LS", "LD", "LR", "LA", "LE",
    }
    assert direction["KA"] == {"pabx"}
    assert direction["LS"] == {"pabx", "host"}
    assert direction["PS"] == {"pabx"}
    assert direction["GI"] == {"host"}
    for rec in direction:
        assert f"{rec}:" in html, f"{rec} missing from the page's ANSWERS map"
    for rec in ("PS", "RE", "WA", "GI", "KR", "PA", "LD", "LR"):
        assert fields.get(rec), f"{rec} has no fields parsed from its table"
    # DS/DE exist only in §3.4 prose: the page lists them explicitly.
    assert "Database Swap start" in html and "Database Swap end" in html
    # Field order fidelity: the PS layout the PABX announces.
    assert "RNP#DATITADDDUSO" not in html  # layouts come from the live sim, not the page
