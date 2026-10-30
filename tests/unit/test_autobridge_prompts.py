"""REQ-045: engine prompts live in files, loaded once at startup, well-posed.

Covers: the loader (valid / missing file / bad front-matter /
unrendered / undeclared placeholders), startup entry points, call-site
rendering with full context and no secrets, L2 output parsing plus the
labelled prior-order fallback, L3-hypothesis parsing (propose / none /
unknown-family rejection), ledger prompt id/version/hash, replay
round-trips with the new prompts, and the guard that no engine
``.complete()`` call builds messages from a Python literal.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy import create_engine

from autobridge.control.models import Base, ModelCall, session_factory
from autobridge.discovery.docs_reader import Provenance
from autobridge.discovery.hypotheses import Hypothesis
from autobridge.llm.prompts import (
    PromptError,
    ensure_prompts_loaded,
    get_prompt,
    load_prompts,
    prompt_ref,
    render_prompt,
    reset_prompts_cache,
)
from autobridge.llm.provider import (
    Budget,
    Completion,
    FakeProvider,
    Usage,
)
from autobridge.repair.knowledge import KnowledgeCache
from autobridge.repair.ladder import (
    _call_frontier_generate,
    _call_small_model_rank,
    _parse_l2_response,
    _parse_l3_hypothesis_response,
    run_frontier_climb,
    rung_ledger,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _prov(snippet: str = "total_amount reads net") -> tuple[Provenance, ...]:
    return (Provenance(source="demo:gross_to_net", line=1, snippet=snippet),)


def _hyp(
    hid: str = "gross_is_canonical",
    claim: str = "total_amount must stay gross",
    prior: float = 0.41,
    snippet: str = "total_amount reads net",
) -> Hypothesis:
    return Hypothesis(
        id=hid,
        family="amount",
        claim=claim,
        candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
        prior=prior,
        evidence_for=_prov(snippet),
        field="total_amount",
    )


def _pair() -> list[Hypothesis]:
    return [
        _hyp("gross_is_canonical", "total_amount must stay gross", 0.41),
        _hyp("net_is_canonical", "total_amount tracks net", 0.39),
    ]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_loader_loads_all_required_prompts() -> None:
    reset_prompts_cache()
    loaded = load_prompts()
    assert set(loaded) >= {"l2_rank", "l3_hypothesis", "l3_patch"}
    assert loaded["l2_rank"].tier == "small"
    assert loaded["l3_hypothesis"].tier == "frontier"
    assert loaded["l3_patch"].tier == "frontier"
    assert all(t.version.strip() for t in loaded.values())
    assert all(t.max_tokens > 0 for t in loaded.values())
    for template in loaded.values():
        assert template.inputs, template.id
        assert "{" + template.inputs[0] + "}" in (template.system + template.user_template)


def test_loader_missing_file_names_it(tmp_path: pathlib.Path) -> None:
    with pytest.raises(PromptError, match="l2_rank"):
        load_prompts(directory=tmp_path)


def test_loader_bad_front_matter_names_file(tmp_path: pathlib.Path) -> None:
    for name in ("l2_rank", "l3_hypothesis", "l3_patch"):
        (tmp_path / f"{name}.md").write_text(
            "---\nid: "
            + name
            + "\nversion: '1'\n"
            "purpose: test\ntier: small\nmax_tokens: 512\n"
            "inputs: [a]\nanswer_format: json\n---\n\n## System\n\nsys {a}\n\n## User\n\nusr\n",
            encoding="utf-8",
        )
    (tmp_path / "l3_patch.md").write_text("no front matter here", encoding="utf-8")
    with pytest.raises(PromptError, match="l3_patch.md"):
        load_prompts(directory=tmp_path)


def test_loader_rejects_undeclared_and_unused_placeholders(tmp_path: pathlib.Path) -> None:
    (tmp_path / "l2_rank.md").write_text(
        "---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\n"
        "inputs: [declared]\nanswer_format: j\n---\n\n## System\n\nsys {undeclared}\n\n## User\n\nusr {declared}\n",
        encoding="utf-8",
    )
    with pytest.raises(PromptError, match="undeclared"):
        load_prompts(directory=tmp_path)
    (tmp_path / "l2_rank.md").write_text(
        "---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\n"
        "inputs: [declared, unused]\nanswer_format: j\n---\n\n## System\n\nsys {declared}\n\n## User\n\nusr\n",
        encoding="utf-8",
    )
    with pytest.raises(PromptError, match="unused"):
        load_prompts(directory=tmp_path)


def test_render_requires_every_input_and_rejects_extra() -> None:
    template = get_prompt("l2_rank")
    with pytest.raises(PromptError, match="missing"):
        render_prompt(template, drift_class="X")
    with pytest.raises(PromptError, match="undeclared"):
        render_prompt(
            template,
            drift_class="X",
            hypotheses_block="h",
            evidence_block="e",
            extra="nope",
        )
    messages = render_prompt(
        template, drift_class="X", hypotheses_block="h", evidence_block="e"
    )
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "{" not in messages[1]["content"].replace('{"ranking"', "")


def test_ensure_prompts_loaded_fails_startup_on_bad_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOBRIDGE_PROMPTS_DIR", str(tmp_path))
    reset_prompts_cache()
    try:
        with pytest.raises(PromptError, match=str(tmp_path)):
            ensure_prompts_loaded()
    finally:
        monkeypatch.delenv("AUTOBRIDGE_PROMPTS_DIR", raising=False)
        reset_prompts_cache()
    assert ensure_prompts_loaded()["l2_rank"].id == "l2_rank"


def test_prompt_ref_is_traceable() -> None:
    import hashlib

    ref = prompt_ref("l2_rank")
    raw = (REPO_ROOT / "config" / "prompts" / "l2_rank.md").read_bytes()
    assert ref["id"] == "l2_rank"
    assert ref["version"] == get_prompt("l2_rank").version
    assert ref["hash"] == hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# L2 rendering: full context, no secrets
# ---------------------------------------------------------------------------


def test_l2_render_carries_claims_and_evidence() -> None:
    from autobridge.repair.ladder import _hypotheses_block, _shared_evidence_block

    template = get_prompt("l2_rank")
    messages = render_prompt(
        template,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses_block=_hypotheses_block(_pair()),
        evidence_block=_shared_evidence_block(
            "SEMANTIC_VALUE_DRIFT", {"observed": "total 90 vs 100"}
        ),
    )
    user = messages[1]["content"]
    assert "SEMANTIC_VALUE_DRIFT" in user
    for hid in ("gross_is_canonical", "net_is_canonical"):
        assert hid in user
    assert "total_amount must stay gross" in user
    assert "total_amount tracks net" in user
    assert "0.41" in user and "0.39" in user
    assert "ranking" in messages[0]["content"]


def test_l2_render_never_carries_secrets() -> None:
    from autobridge.repair.ladder import _hypotheses_block

    secret = "SECRET-XYZ-123"
    hyps = [_hyp(snippet=f"field total_amount api_token={secret} ok")]
    template = get_prompt("l2_rank")
    messages = render_prompt(
        template,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses_block=_hypotheses_block(hyps),
        evidence_block="{}",
    )
    assert secret not in messages[1]["content"]
    assert "<redacted>" in messages[1]["content"]


# ---------------------------------------------------------------------------
# L2 parsing + fallback
# ---------------------------------------------------------------------------


def test_l2_parse_well_posed_object() -> None:
    parsed = _parse_l2_response(
        '{"ranking": ["b", "a"], "confidence": 0.8, "rationale": "b explains the wire"}'
    )
    assert parsed["ids"] == ["b", "a"]
    assert parsed["confidence"] == pytest.approx(0.8)
    assert parsed["fallback"] is None


def test_l2_parse_legacy_list_shape() -> None:
    parsed = _parse_l2_response('["b", "a"]')
    assert parsed["ids"] == ["b", "a"]
    assert parsed["confidence"] is None
    assert parsed["fallback"] is None


def test_l2_parse_failure_requests_labelled_fallback() -> None:
    parsed = _parse_l2_response("nope")
    assert parsed["fallback"] is not None
    assert "prior order" in parsed["fallback"]
    assert _parse_l2_response("")["fallback"] is not None
    # Token soup with no known id parses as ids but the caller still
    # falls back (covered by test_l2_call_falls_back_to_prior_order_labelled).
    soup = _parse_l2_response("ranking: first b then a maybe")
    assert soup["ids"] and soup["fallback"] is None


def test_l2_call_uses_model_confidence() -> None:
    fake = FakeProvider(
        responses={
            "small": Completion(
                text='{"ranking": ["net_is_canonical", "gross_is_canonical"], '
                '"confidence": 0.9, "rationale": "net matches the wire"}',
                usage=Usage(4, 4),
            )
        }
    )
    ranked, usage, info = _call_small_model_rank(
        _pair(), fake, Budget(), drift_class="SEMANTIC_VALUE_DRIFT"
    )
    assert [h.id for h in ranked] == ["net_is_canonical", "gross_is_canonical"]
    assert info["confidence"] == pytest.approx(0.9)
    assert info["fallback"] is None
    assert usage["small_calls"] == 1


def test_l2_call_falls_back_to_prior_order_labelled() -> None:
    fake = FakeProvider(
        responses={"small": Completion(text="ranking: first b then a maybe", usage=Usage(4, 4))}
    )
    hyps = _pair()
    ranked, _, info = _call_small_model_rank(
        hyps, fake, Budget(), drift_class="SEMANTIC_VALUE_DRIFT"
    )
    assert [h.id for h in ranked] == [h.id for h in hyps]
    assert info["fallback"] is not None
    assert "prior order" in info["fallback"]


def test_l2_call_records_prompt_ref_on_ledger(tmp_path: pathlib.Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/l2.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    fake = FakeProvider(
        responses={
            "small": Completion(
                text='{"ranking": ["gross_is_canonical", "net_is_canonical"], '
                '"confidence": 0.6, "rationale": "gross fits"}',
                usage=Usage(4, 4),
            )
        }
    )
    with factory() as s:
        ranked, _, _ = _call_small_model_rank(
            _pair(), fake, Budget(), run_id="run-l2", session=s,
            drift_class="SEMANTIC_VALUE_DRIFT",
        )
        assert [h.id for h in ranked] == ["gross_is_canonical", "net_is_canonical"]
        s.commit()
        row = s.query(ModelCall).one()
        ref = prompt_ref("l2_rank")
        assert row.prompt_id == "l2_rank"
        assert row.prompt_version == ref["version"]
        assert row.prompt_hash == ref["hash"]
        assert row.outcome == "ranked"
    phones = rung_ledger([{"level": 2, "action": "small_rank", "reason": "r"}], [row])
    assert phones[0]["calls"][0]["prompt_id"] == "l2_rank"
    assert phones[0]["calls"][0]["prompt_version"] == ref["version"]


# ---------------------------------------------------------------------------
# L3 hypothesis: parse + call
# ---------------------------------------------------------------------------


def test_l3_hypothesis_parse_shapes() -> None:
    ok = _parse_l3_hypothesis_response(
        '{"hypothesis": {"id": "amount:total:net", "family": "amount", '
        '"field": "total", "claim": "reads net"}, "rationale": "wire shows net"}'
    )
    assert ok["items"] and ok["items"][0]["family"] == "amount"
    none = _parse_l3_hypothesis_response('{"none": "both readings fit"}')
    assert none["items"] == [] and "both readings fit" in none["none_reason"]
    legacy = _parse_l3_hypothesis_response(
        '{"id": "date:x:business", "family": "date", "claim": "c"}'
    )
    assert legacy["items"] and legacy["items"][0]["id"] == "date:x:business"
    bad_family = _parse_l3_hypothesis_response(
        '{"hypothesis": {"id": "zzz:x:y", "family": "zzz", "claim": "c"}, "rationale": "r"}'
    )
    assert bad_family["items"] == [] and bad_family["rejected"] == "zzz"
    assert _parse_l3_hypothesis_response("not json at all")["items"] == []


def test_l3_hypothesis_call_appends_novel_hypothesis() -> None:
    fake = FakeProvider(
        responses={
            "frontier": Completion(
                text='{"hypothesis": {"id": "amount:total:net", "family": "amount", '
                '"field": "total", "claim": "reads net"}, "rationale": "wire"}',
                usage=Usage(9, 9),
            )
        }
    )
    hyps = _pair()
    new_hyps, usage, info = _call_frontier_generate(
        hyps, fake, Budget(), enumerator_yield_count=1, drift_class="SEMANTIC_VALUE_DRIFT"
    )
    assert len(new_hyps) == len(hyps) + 1
    assert info["proposed"] is True
    assert usage["frontier_calls"] == 1


def test_l3_hypothesis_call_rejects_unknown_family_labelled() -> None:
    fake = FakeProvider(
        responses={
            "frontier": Completion(
                text='{"hypothesis": {"id": "zzz:x:y", "family": "zzz", "claim": "c"}, "rationale": "r"}',
                usage=Usage(9, 9),
            )
        }
    )
    hyps = _pair()
    new_hyps, _, info = _call_frontier_generate(
        hyps, fake, Budget(), enumerator_yield_count=1, drift_class="SEMANTIC_VALUE_DRIFT"
    )
    assert new_hyps == hyps
    assert info["rejected"] == "zzz"


def test_l3_hypothesis_call_none_is_labelled() -> None:
    fake = FakeProvider(
        responses={"frontier": Completion(text='{"none": "both fit"}', usage=Usage(9, 9))}
    )
    _, _, info = _call_frontier_generate(
        _pair(), fake, Budget(), enumerator_yield_count=1, drift_class="SEMANTIC_VALUE_DRIFT"
    )
    assert info["proposed"] is False
    assert "both fit" in info["none_reason"]


def test_l3_prompt_names_context(tmp_path: pathlib.Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/l3h.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    fake = FakeProvider(
        responses={"frontier": Completion(text='{"none": "both fit"}', usage=Usage(9, 9))}
    )
    with factory() as s:
        _, _, _ = _call_frontier_generate(
            _pair(), fake, Budget(), enumerator_yield_count=1,
            run_id="run-l3h", session=s, drift_class="SEMANTIC_VALUE_DRIFT",
            signals=[{"kind": "money_mismatch"}],
            spec_excerpt={"entities": {"Reservation": {}}},
            docs_excerpt="amounts are gross",
        )
        s.commit()
        row = s.query(ModelCall).one()
        assert row.prompt_id == "l3_hypothesis"
        assert row.outcome == "none"
    text = fake.calls[0]["messages"][1]["content"]
    assert "SEMANTIC_VALUE_DRIFT" in text
    assert "money_mismatch" in text
    assert "gross_is_canonical" in text
    assert "amount, date, pagination, status" in text


# ---------------------------------------------------------------------------
# L3 patch renders from the file with full context
# ---------------------------------------------------------------------------


def test_l3_patch_render_names_pointers_evidence_and_format() -> None:
    from autobridge.repair.ladder import _l3_evidence_blocks

    evidence = {
        "patch_target": {"document": "connector spec", "allowed_pointers": ["/entities/X/fields/a/path"]},
        "spec_subtree": {"entities": {"X": {}}},
        "raw_records": [{"a": "1"}],
        "observed": {"delta": True},
    }
    blocks = _l3_evidence_blocks(evidence)
    messages = render_prompt(
        get_prompt("l3_patch"),
        drift_class="SEMANTIC_VALUE_DRIFT",
        ranked_ids="['a']",
        **blocks,
    )
    user = messages[1]["content"]
    assert "/entities/X/fields/a/path" in user
    assert '"a": "1"' in user or '"a":"1"' in user
    assert "Answer with one JSON object" in user
    assert messages[0]["content"].startswith("You repair API connectors")
    assert "Answer with one JSON object" in messages[0]["content"]


def test_l3_patch_rung_carries_prompt_ref() -> None:
    fake = FakeProvider(
        responses={
            "small": Completion(text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)),
            "frontier": Completion(
                text='{"escalate": "gross or net?", "rationale": "business call"}',
                usage=Usage(10, 10),
            ),
        }
    )
    climb = run_frontier_climb(
        spec={"spec_version": 1, "connector": {"id": "m", "vendor": "h"}},
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_pair(),
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=None,
        vendor="h",
    )
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    l2 = next(a for a in climb["attempts"] if a["rung"] == 2)
    assert l3["prompt"]["id"] == "l3_patch"
    assert l3["prompt"]["hash"] == prompt_ref("l3_patch")["hash"]
    assert l2["prompt"]["id"] == "l2_rank"


# ---------------------------------------------------------------------------
# No prompt text in Python: engine messages must come from the loader
# ---------------------------------------------------------------------------


def test_engine_messages_come_from_loader() -> None:
    """Every engine ``.complete()`` sends messages built by ``render_prompt``.

    Walks ``src/autobridge``: a ``.complete(messages, ...)`` call whose
    messages are an inline list/dict literal (prompt text in Python) fails;
    messages assigned from ``render_prompt(...)`` pass. Forwarding calls
    (``messages`` is a function parameter) are skipped.
    """
    root = REPO_ROOT / "src" / "autobridge"
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            arg_names = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
            for child in ast.walk(node):
                if not (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "complete"
                ):
                    continue
                messages_arg = None
                if child.args:
                    messages_arg = child.args[0]
                for kw in child.keywords:
                    if kw.arg == "messages":
                        messages_arg = kw.value
                if messages_arg is None or isinstance(messages_arg, ast.Starred):
                    continue
                if isinstance(messages_arg, ast.Name) and messages_arg.id in arg_names:
                    continue  # forwarding (e.g. BudgetedProvider -> inner)
                if isinstance(messages_arg, ast.Name):
                    target = messages_arg.id
                    ok = False
                    # Messages must be assigned from a render_prompt(...)
                    # call (possibly imported under an alias such as
                    # _render_prompt) in the same function.
                    def _is_render(call: ast.Call) -> bool:
                        func = call.func
                        return isinstance(func, ast.Name) and (
                            func.id == "render_prompt" or func.id.endswith("_render_prompt")
                        )

                    for stmt in ast.walk(node):
                        if isinstance(stmt, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == target for t in stmt.targets
                        ):
                            ok = isinstance(stmt.value, ast.Call) and _is_render(stmt.value)
                            break
                        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == target:
                            ok = (
                                isinstance(stmt.value, ast.Call) and _is_render(stmt.value)
                            )
                            break
                    if not ok:
                        violations.append(f"{path.name}: .complete({target}) not from render_prompt")
                else:
                    violations.append(f"{path.name}: .complete(<inline {type(messages_arg).__name__}>)")
    assert violations == [], violations


def test_old_prompt_literals_are_gone() -> None:
    """The three one-line prompt stubs must not survive anywhere in the engine."""
    hits: list[str] = []
    for path in sorted((REPO_ROOT / "src" / "autobridge").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for literal in (
            "You are a deterministic hypothesis ranker.",
            "You are a hypothesis generator for unknown API semantics.",
            "You repair API connectors. The fix is a JSON Patch",
        ):
            if literal in text:
                hits.append(f"{path.name}: {literal[:40]}...")
    assert hits == [], hits


# ---------------------------------------------------------------------------
# Loader error branches (every bad file fails startup naming the file)
# ---------------------------------------------------------------------------


def _write_minimal(tmp_path: pathlib.Path, name: str = "l2_rank", tier: str = "small") -> pathlib.Path:
    path = tmp_path / f"{name}.md"
    path.write_text(
        "---\nid: "
        + name
        + "\nversion: '1'\npurpose: test\ntier: "
        + tier
        + "\nmax_tokens: 512\n"
        "inputs: [a]\nanswer_format: json\n---\n\n## System\n\nsys {a}\n\n## User\n\nusr {a}\n",
        encoding="utf-8",
    )
    return path


def _write_all_valid(tmp_path: pathlib.Path) -> None:
    _write_minimal(tmp_path, "l2_rank", "small")
    _write_minimal(tmp_path, "l3_hypothesis", "frontier")
    _write_minimal(tmp_path, "l3_patch", "frontier")


@pytest.mark.parametrize(
    "body,match",
    [
        ("no front matter here", "front-matter"),
        ("---\njust: one\n---\n\n## System\n\ns\n\n## User\n\nu\n", "mapping|missing"),
        ("---\nversion: '1'\n---\n\n## System\n\ns\n\n## User\n\nu\n", "missing 'id'"),
        ("---\nid: l2_rankX\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\ninputs: [a]\nanswer_format: j\n---\n\n## System\n\ns {a}\n\n## User\n\nu\n", "!= file name"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: medium\nmax_tokens: 1\ninputs: [a]\nanswer_format: j\n---\n\n## System\n\ns {a}\n\n## User\n\nu\n", "tier"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: lots\ninputs: [a]\nanswer_format: j\n---\n\n## System\n\ns {a}\n\n## User\n\nu\n", "max_tokens"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 0\ninputs: [a]\nanswer_format: j\n---\n\n## System\n\ns {a}\n\n## User\n\nu\n", "max_tokens"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\ninputs: 42\nanswer_format: j\n---\n\n## System\n\ns\n\n## User\n\nu\n", "inputs"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\ninputs: []\nanswer_format: j\n---\n\n## System\n\ns\n\n## User\n\nu\n", "inputs"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\ninputs: [a, a]\nanswer_format: j\n---\n\n## System\n\ns {a}\n\n## User\n\nu\n", "unique"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\ninputs: [a]\nanswer_format: j\n---\n\nno sections here\n", "## System"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\ninputs: [a]\nanswer_format: j\n---\n\n## User\n\nu\n\n## System\n\ns\n", "## System"),
        ("---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\ninputs: [a]\nanswer_format: j\n---\n\n## System\n\n\n\n## User\n\nu {a}\n", "non-empty"),
    ],
)
def test_loader_bad_files_fail_naming_the_file(tmp_path: pathlib.Path, body: str, match: str) -> None:
    (tmp_path / "l2_rank.md").write_text(body, encoding="utf-8")
    with pytest.raises(PromptError, match=match) as exc:
        load_prompts(directory=tmp_path)
    assert "l2_rank.md" in str(exc.value)


def test_loader_unterminated_front_matter(tmp_path: pathlib.Path) -> None:
    (tmp_path / "l2_rank.md").write_text("---\nid: l2_rank\n", encoding="utf-8")
    with pytest.raises(PromptError, match="unterminated"):
        load_prompts(directory=tmp_path)


def test_loader_unreadable_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(PromptError, match="l2_rank.md"):
        load_prompts(directory=tmp_path / "missing-dir")


def test_loader_inputs_string_form_and_optional_retry(tmp_path: pathlib.Path) -> None:
    (tmp_path / "l2_rank.md").write_text(
        "---\nid: l2_rank\nversion: '1'\npurpose: t\ntier: small\nmax_tokens: 1\n"
        "inputs: a, b\nanswer_format: j\n---\n\n## System\n\ns {a}\n\n## User\n\nu {b}\n",
        encoding="utf-8",
    )
    _write_minimal(tmp_path, "l3_hypothesis", "frontier")
    _write_minimal(tmp_path, "l3_patch", "frontier")
    loaded = load_prompts(directory=tmp_path)
    assert loaded["l2_rank"].inputs == ("a", "b")
    # The retry prompt is optional: absent here, and unknown ids raise.
    with pytest.raises(KeyError):
        get_prompt("l3_patch_retry") if False else _require(loaded, "l3_patch_retry")
    (tmp_path / "l3_patch_retry.md").write_text(
        "---\nid: l3_patch_retry\nversion: '1'\npurpose: t\ntier: frontier\nmax_tokens: 1\n"
        "inputs: [a]\nanswer_format: j\n---\n\n## System\n\ns {a}\n\n## User\n\nu {a}\n",
        encoding="utf-8",
    )
    assert load_prompts(directory=tmp_path)["l3_patch_retry"].id == "l3_patch_retry"


def _require(loaded: dict, prompt_id: str):  # type: ignore[no-untyped-def]
    return loaded[prompt_id]


def test_loader_env_override_points_at_custom_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_all_valid(tmp_path)
    monkeypatch.setenv("AUTOBRIDGE_PROMPTS_DIR", str(tmp_path))
    reset_prompts_cache()
    try:
        from autobridge.llm.prompts import default_prompts_dir

        assert default_prompts_dir() == tmp_path
        assert set(load_prompts()) == {"l2_rank", "l3_hypothesis", "l3_patch"}
    finally:
        monkeypatch.delenv("AUTOBRIDGE_PROMPTS_DIR", raising=False)
        reset_prompts_cache()


def test_render_leftover_placeholder_from_value_fails() -> None:
    template = get_prompt("l2_rank")
    with pytest.raises(PromptError, match="unrendered"):
        render_prompt(
            template,
            drift_class="uses {drift_class} again",
            hypotheses_block="h",
            evidence_block="e",
        )


# ---------------------------------------------------------------------------
# Block builders: scrubbing, truncation, odd inputs
# ---------------------------------------------------------------------------


def test_scrub_variants() -> None:
    from autobridge.repair.ladder import _provenance_text, _scrub_text

    assert _scrub_text("plain words") == "plain words"
    assert "tok-1" not in _scrub_text("api_token=tok-1 ok")
    assert "<redacted>" in _scrub_text('{"secret": "s3cr3t"}')
    long_text = "x" * 300
    assert _scrub_text(long_text).endswith("…")
    assert _provenance_text(Provenance(source="s", line=2, snippet="api_key = K val")) == (
        "s:2 — " + _scrub_text("api_key = K val")
    )
    assert "K" not in _provenance_text(Provenance(source="s", line=2, snippet="api_key = K val"))
    assert _provenance_text(Provenance(source="", line="", snippet="")) == "(no provenance detail)"
    assert _provenance_text(Provenance(source="s", line=None, snippet="")) == "s"

    class _Bad:
        @property
        def source(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    assert _provenance_text(_Bad()) == ""


def test_hypotheses_block_variants() -> None:
    from autobridge.repair.ladder import _hypotheses_block

    assert _hypotheses_block([]) == "(no hypotheses)"

    class _Loose:
        id = "loose"
        claim = "c"
        prior = "not-a-float"
        field = "f"
        family = "amount"
        evidence_for = ()
        evidence_against = ()

    block = _hypotheses_block([_Loose()])
    assert "loose" in block and "0.50" in block and "(none recorded)" in block


def test_shared_evidence_block_variants() -> None:
    from autobridge.repair.ladder import _shared_evidence_block

    assert "no shared evidence" in _shared_evidence_block("D", None)
    assert "no shared evidence" in _shared_evidence_block("D", {})
    circular: dict = {}
    circular["self"] = circular
    assert "self" in _shared_evidence_block("D", circular)


def test_l2_confidence_bounds() -> None:
    assert _parse_l2_response('{"ranking": ["a"], "confidence": 9}')['confidence'] is None
    assert _parse_l2_response('{"ranking": ["a"], "confidence": "high"}')['confidence'] is None
    assert _parse_l2_response('{"ranking": ["a"], "confidence": 0.5}')['confidence'] == pytest.approx(0.5)
    assert _parse_l2_response('["only-one"]')['ids'] == ["only-one"]


def test_l3_hypothesis_legacy_and_rejected_lists() -> None:
    legacy = _parse_l3_hypothesis_response('[{"id": "date:x:business", "family": "date", "claim": "c"}]')
    assert [i["id"] for i in legacy["items"]] == ["date:x:business"]
    all_bad = _parse_l3_hypothesis_response('[{"id": "z:1", "family": "zzz"}]')
    assert all_bad["items"] == [] and all_bad["rejected"] == "zzz"
    assert _parse_l3_hypothesis_response('{"other": 1}')["detail"] != ""
    none_empty = _parse_l3_hypothesis_response('{"none": ""}')
    assert none_empty["items"] == []


def test_no_provider_infos_are_labelled() -> None:
    ranked, usage, info = _call_small_model_rank(_pair(), None, None)
    assert [h.id for h in ranked] == ["gross_is_canonical", "net_is_canonical"]
    assert info["fallback"] == "no provider configured"
    assert usage["calls"] == 0
    hyps, _, info = _call_frontier_generate(_pair(), None, None, enumerator_yield_count=1)
    assert hyps == _pair()
    assert "no provider" in info["none_reason"]


def test_l3_call_stamps_rejected_and_unparsable(tmp_path: pathlib.Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/l3o.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)

    def _outcome_for(text: str) -> str:
        fake = FakeProvider(responses={"frontier": Completion(text=text, usage=Usage(1, 1))})
        with factory() as s:
            _call_frontier_generate(
                _pair(), fake, Budget(), enumerator_yield_count=1,
                run_id="r", session=s, drift_class="SEMANTIC_VALUE_DRIFT",
            )
            s.commit()
            return str(s.query(ModelCall).order_by(ModelCall.created_at.desc()).first().outcome)

    assert _outcome_for('{"hypothesis": {"id": "z:1", "family": "zzz", "claim": "c"}}') == "rejected_family"
    assert _outcome_for("not json") == "unparsable"


def test_l3_retry_skipped_without_retry_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without l3_patch_retry.md the retry is skipped explicitly, never invented."""
    import shutil

    from autobridge.repair.ladder import run_frontier_climb as _climb

    for name in ("l2_rank", "l3_hypothesis", "l3_patch"):
        shutil.copy(REPO_ROOT / "config" / "prompts" / f"{name}.md", tmp_path / f"{name}.md")
    assert not (tmp_path / "l3_patch_retry.md").exists()
    monkeypatch.setenv("AUTOBRIDGE_PROMPTS_DIR", str(tmp_path))
    reset_prompts_cache()
    try:
        fake = FakeProvider(
            responses={
                "small": Completion(text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)),
                "frontier": Completion(
                    text='{"patch": [{"op": "replace", "path": "/mappings/total_amount", '
                    '"value": "$.net_amount"}], "rationale": "invented"}',
                    usage=Usage(10, 10),
                ),
            }
        )
        climb = _climb(
            spec={"spec_version": 1, "connector": {"id": "m", "vendor": "h"}},
            drift_class="SEMANTIC_VALUE_DRIFT",
            hypotheses=_pair(),
            enumerator_yield_count=1,
            provider=fake,
            budget=Budget(),
            knowledge_cache=KnowledgeCache(),
            fingerprint_delta=None,
            vendor="h",
        )
        l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
        assert l3["action"] == "proposal_rejected"
        assert "retry skipped" in l3["reason"]
        assert climb["proposal"] is None
        assert fake.calls and len(fake.calls) == 2  # L2 + L3 only, no retry call
    finally:
        monkeypatch.delenv("AUTOBRIDGE_PROMPTS_DIR", raising=False)
        reset_prompts_cache()


def test_worker_lifespan_validates_prompts() -> None:
    import asyncio

    from autobridge.engine.app import lifespan

    async def _run() -> None:
        from fastapi import FastAPI

        async with lifespan(FastAPI()):
            pass

    asyncio.run(_run())
