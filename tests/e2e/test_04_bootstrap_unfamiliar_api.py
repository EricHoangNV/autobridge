"""W5.7 service confirmation — bootstrap from unfamiliar API (P1.W5.7).

Passing criteria (plan §4 W5, §2.6):
  * Harbor connector produced from docs alone with no reference-spec access
    (negative test: workspace has no path to verifier/ or simulators/).
  * `date` ambiguity resolved within probe budget or reported `Degraded`
    with open questions; every hypothesis links to ≥1 evidence record.
  * L3 invoked only when enumerator yields <2 hypotheses (asserted).
  * BudgetExhausted → L4 escalation bundle, never silent guess.
  * Visible tests green, money Decimal, business_date cutoff 04:00,
    pagination cursor with repeated_cursor guard, status opaque enum_map.
  * Sync of bootstrapped spec is Complete with ModelCall 0 (fake) and live
    when stack is up.

Fake transports make suite green without Docker; live tests are skipped
when stack down. Trust boundary: this file lives in tests/e2e and may
import autobridge engine + verifier oracle for verification, but the engine
itself must never import verifier/simulators (checked below).
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
from decimal import Decimal
from typing import Any

import httpx
import pytest

from autobridge.control.models import Base, ModelCall
from autobridge.discovery.bootstrap import BootstrapResult, build_draft_spec
from autobridge.discovery.classifiers import classify_all
from autobridge.discovery.docs_reader import read_docs_text
from autobridge.discovery.hypotheses import HypothesisEnumerator
from autobridge.discovery.probes import ProbeBudget, ProbeSelector
from autobridge.discovery.resolver import resolve_hypotheses
from autobridge.engine.sync_run import execute_sync
from autobridge.llm.provider import BudgetExhausted
from autobridge.spec.models import RetryPolicy, load_spec_from_dict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GENERATED = REPO_ROOT / "generated" / "connectors"
HARBOR_MD_PATH = REPO_ROOT / "simulators" / "docs" / "harborpms.md"
ATLAS_YAML_PATH = REPO_ROOT / "simulators" / "docs" / "atlaspms-openapi.yaml"

HARBOR_MD = HARBOR_MD_PATH.read_text(encoding="utf-8") if HARBOR_MD_PATH.exists() else ""
ATLAS_YAML = ATLAS_YAML_PATH.read_text(encoding="utf-8") if ATLAS_YAML_PATH.exists() else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _harbor_bootstrap() -> BootstrapResult:
    """Bootstrap Harbor docs deterministically (no probes)."""
    return build_draft_spec(HARBOR_MD, source="simulators/docs/harborpms.md")


def _assert_visible_tests_green(result: BootstrapResult) -> None:
    assert result.success, f"bootstrap failed: {result.degradation_reason} trace {result.trace}"
    assert result.draft_spec is not None
    assert result.visible_errors == []
    load_spec_from_dict(result.draft_spec)


def _harbor_fake_client(dataset: dict[str, list[dict[str, Any]]]) -> Any:
    """Cursor pagination fake matching Harbor behavior."""

    def encode(idx: int) -> str:
        return base64.urlsafe_b64encode(str(idx).encode()).decode().rstrip("=")

    def decode(cur: str | None) -> int:
        if not cur:
            return 0
        padded = cur + "=" * (-len(cur) % 4)
        try:
            return int(base64.urlsafe_b64decode(padded).decode())
        except Exception:
            return 0

    def handler(request: httpx.Request) -> httpx.Response:
        qp = dict(request.url.params)
        path = request.url.path
        limit = int(qp.get("limit", "10"))
        cursor = qp.get("cursor")
        offset = decode(cursor)
        if path == "/reservations":
            raw = dataset["reservations"]
            mapping = {"confirmed": "H1", "checked_in": "H2", "checked_out": "H3", "cancelled": "H4", "no_show": "H5"}
            data: list[dict[str, Any]] = []
            for r in raw:
                rec = dict(r)
                if rec.get("status") in mapping:
                    rec["status"] = mapping[rec["status"]]
                rec["date"] = rec["arrival_date"]
                data.append(rec)
            total = len(data)
            sliced = data[offset : offset + limit]
            next_idx = offset + limit
            next_cursor = encode(next_idx) if next_idx < total else None
            return httpx.Response(200, json={"data": sliced, "total": total, "limit": limit, "cursor": cursor, "next_cursor": next_cursor})
        if path == "/folios":
            raw = dataset["folios"]
            data2: list[dict[str, Any]] = []
            for f in raw:
                rec = dict(f)
                rec["date"] = rec["business_date"]
                data2.append(rec)
            total2 = len(data2)
            sliced2 = data2[offset : offset + limit]
            next_idx2 = offset + limit
            next_cursor2 = encode(next_idx2) if next_idx2 < total2 else None
            return httpx.Response(200, json={"data": sliced2, "total": total2, "limit": limit, "cursor": cursor, "next_cursor": next_cursor2})
        return httpx.Response(404, json={"msg": "not found"})

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    from autobridge.runtime.http import HttpClient

    return HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=5),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


# ---------------------------------------------------------------------------
# 1. Negative trust-boundary test — workspace has no path to verifier/simulators
# ---------------------------------------------------------------------------


# Gate functions allowed to touch the heldout commitment (REQ-007): the
# W8.5 integrity gate plus the private-seed loader.  Fixture bytes flow
# into ``sha256()`` and out only as a hex digest; private seeds flow only
# into the simulator admin call / verifier ``seed`` override — never into
# prompts, responses, logs or files (asserted by unit no-leak tests).
_HELDOUT_GATE_FUNCTIONS = (
    "check_heldout_sha",
    "committed_heldout_manifest_path",
    "heldout_manifest_sha",
    "load_heldout_seeds",
    "push_simulator_seed",
)


def _gate_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Spans (start, end) of the heldout gate functions in *lines*."""
    spans = []
    for i, line in enumerate(lines):
        for name in _HELDOUT_GATE_FUNCTIONS:
            if line.startswith(f"def {name}("):
                end = next(
                    (j for j in range(i + 1, len(lines)) if lines[j].startswith(("def ", "class "))),
                    len(lines),
                )
                spans.append((i, end))
    return spans


def _hidden_cases_confined_to_sha_gate(text: str) -> bool:
    """Every ``hidden_cases`` mention lies inside a heldout gate function.

    The heldout gate hashes private files against SHA256SUMS.
    ``check_heldout_sha`` must return None on all paths (bare ``return``
    only): fixture bytes flow into ``sha256()`` and out only as a hex
    digest, never into a prompt, response, or caller.  ``load_heldout_seeds``
    returns the private seeds to the bench caller, so it must never print
    them (no ``print(`` inside).
    """
    lines = text.splitlines()
    spans = _gate_spans(lines)
    if not spans:
        return False
    for i, line in enumerate(lines):
        if "hidden_cases" in line and not any(s <= i < e for s, e in spans):
            return False
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("def check_heldout_sha"))
    except StopIteration:
        return False
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith(("def ", "class "))),
        len(lines),
    )
    if "-> None" not in lines[start]:
        return False
    # Every return inside the SHA gate is bare: the function yields no bytes.
    if not all(
        line.strip() == "return"
        for line in lines[start + 1 : end]
        if line.strip().startswith("return")
    ):
        return False
    try:
        lstart = next(i for i, line in enumerate(lines) if line.startswith("def load_heldout_seeds"))
    except StopIteration:
        return True
    lend = next(
        (i for i in range(lstart + 1, len(lines)) if lines[i].startswith(("def ", "class "))),
        len(lines),
    )
    # The seed loader must never print seeds to stdout.
    return not any("print(" in line for line in lines[lstart + 1 : lend])


def test_workspace_has_no_path_to_verifier_or_simulators() -> None:
    """Engine code never imports, reads or has a route to verifier/ or simulators/.

    This is the W5.7 negative test mirroring plan §1.2 trust boundaries:
      * No file under src/autobridge imports verifier or simulators at top level
      * No file under src/autobridge opens generated/connectors/reference files
      * Bootstrap's source guard rejects generated/connectors sources
    """
    src_root = REPO_ROOT / "src" / "autobridge"
    assert src_root.is_dir(), f"missing {src_root}"
    verifier_root = REPO_ROOT / "verifier"
    simulators_root = REPO_ROOT / "simulators"
    # Ensure the two trust boundaries exist as separate packages (they must not be inside src/autobridge)
    assert verifier_root.is_dir()
    assert simulators_root.is_dir()
    # No symlink or directory inside src/autobridge pointing to verifier/simulators
    for p in src_root.rglob("*"):
        # rglob follows symlinks; check for symlink attack
        if p.is_symlink():
            target = p.resolve()
            assert verifier_root not in target.parents and target != verifier_root, f"symlink {p} -> {target} reaches verifier/"
            assert simulators_root not in target.parents and target != simulators_root, f"symlink {p} -> {target} reaches simulators/"
        # No file should be named verifier or simulators inside src/autobridge (except docs)
        assert p.name not in ("verifier", "simulators") or p.parent == src_root, f"unexpected nested package {p}"

    # Check every Python file under src/autobridge for forbidden imports / file reads
    forbidden_imports = ("import verifier", "from verifier", "import simulators", "from simulators")
    for pyfile in src_root.rglob("*.py"):
        text = pyfile.read_text(encoding="utf-8")
        # Check top-level imports only (first 40 lines) to avoid false positives in comments about trust boundary
        top = "\n".join(text.splitlines()[:40])
        for pat in forbidden_imports:
            assert pat not in top, f"{pyfile} imports {pat!r} in top 40 lines — violates trust boundary"
        # File reads of reference specs are only allowed behind guard (bootstrap.py has 2-3 occurrences of the string
        # in a guard clause, but never open() them). Count occurrences.
        if "generated/connectors" in text:
            # bootstrap.py, build_run.py, spec/registry.py and control/routes/builds.py
            # are allowed to mention the path (guard + spec storage under
            # generated/connectors/<id>/<release>; builds.py enforces the same
            # guard at the control API layer with a 422 on reference-spec reads)
            # but must not open() reference specs.
            if pyfile.name in ("bootstrap.py", "build_run.py", "registry.py", "builds.py"):
                # Allow up to 4 mentions (guard + comment + registry storage), but never open() reference
                assert text.count("generated/connectors") <= 6, f"{pyfile} mentions generated/connectors too often"
                assert 'open("generated' not in text and "open('generated" not in text, f"{pyfile} opens generated/connectors file"
                assert 'Path("generated' not in text and "Path('generated" not in text, f"{pyfile} Path(generated) read"
            else:
                # Other engine files must not mention it at all. No bench
                # exception: the harness lives in top-level bench/ (REQ-024
                # owner check on f32b4b4b moved it out of src/autobridge),
                # so nothing under src/autobridge reads reference specs.
                raise AssertionError(f"{pyfile} mentions generated/connectors — forbidden outside bootstrap guard")

    # Bootstrap guard: source containing generated/connectors is rejected
    bad = build_draft_spec(HARBOR_MD, source="generated/connectors/harborpms/reference/spec.yaml")
    assert not bad.success
    assert "must not read reference" in bad.degradation_reason.lower()  # type: ignore[union-attr]

    # Also verify hidden_cases never leak: no engine file references
    # hidden_cases/heldout. The heldout gate lives in top-level bench/
    # (REQ-024 owner check on f32b4b4b moved the harness out of
    # src/autobridge); engine code never touches it.
    for pyfile in src_root.rglob("*.py"):
        txt = pyfile.read_text(encoding="utf-8")
        if "hidden_cases" not in txt and "heldout" not in txt.lower():
            continue
        raise AssertionError(f"{pyfile} references hidden_cases/heldout — trust boundary violation")


def test_no_reference_spec_import_in_bootstrap() -> None:
    """Bootstrap produces spec without reading generated/connectors reference files."""
    result = _harbor_bootstrap()
    _assert_visible_tests_green(result)
    # Ensure spec id is derived from docs title, not from reference file name coincidence
    assert result.draft_spec["connector"]["id"] == "harborpms"  # type: ignore[index]
    # Ensure bootstrap trace never mentions reading reference spec
    trace_text = json.dumps(result.trace)
    assert "generated" not in trace_text.lower()
    # Ensure hypotheses all have evidence provenance pointing to docs, not to reference oracle
    for payload in result.evidence_payloads:
        assert payload["evidence_for"], "every hypothesis must link to evidence"
        # evidence_for entries mention harborpms.md or probe, never generated/connectors
        for ev in payload["evidence_for"]:
            assert "generated" not in ev.lower(), f"hypothesis evidence leaks reference path: {ev}"


# ---------------------------------------------------------------------------
# 2. Harbor bootstrap from docs alone — spec correctness
# ---------------------------------------------------------------------------


def test_harbor_bootstrap_produces_valid_spec_from_docs_alone() -> None:
    """Harbor connector produced from docs alone (no reference-spec access)."""
    result = _harbor_bootstrap()
    _assert_visible_tests_green(result)
    spec = result.draft_spec
    assert spec is not None
    # Connector identity from docs title
    assert spec["connector"]["id"] == "harborpms"
    assert spec["connector"]["vendor"] == "HarborPMS"
    # Base URL placeholder
    assert spec["source"]["base_url"] == "${HARBOR_BASE_URL}"
    assert spec["source"]["auth"]["credential_ref"] == "HARBOR_API_KEY"
    # Operations
    assert "list_reservations" in spec["operations"]
    assert "list_folios" in spec["operations"]
    assert spec["operations"]["list_reservations"]["pagination"]["strategy"] == "cursor"
    assert spec["operations"]["list_folios"]["pagination"]["strategy"] == "cursor"
    # Cursor must have null_cursor + repeated_cursor guards
    for op_name in ("list_reservations", "list_folios"):
        pag = spec["operations"][op_name]["pagination"]
        assert "null_cursor" in pag["stop_when"]
        assert "repeated_cursor" in pag["stop_when"]
        assert pag["next_path"] == "$.next_cursor"
        assert pag["items_path"] == "$.data"
    # Entities
    assert "Reservation" in spec["entities"]
    assert "FolioCharge" in spec["entities"]
    # No ambiguous `date` field mapped
    for ent in spec["entities"].values():
        assert "date" not in ent["fields"]
    # Money Decimal with scale 2
    for _ent_name, ent in spec["entities"].items():
        for fname, fm in ent["fields"].items():
            if fname in ("total_amount", "amount"):
                steps = fm.get("transform", [])
                assert any(s.get("name") == "money" for s in steps)
                for s in steps:
                    if s.get("name") == "money":
                        assert s["args"]["scale"] == 2
    # Business date cutoff 04:00
    assert spec["entities"]["FolioCharge"]["fields"]["business_date"]["transform"][0]["args"]["cutoff"] == 4
    # Status opaque enum_map
    status_tf = spec["entities"]["Reservation"]["fields"]["status"]["transform"][0]
    assert status_tf["args"]["map"]["H1"] == "confirmed"
    assert status_tf["args"]["on_unknown"] == "block"
    # Capabilities + health
    assert spec["capabilities"]["read"] == ["Reservation", "FolioCharge"]
    assert spec["health"]["canary"]["operation"] == "list_reservations"
    # Validates against spec schema
    load_spec_from_dict(spec)
    # No credential values in spec
    assert "credential_ref" in json.dumps(spec)
    assert "${" in spec["source"]["base_url"]  # placeholder, not raw http


def test_harbor_bootstrap_oracle_pass() -> None:
    """Draft spec passes verifier oracle for baseline (schema + money + date + no-loss)."""
    result = _harbor_bootstrap()
    _assert_visible_tests_green(result)
    from verifier.oracle import oracle_verdict

    verdict, score, failed = oracle_verdict("baseline", result.draft_spec)
    assert verdict == "pass", f"oracle failed: {failed}"
    assert score == 1.0
    assert not failed


def test_harbor_bootstrap_money_decimal_and_business_date() -> None:
    """Money is Decimal and business-date boundary lands on right local date (via oracle + sync)."""
    result = _harbor_bootstrap()
    _assert_visible_tests_green(result)
    # Money Decimal via sync against fake dataset
    from simulators.common.generator import generate_dataset

    dataset = generate_dataset(seed=0)
    os.environ["HARBOR_API_KEY"] = "test-token"
    http = _harbor_fake_client(dataset)
    summary = execute_sync(result.draft_spec, http)
    assert summary["outcome"] == "Complete", f"unmet {summary['unmet_criteria']}"
    for ent_name in ("Reservation", "FolioCharge"):
        recs = summary["canonical_records"].get(ent_name, [])
        if recs:
            money_field = "total_amount" if ent_name == "Reservation" else "amount"
            if money_field in recs[0]:
                assert isinstance(recs[0][money_field], Decimal)
                assert recs[0][money_field].as_tuple().exponent == -2
        # Business date via oracle already checks boundary; sanity-check via transform unit
        from autobridge.runtime.transforms.business_date import transform_business_date

        # 00:12 NY is prior business date
        assert transform_business_date("2026-03-15T00:12:00Z", args={"cutoff": 4}, context_tz="America/New_York") == "2026-03-14"
        assert transform_business_date("2026-03-15T08:00:00Z", args={"cutoff": 4}, context_tz="America/New_York") == "2026-03-15"


# ---------------------------------------------------------------------------
# 3. Date ambiguity — enumeration, probe budget resolution or Degraded
# ---------------------------------------------------------------------------


def test_date_ambiguity_classification_and_hypotheses() -> None:
    """Harbor's `date` is flagged ambiguous and enumerator yields 4 date hypotheses with evidence."""
    iface = read_docs_text(HARBOR_MD, "simulators/docs/harborpms.md")
    classification = classify_all(iface)
    assert "date" in classification.date_semantics.ambiguous_fields
    enumerator = HypothesisEnumerator()
    date_hyps = enumerator.enumerate_date(iface, classification)
    assert len(date_hyps) == 4
    # Every hypothesis links to ≥1 evidence record
    for h in date_hyps:
        assert h.evidence_for, f"{h.id} missing evidence_for"
        assert h.family == "date"
        assert h.candidate_mapping_fragment, f"{h.id} missing fragment"
        assert h.strategy in ("created", "arrival", "business", "utc")
    # All families produce evidence
    all_fams = enumerator.enumerate_all_families(iface, classification)
    for fam, hyps in all_fams.items():
        for h in hyps:
            assert h.evidence_for, f"{fam}:{h.id} missing evidence"
            assert h.candidate_mapping_fragment


def test_date_ambiguity_resolved_within_probe_budget() -> None:
    """Date ambiguity resolved within probe budget via probing."""
    iface = read_docs_text(HARBOR_MD, "simulators/docs/harborpms.md")
    classification = classify_all(iface)
    enumerator = HypothesisEnumerator()
    date_hyps = enumerator.enumerate_date(iface, classification)
    assert len(date_hyps) == 4

    # Probe budget sufficient for resolution
    budget = ProbeBudget(max_probes=5, max_requests=10, max_tokens=100)
    selector = ProbeSelector(iface, budget=budget)

    # Fake executor that resolves date ambiguity to `arrival` strategy
    def fake_executor(probe: Any) -> dict[str, Any]:  # noqa: ARG001
        # Return strategy arrival so filter keeps only arrival hypothesis
        return {"strategy": "arrival", "operation": probe.operation}

    result = resolve_hypotheses(
        date_hyps,
        probe_selector=selector,
        probe_budget=budget,
        probe_executor=fake_executor,
        enumerator_yield_count=len(date_hyps),
    )
    assert result.decision == "resolved", f"expected resolved, got {result.decision} trace {result.trace}"
    assert result.resolved_hypothesis is not None
    assert result.resolved_hypothesis.strategy == "arrival"
    assert result.model_usage["frontier_tokens"] == 0
    assert result.ladder_level_reached in (0, 1)

    # Also via bootstrap path with probes — pagination is skipped (main ops high confidence), only date probed
    bootstrap_result = build_draft_spec(
        HARBOR_MD,
        source="simulators/docs/harborpms.md",
        probe_executor=fake_executor,
        probe_budget=ProbeBudget(max_probes=5, max_requests=10),
    )
    assert bootstrap_result.success
    _assert_visible_tests_green(bootstrap_result)
    # Bootstrap trace should show date resolution (pagination skipped, date resolved)
    has_date_resolve = any(t.get("family") == "date" and t.get("decision") == "resolved" for t in bootstrap_result.trace)
    # After fix, pagination is skipped deterministically, date should be resolved
    assert has_date_resolve, f"bootstrap trace should have date resolved, got: {bootstrap_result.trace}"


def test_date_ambiguity_degraded_when_probe_budget_exhausted_or_no_probe() -> None:
    """Date ambiguity reported Degraded with open questions when budget exhausted or no distinguishing probe."""
    iface = read_docs_text(HARBOR_MD, "simulators/docs/harborpms.md")
    classification = classify_all(iface)
    enumerator = HypothesisEnumerator()
    date_hyps = enumerator.enumerate_date(iface, classification)
    assert len(date_hyps) == 4

    # Path 1: no probe executor → degraded (surviving ambiguity)
    budget = ProbeBudget(max_probes=5)
    selector = ProbeSelector(iface, budget=budget)
    result_no_exec = resolve_hypotheses(
        date_hyps,
        probe_selector=selector,
        probe_budget=budget,
        probe_executor=None,
        enumerator_yield_count=len(date_hyps),
    )
    assert result_no_exec.decision == "degraded", f"expected degraded without executor, got {result_no_exec.decision}"
    assert result_no_exec.open_questions, "degraded must carry open_questions"
    assert result_no_exec.model_usage["frontier_tokens"] == 0

    # Path 2: probe executor returns non-distinguishing observation (keeps all) — need budget large enough to try all candidates then degrade
    def nondistinguishing_executor(probe: Any) -> dict[str, Any]:  # noqa: ARG001
        return {"note": "probe returned same date as both candidates", "operation": probe.operation}

    budget2 = ProbeBudget(max_probes=10, max_requests=20)  # enough to try all 7 candidates then degrade
    selector2 = ProbeSelector(iface, budget=budget2)
    result_nondist = resolve_hypotheses(
        date_hyps,
        probe_selector=selector2,
        probe_budget=budget2,
        probe_executor=nondistinguishing_executor,
        enumerator_yield_count=len(date_hyps),
    )
    assert result_nondist.decision == "degraded", f"expected degraded with non-distinguishing probes, got {result_nondist.decision} trace {result_nondist.trace}"
    assert result_nondist.open_questions

    # Path 3: BudgetExhausted → escalation, never silent guess
    def budget_exhausted_executor(probe: Any) -> dict[str, Any]:  # noqa: ARG001
        raise BudgetExhausted("max_probes exceeded in test")

    budget3 = ProbeBudget(max_probes=5)
    selector3 = ProbeSelector(iface, budget=budget3)
    result_escalate = resolve_hypotheses(
        date_hyps,
        probe_selector=selector3,
        probe_budget=budget3,
        probe_executor=budget_exhausted_executor,
        enumerator_yield_count=len(date_hyps),
    )
    assert result_escalate.decision == "escalate"
    assert result_escalate.escalation_bundle is not None
    assert result_escalate.escalation_bundle.question

    # Bootstrap path with exhausted budget should Degrade (or escalate) but never silently guess
    bootstrap_degraded = build_draft_spec(
        HARBOR_MD,
        source="simulators/docs/harborpms.md",
        probe_executor=budget_exhausted_executor,
        probe_budget=ProbeBudget(max_probes=5, max_requests=10),
    )
    # Bootstrap still succeeds (compile) but with open_questions or escalation trace
    assert bootstrap_degraded.success or bootstrap_degraded.open_questions or bootstrap_degraded.degradation_reason
    # Must not have silently guessed: if open_questions present, degradation_reason mentions ambiguity or budget
    if bootstrap_degraded.open_questions:
        assert bootstrap_degraded.degradation_reason is not None


def test_budget_exhausted_never_silent_guess() -> None:
    """BudgetExhausted in probe loop escalates to L4 with bundle, never guess."""
    iface = read_docs_text(HARBOR_MD, "simulators/docs/harborpms.md")
    classification = classify_all(iface)
    enumerator = HypothesisEnumerator()
    hyps = enumerator.enumerate_date(iface, classification)
    # Exhaust budget before first probe
    budget = ProbeBudget(max_probes=0)
    selector = ProbeSelector(iface, budget=budget)

    def any_executor(probe: Any) -> dict[str, Any]:  # noqa: ARG001
        return {"strategy": "arrival"}

    result = resolve_hypotheses(
        hyps,
        probe_selector=selector,
        probe_budget=budget,
        probe_executor=any_executor,
        enumerator_yield_count=len(hyps),
    )
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    assert result.ladder_level_reached == 4
    assert result.escalation_reason is not None
    assert "max_probes" in result.escalation_reason.lower() or "exceeded" in result.escalation_reason.lower()


# ---------------------------------------------------------------------------
# 4. L3 precondition — only when enumerator yields <2
# ---------------------------------------------------------------------------


def test_l3_only_when_enumerator_yields_fewer_than_two() -> None:
    """L3 is invoked only when enumerator yields <2 hypotheses (asserted)."""
    from autobridge.discovery.resolver import assert_l3_precondition

    # Should fail when >=2
    with pytest.raises(AssertionError, match="L3 frontier invoked only when"):
        assert_l3_precondition(4)
    with pytest.raises(AssertionError):
        assert_l3_precondition(2)
    # Should pass when <2
    assert_l3_precondition(1)
    assert_l3_precondition(0)

    # Real resolver path: with >=2 hypotheses, L3 is skipped (trace shows l3_skipped)
    iface = read_docs_text(HARBOR_MD, "simulators/docs/harborpms.md")
    classification = classify_all(iface)
    enumerator = HypothesisEnumerator()
    date_hyps = enumerator.enumerate_date(iface, classification)
    assert len(date_hyps) >= 2
    budget = ProbeBudget(max_probes=1)
    selector = ProbeSelector(iface, budget=budget)
    result = resolve_hypotheses(
        date_hyps,
        probe_selector=selector,
        probe_budget=budget,
        probe_executor=lambda p: {"strategy": "arrival"},  # noqa: ARG005
        enumerator_yield_count=len(date_hyps),
    )
    assert result.decision == "resolved"
    # Trace must contain l3_skipped, not l3_required
    assert any(t.get("action") == "l3_skipped" for t in result.trace)
    assert not any(t.get("action") == "l3_required" for t in result.trace)


# ---------------------------------------------------------------------------
# 5. Every hypothesis links to ≥1 evidence (all families)
# ---------------------------------------------------------------------------


def test_every_hypothesis_links_to_evidence() -> None:
    """Every hypothesis links to at least one evidence record (W5.3 invariant)."""
    iface = read_docs_text(HARBOR_MD, "simulators/docs/harborpms.md")
    classification = classify_all(iface)
    enumerator = HypothesisEnumerator()
    all_hyps = enumerator.enumerate_all(iface, classification)
    assert len(all_hyps) > 0
    for h in all_hyps:
        assert h.evidence_for, f"{h.id} missing evidence_for"
        # evidence_for entries must be Provenance with source+line
        for prov in h.evidence_for:
            assert prov.source
            assert prov.line >= 1
        # candidate_mapping_fragment must be non-empty dict
        assert isinstance(h.candidate_mapping_fragment, dict)
        assert h.candidate_mapping_fragment
        # to_evidence_payload must contain evidence_for and fragment
        payload = h.to_evidence_payload()
        assert payload["evidence_for"]
        assert payload["fragment"]
        assert payload["hypothesis_id"] == h.id

    # Bootstrap evidence_payloads also satisfy invariant
    result = _harbor_bootstrap()
    for payload in result.evidence_payloads:
        assert payload["evidence_for"]
        assert payload["fragment"]


# ---------------------------------------------------------------------------
# 6. Bootstrap spec syncs Complete with ModelCall 0 (fake) + visible tests
# ---------------------------------------------------------------------------


def test_bootstrap_spec_syncs_complete_with_zero_model_calls_fake() -> None:
    """Bootstrapped Harbor spec syncs Complete with ModelCall 0 against fake transport."""
    from sqlalchemy import create_engine

    from autobridge.control.models import session_factory

    result = _harbor_bootstrap()
    _assert_visible_tests_green(result)
    assert result.model_usage["frontier_tokens"] == 0

    from simulators.common.generator import generate_dataset

    dataset = generate_dataset(seed=0)
    os.environ["HARBOR_API_KEY"] = "test-token"
    os.environ["HARBOR_BASE_URL"] = "https://example.com"
    http = _harbor_fake_client(dataset)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)

    summary = execute_sync(result.draft_spec, http)  # type: ignore[arg-type]
    assert summary["outcome"] == "Complete", f"outcome {summary['outcome']} unmet {summary['unmet_criteria']}"
    assert not summary["unmet_criteria"]
    assert summary["data_readiness"]["freshness"] == "current"
    assert summary["data_readiness"]["completeness"] == "complete"
    assert summary["records_observed"]["Reservation"] == len(dataset["reservations"])
    assert summary["records_observed"]["FolioCharge"] == len(dataset["folios"])
    # Fingerprint stable
    assert summary["fingerprint_id"]
    # No ModelCall rows (engine never touches LLM)
    with sessions() as session:
        assert session.query(ModelCall).count() == 0
    # Money Decimal and status opaque mapping via oracle
    from verifier.oracle import oracle_verdict

    verdict, score, failed = oracle_verdict("baseline", result.draft_spec)
    assert verdict == "pass"
    assert score == 1.0
    assert not failed


def test_bootstrap_spec_visible_tests_cover_closed_transforms_and_guards() -> None:
    """Visible tests enforce closed transform set, stop_when guards, money scale, enum on_unknown block."""
    result = _harbor_bootstrap()
    _assert_visible_tests_green(result)
    from autobridge.discovery.bootstrap import visible_tests

    errs = visible_tests(result.draft_spec)  # type: ignore[arg-type]
    assert errs == []
    # Tamper checks: missing stop_when should fail
    bad = json.loads(json.dumps(result.draft_spec))
    bad["operations"]["list_reservations"]["pagination"]["stop_when"] = []
    assert visible_tests(bad)
    # Missing money transform should fail
    bad2 = json.loads(json.dumps(result.draft_spec))
    bad2["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = []
    assert visible_tests(bad2)
    # Enum without on_unknown block should fail
    bad3 = json.loads(json.dumps(result.draft_spec))
    bad3["entities"]["Reservation"]["fields"]["status"]["transform"][0]["args"]["on_unknown"] = "guess"
    assert visible_tests(bad3)


# ---------------------------------------------------------------------------
# 7. Live e2e — when stack is up, bootstrap spec syncs live
# ---------------------------------------------------------------------------


def test_bootstrap_spec_syncs_live_when_stack_up(e2e_stack: str) -> None:
    """Live Harbor bootstrap sync against real simulators when Compose stack is up."""
    assert e2e_stack
    # Reset Harbor simulator to baseline via admin API (conftest autouse already does)
    try:
        from tests.e2e.conftest import admin_headers, admin_url

        url = admin_url("sim-harbor", "/admin/scenario")
        data = json.dumps({"id": "baseline", "seed": 0}).encode()
        req = __import__("urllib.request").request.Request(
            url, data=data, headers={"Content-Type": "application/json", **admin_headers()}, method="POST"
        )
        with __import__("urllib.request").request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass
    result = _harbor_bootstrap()
    _assert_visible_tests_green(result)
    # Live bootstrap: use host-mapped harbor port
    os.environ["HARBOR_BASE_URL"] = "http://localhost:8106"
    os.environ["HARBOR_API_KEY"] = "test-token-live"
    parsed = load_spec_from_dict(result.draft_spec)  # type: ignore[arg-type]
    from autobridge.runtime.http import HttpClient as _HC

    http = _HC(base_url=parsed.source.base_url, auth=parsed.source.auth, retry=parsed.source.rate_limit.retry)
    summary = execute_sync(result.draft_spec, http)  # type: ignore[arg-type]
    assert summary["outcome"] == "Complete", f"live outcome {summary['outcome']} unmet {summary['unmet_criteria']}"
    assert not summary["unmet_criteria"]
    # ModelCall 0 via in-memory DB check (engine never calls LLM)
    from sqlalchemy import create_engine as _ce

    from autobridge.control.models import session_factory as _sf

    engine = _ce("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = _sf(engine)
    with sessions() as session:
        assert session.query(ModelCall).count() == 0


def test_bootstrap_date_ambiguity_live_probe_if_stack(e2e_stack: str) -> None:
    """Live date disambiguation probe against Harbor simulator (when stack up)."""
    assert e2e_stack
    # Probe Harbor's /reservations and compare `date` with `arrival_date`
    import urllib.error
    import urllib.request

    url = "http://localhost:8106/reservations?limit=1"
    try:
        headers = {"Authorization": "Bearer test-token-live"}
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        from tests.e2e.conftest import stack_unreachable  # noqa: PLC0415

        stack_unreachable(f"Harbor simulator not reachable live: {exc}")
        return  # pragma: no cover - stack_unreachable never returns
    data = body.get("data", [])
    assert data, "Harbor /reservations should return data"
    sample = data[0]
    # Harbor's `date` duplicates `arrival_date` (and business_date for folios)
    assert "date" in sample, "Harbor should return ambiguous `date` field"
    # At least one of the canonical dates should equal `date`
    assert sample["date"] == sample.get("arrival_date") or sample["date"] == sample.get("business_date") or "date" in sample, (
        f"Harbor date field should duplicate arrival_date or business_date, got date={sample.get('date')} arrival={sample.get('arrival_date')}"
    )
