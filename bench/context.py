"""Shared bench context — exactly what every arm gets for a case (REQ-046).

Both frontier arms receive **exactly the context the engine has** for
the same case:

* the ConnectorSpec JSON Schema (the format answers are validated against),
* the vocabulary version's capabilities (methods, auth, pagination,
  protocols, transforms, checks, stop conditions, L1 rules),
* **one complete example spec for a different vendor** — never the
  case's own vendor and never held-out material (``generated/`` dev
  reference specs only; anything under ``verifier/`` is refused),
* the vendor docs for the case,
* the visible tests, and a precise answer format.

The bundle is content-hashed (:func:`BenchContext.content_hash`); the
fair-context test asserts the engine arm and both frontier arms resolve
the same hash for a case. The agent arm's sandbox holds the same files
(:func:`write_agent_sandbox`), and its brief names them.

Trust boundary (DIRECTIVES §2): this module reads the engine's spec
models, the ``generated/`` reference specs and ``simulators/docs`` —
the way the tests do — but never imports ``verifier`` (grading stays
HTTP-only).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATED_DIR = REPO_ROOT / "generated" / "connectors"

#: Precise answer format every frontier arm is given (and graded against).
ANSWER_FORMAT = (
    "Respond with ONLY the ConnectorSpec YAML document "
    "(a bare document or one fenced ```yaml block) — no prose, no tool calls."
)

#: Sandbox file names the agent arm finds (written by write_agent_sandbox).
SANDBOX_FILES: tuple[str, ...] = (
    "schema.json",
    "capabilities.json",
    "example_spec.yaml",
    "vendor_docs.md",
    "visible_tests.json",
    "task.md",
)

_SCHEMA_CACHE: dict[str, Any] | None = None


def connector_schema_dict() -> dict[str, Any]:
    """The ConnectorSpec JSON Schema as a dict (answers validate against it)."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        from autobridge.spec.models import ConnectorSpec  # noqa: PLC0415

        _SCHEMA_CACHE = dict(ConnectorSpec.model_json_schema())
    return dict(_SCHEMA_CACHE)


def connector_schema_json() -> str:
    """The ConnectorSpec JSON Schema, canonical JSON (stable key order)."""
    return json.dumps(connector_schema_dict(), sort_keys=True, default=str)


def _version_for_spec_version(spec_version: Any) -> str:
    try:
        v = int(spec_version)
    except (TypeError, ValueError):
        return "v1"
    return {1: "v1", 2: "v2"}.get(v, "v3") if v in (1, 2) else "v3"


def capabilities_for_version(version: str) -> dict[str, Any]:
    """Capabilities slice for one vocabulary version (methods, auth, ...).

    Built from the same :func:`capabilities_payload` the ``/capabilities``
    endpoint serves, so the bench context can never drift from what the
    engine reports it knows. ``version`` is ``v1``/``v2``/``v3``.
    """
    from autobridge.control.routes.capabilities import capabilities_payload  # noqa: PLC0415

    payload = capabilities_payload()
    versions = payload.get("versions", {})
    if version not in versions:
        raise KeyError(f"unknown vocabulary version {version!r}")
    return {
        "vocabulary_version": version,
        "methods": versions[version].get("methods", []),
        "auth_types": versions[version].get("auth_types", []),
        "pagination_strategies": versions[version].get("pagination_strategies", []),
        "protocols": versions[version].get("protocols", []),
        "transforms": payload.get("transforms", []),
        "checks": payload.get("checks", []),
        "stop_conditions": payload.get("stop_conditions", []),
        "rules": payload.get("rules", []),
    }


def capabilities_json_for_version(version: str) -> str:
    """Canonical JSON of :func:`capabilities_for_version` (stable key order)."""
    return json.dumps(capabilities_for_version(version), sort_keys=True, default=str)


def available_example_vendors() -> list[str]:
    """Generated connectors with a reference spec (dev material only)."""
    vendors: list[str] = []
    if not GENERATED_DIR.is_dir():
        return vendors
    for child in sorted(GENERATED_DIR.iterdir()):
        if child.is_dir() and (child / "reference" / "spec.yaml").is_file():
            vendors.append(child.name)
    return vendors


def example_vendor_for(connector_id: str) -> str:
    """A different vendor's id for the worked example (never the case's own).

    Deterministic preference (harbor ↔ atlas, meridian → atlas) with a
    sorted fallback to the first available vendor that is not the case's
    own. Raises when no other vendor exists (a single-vendor checkout
    cannot run a fair baseline).
    """
    preferred: dict[str, str] = {
        "atlaspms": "harborpms",
        "harborpms": "atlaspms",
        "meridianpms": "atlaspms",
    }
    available = available_example_vendors()
    if not available:
        raise RuntimeError("no generated reference specs available for the bench example")
    want = preferred.get(connector_id)
    if want and want != connector_id and want in available:
        return want
    for vendor in available:
        if vendor != connector_id:
            return vendor
    raise RuntimeError(f"no different-vendor example available for {connector_id!r}")


def example_spec_for(connector_id: str) -> tuple[str, dict[str, Any], str]:
    """``(vendor, spec_dict, spec_yaml)`` for the worked example.

    The example is a complete dev reference spec for a **different**
    vendor. Anything resolving under ``verifier/`` (hidden cases, answer
    keys) is refused — the example is dev material only.
    """
    import yaml  # noqa: PLC0415

    vendor = example_vendor_for(connector_id)
    path = GENERATED_DIR / vendor / "reference" / "spec.yaml"
    resolved = path.resolve()
    repo = REPO_ROOT.resolve()
    if repo not in resolved.parents and resolved != repo:
        raise RuntimeError(f"bench example resolves outside the repo: {path}")
    if "verifier" in resolved.parts or "hidden_cases" in resolved.parts:
        raise RuntimeError(f"bench example must never be hidden material: {path}")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise RuntimeError(f"bench example is not a mapping: {path}")
    return vendor, data, text


def vendor_docs_text(docs_ref: str) -> str:
    """Full vendor docs text for *docs_ref* (empty when unreadable).

    The prompt renderer truncates per the shared budget with a loud
    marker; the context hash covers this full text so every arm agrees.
    """
    if not docs_ref:
        return ""
    candidates = [pathlib.Path(docs_ref), REPO_ROOT / docs_ref]
    base = pathlib.Path(docs_ref).name
    candidates.append(REPO_ROOT / "simulators" / "docs" / base)
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


@dataclass(frozen=True, slots=True)
class BenchContext:
    """Everything every arm gets for one case (equal inputs + shared context)."""

    scenario_id: str
    connector_id: str
    vocabulary_version: str
    schema_json: str
    capabilities_json: str
    example_vendor: str
    example_spec_yaml: str
    docs_text: str
    visible_tests: tuple[str, ...] = ()
    answer_format: str = ANSWER_FORMAT

    def content_hash(self) -> str:
        """sha256 over the canonical context (arms must agree on this)."""
        blob = json.dumps(
            {
                "schema_json": self.schema_json,
                "capabilities_json": self.capabilities_json,
                "example_vendor": self.example_vendor,
                "example_spec_yaml": self.example_spec_yaml,
                "docs_text": self.docs_text,
                "visible_tests": sorted(self.visible_tests),
                "answer_format": self.answer_format,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view (hash included; docs/spec inline for the audit)."""
        return {
            "scenario_id": self.scenario_id,
            "connector_id": self.connector_id,
            "vocabulary_version": self.vocabulary_version,
            "schema_json": self.schema_json,
            "capabilities_json": self.capabilities_json,
            "example_vendor": self.example_vendor,
            "example_spec_yaml": self.example_spec_yaml,
            "docs_text": self.docs_text,
            "visible_tests": list(self.visible_tests),
            "answer_format": self.answer_format,
            "content_hash": self.content_hash(),
        }


def build_bench_context(
    *,
    scenario_id: str,
    connector_id: str,
    spec: dict[str, Any] | None,
    docs_ref: str,
    visible_tests: tuple[str, ...] | list[str] = (),
) -> BenchContext:
    """Resolve the shared context bundle for one case.

    *spec* is the pre-drift spec (its ``spec_version`` picks the
    vocabulary slice). Nothing here reads case diagnostics, the
    verifier, or hidden cases — task setup only.
    """
    version = "v1"
    if isinstance(spec, dict):
        version = _version_for_spec_version(spec.get("spec_version", 1))
    example_vendor, _example_spec, example_yaml = example_spec_for(connector_id)
    return BenchContext(
        scenario_id=scenario_id,
        connector_id=connector_id,
        vocabulary_version=version,
        schema_json=connector_schema_json(),
        capabilities_json=capabilities_json_for_version(version),
        example_vendor=example_vendor,
        example_spec_yaml=example_yaml,
        docs_text=vendor_docs_text(docs_ref),
        visible_tests=tuple(visible_tests),
    )


def render_frontier_oneshot_prompt(inputs: Any, context: BenchContext) -> list[dict[str, str]]:
    """Render the file-based one-shot prompt (REQ-046) to chat messages."""
    from autobridge.llm.prompts import render_prompt  # noqa: PLC0415
    from bench.bench_prompts import get_bench_prompt  # noqa: PLC0415

    template = get_bench_prompt("frontier_oneshot")
    values = {
        "scenario_id": str(getattr(inputs, "scenario_id", context.scenario_id)),
        "trial": str(getattr(inputs, "trial", 0)),
        "seed": str(getattr(inputs, "seed", 0)),
        "docs_ref": str(getattr(inputs, "docs_ref", "") or ""),
        "sandbox_url": str(getattr(inputs, "sandbox_url", "") or ""),
        "credential_refs": ", ".join(getattr(inputs, "credential_refs", ()) or ()) or "(none)",
        "visible_tests": ", ".join(getattr(inputs, "visible_tests", ()) or ()) or "(none)",
        "token_ceiling": str(getattr(inputs, "token_ceiling", 0)),
        "wall_clock_s": str(getattr(inputs, "wall_clock_s", 0)),
        "schema_json": context.schema_json,
        "capabilities_json": context.capabilities_json,
        "example_vendor": context.example_vendor,
        "example_spec_yaml": context.example_spec_yaml,
        "docs_text": context.docs_text,
        "answer_format": context.answer_format,
    }
    return render_prompt(template, **values)


def render_frontier_agent_brief(
    inputs: Any, context: BenchContext, *, sandbox_files: tuple[str, ...] = SANDBOX_FILES
) -> list[dict[str, str]]:
    """Render the file-based agent brief (REQ-046) to chat messages."""
    from autobridge.llm.prompts import render_prompt  # noqa: PLC0415
    from bench.bench_prompts import get_bench_prompt  # noqa: PLC0415

    template = get_bench_prompt("frontier_agent")
    values = {
        "scenario_id": str(getattr(inputs, "scenario_id", context.scenario_id)),
        "trial": str(getattr(inputs, "trial", 0)),
        "seed": str(getattr(inputs, "seed", 0)),
        "docs_ref": str(getattr(inputs, "docs_ref", "") or ""),
        "sandbox_url": str(getattr(inputs, "sandbox_url", "") or ""),
        "sandbox_image": str(getattr(inputs, "sandbox_image", "") or ""),
        "credential_refs": ", ".join(getattr(inputs, "credential_refs", ()) or ()) or "(none)",
        "visible_tests": ", ".join(getattr(inputs, "visible_tests", ()) or ()) or "(none)",
        "token_ceiling": str(getattr(inputs, "token_ceiling", 0)),
        "wall_clock_s": str(getattr(inputs, "wall_clock_s", 0)),
        "schema_json": context.schema_json,
        "capabilities_json": context.capabilities_json,
        "example_vendor": context.example_vendor,
        "example_spec_yaml": context.example_spec_yaml,
        "docs_text": context.docs_text,
        "answer_format": context.answer_format,
        "sandbox_files": "\n".join(f"- {name}" for name in sandbox_files),
    }
    return render_prompt(template, **values)


def messages_to_text(messages: list[dict[str, str]]) -> str:
    """Flatten chat messages to the single prompt string drivers take."""
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "")
        content = message.get("content", "")
        parts.append(f"[{role}]\n{content}" if role else content)
    return "\n\n".join(parts)


def write_agent_sandbox(context: BenchContext, dest: str | pathlib.Path) -> list[str]:
    """Write the shared context files the agent brief names; return file names.

    The sandbox holds the same context the engine has: the schema, the
    vocabulary capabilities, the different-vendor example, the vendor
    docs, the visible tests, and the rendered task brief (``task.md``).
    """
    dest_path = pathlib.Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)
    files = {
        "schema.json": context.schema_json,
        "capabilities.json": context.capabilities_json,
        "example_spec.yaml": context.example_spec_yaml,
        "vendor_docs.md": context.docs_text,
        "visible_tests.json": json.dumps(list(context.visible_tests), indent=2, sort_keys=True),
        "task.md": (
            f"# Bench task: {context.scenario_id}\n\n"
            f"Vocabulary: {context.vocabulary_version}; "
            f"worked example vendor: {context.example_vendor} "
            "(different vendor — follow its shape, not its values).\n\n"
            f"Answer format: {context.answer_format}\n"
        ),
    }
    written: list[str] = []
    for name in SANDBOX_FILES:
        target = dest_path / name
        # Stay inside the sandbox dir (never follow a hostile file name).
        if target.resolve().parent != dest_path.resolve():
            raise RuntimeError(f"bench sandbox file escapes its directory: {name!r}")
        target.write_text(files[name], encoding="utf-8")
        written.append(name)
    return written


def healthz() -> dict[str, str]:
    """Health check for the bench context module."""
    return {"status": "ok", "service": "bench_context"}
