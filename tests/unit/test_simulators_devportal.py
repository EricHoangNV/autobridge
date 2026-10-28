"""Vendor developer portals: page, rendered source and assets on each simulator."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from simulators.atlaspms.app import app as atlas_app
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
