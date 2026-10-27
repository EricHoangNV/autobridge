"""EvidenceStore + redact tests (P1.W1.7, DIRECTIVES §6).

The store is the append-only evidence chain (plan §2.3 ``EvidenceRecord``);
``redact`` guarantees credential-ref values never reach a stored payload,
a fixture prompt, or an API response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import create_engine

from autobridge.control import models as m
from autobridge.control.models import EVIDENCE_KINDS
from autobridge.evidence import (
    EvidenceStore,
    UnknownKindError,
    UnknownParentError,
    redact,
    redact_payload,
    redact_prompt,
    secrets_for_refs,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session


@pytest.fixture()
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path}/evidence.db")
    m.init_db(engine)
    factory = m.session_factory(engine)
    with factory() as s:
        yield s


@pytest.fixture()
def store(session: Session) -> EvidenceStore:
    return EvidenceStore(session)


# --- append / get -----------------------------------------------------------


def test_append_get_round_trip(store: EvidenceStore) -> None:
    row = store.append(
        kind="probe",
        payload={"url": "http://sim-harbor:8000/reservations", "status": 200},
        run_id="build_runs:abc",
    )
    assert row.id
    assert row.kind == "probe"
    assert row.parent_ids == []
    fetched = store.get(row.id)
    assert fetched is not None
    assert fetched.payload == {"url": "http://sim-harbor:8000/reservations", "status": 200}
    assert fetched.run_id == "build_runs:abc"
    assert fetched.created_at is not None


def test_get_unknown_returns_none(store: EvidenceStore) -> None:
    assert store.get("no-such-id") is None


def test_all_plan_kinds_accepted(store: EvidenceStore, session: Session) -> None:
    for kind in sorted(EVIDENCE_KINDS):
        store.append(kind=kind, payload={"k": kind}, run_id="r:1")
    session.commit()
    assert len(store.list_for_run("r:1")) == len(EVIDENCE_KINDS)


def test_unknown_kind_rejected(store: EvidenceStore) -> None:
    with pytest.raises(UnknownKindError):
        store.append(kind="gossip", payload={}, run_id="r:1")


def test_unknown_parent_rejected(store: EvidenceStore) -> None:
    with pytest.raises(UnknownParentError):
        store.append(kind="decision", payload={}, run_id="r:1", parent_ids=["missing"])


def test_empty_run_id_rejected(store: EvidenceStore) -> None:
    with pytest.raises(ValueError):
        store.append(kind="probe", payload={}, run_id="")


def test_non_json_payload_rejected(store: EvidenceStore) -> None:
    with pytest.raises(TypeError):
        store.append(kind="probe", payload={"fn": object()}, run_id="r:1")  # type: ignore[dict-item]


# --- chains -----------------------------------------------------------------


def test_parent_chain_and_children(store: EvidenceStore) -> None:
    probe = store.append(kind="probe", payload={"u": 1}, run_id="r:1")
    obs = store.append(
        kind="observation", payload={"n": 3}, run_id="r:1", parent_ids=[probe.id]
    )
    hyp = store.append(
        kind="hypothesis", payload={"h": 1}, run_id="r:1", parent_ids=[obs.id]
    )
    chain = store.chain(hyp.id)
    assert [r.id for r in chain] == [probe.id, obs.id, hyp.id]
    assert [r.id for r in store.children(probe.id)] == [obs.id]
    assert [r.id for r in store.children(obs.id)] == [hyp.id]
    assert store.children(hyp.id) == []


def test_diamond_chain_lists_each_record_once(store: EvidenceStore) -> None:
    root = store.append(kind="probe", payload={}, run_id="r:1")
    left = store.append(kind="observation", payload={}, run_id="r:1", parent_ids=[root.id])
    right = store.append(kind="observation", payload={}, run_id="r:1", parent_ids=[root.id])
    join = store.append(
        kind="decision", payload={}, run_id="r:1", parent_ids=[left.id, right.id]
    )
    ids = [r.id for r in store.chain(join.id)]
    assert sorted(ids) == sorted({root.id, left.id, right.id, join.id})
    assert ids[-1] == join.id
    assert ids[0] == root.id


def test_chain_unknown_id_raises(store: EvidenceStore) -> None:
    with pytest.raises(KeyError):
        store.chain("no-such-id")


def test_list_for_run_scoped_and_ordered(store: EvidenceStore, session: Session) -> None:
    a = store.append(kind="probe", payload={"i": 1}, run_id="r:1")
    b = store.append(kind="probe", payload={"i": 2}, run_id="r:2")
    c = store.append(kind="observation", payload={"i": 3}, run_id="r:1", parent_ids=[a.id])
    session.commit()
    ids = [r.id for r in store.list_for_run("r:1")]
    assert ids == [a.id, c.id]
    assert b.id not in ids


# --- redaction at the store boundary ----------------------------------------


def test_append_redacts_secrets_and_keeps_caller_copy(
    store: EvidenceStore, session: Session
) -> None:
    secret = "planted-harbor-key-9f8e"
    payload = {"auth": f"Bearer {secret}", "nested": {"tokens": [secret]}}
    row = store.append(
        kind="probe", payload=payload, run_id="r:1", secrets=[secret]
    )
    session.commit()
    stored = store.get(row.id)
    assert stored is not None
    dumped = str(stored.payload)
    assert secret not in dumped
    assert "Bearer ***" in dumped
    # Caller object untouched.
    assert payload["auth"] == f"Bearer {secret}"


# --- JSONL ------------------------------------------------------------------


def test_jsonl_round_trip_preserves_chain(
    store: EvidenceStore, session: Session, tmp_path: Path
) -> None:
    probe = store.append(kind="probe", payload={"u": 1}, run_id="r:1")
    store.append(kind="observation", payload={}, run_id="r:1", parent_ids=[probe.id])
    session.commit()
    path = tmp_path / "evidence.jsonl"
    assert store.dump_jsonl(path, run_id="r:1") == 2

    engine2 = create_engine(f"sqlite:///{tmp_path}/evidence2.db")
    m.init_db(engine2)
    with m.session_factory(engine2)() as s2:
        assert EvidenceStore.load_jsonl(s2, path) == 2
        s2.commit()
        reloaded = EvidenceStore(s2)
        rows = reloaded.list_for_run("r:1")
        assert len(rows) == 2
        child = next(r for r in rows if r.kind == "observation")
        assert child.parent_ids == [probe.id]
        # Idempotent re-import inserts nothing.
        assert EvidenceStore.load_jsonl(s2, path) == 0


def test_chain_dangling_parent_raises(store: EvidenceStore, session: Session) -> None:
    from autobridge.control.models import EvidenceRecord as _ER

    parent = store.append(kind="probe", payload={}, run_id="r:1")
    child = store.append(
        kind="observation", payload={}, run_id="r:1", parent_ids=[parent.id]
    )
    session.delete(parent)
    session.flush()
    assert session.get(_ER, parent.id) is None
    with pytest.raises(UnknownParentError):
        store.chain(child.id)


def test_dump_jsonl_without_run_id_dumps_all(
    store: EvidenceStore, session: Session, tmp_path: Path
) -> None:
    store.append(kind="probe", payload={}, run_id="r:1")
    store.append(kind="probe", payload={}, run_id="r:2")
    session.commit()
    path = tmp_path / "all.jsonl"
    assert store.dump_jsonl(path) == 2
    assert len(path.read_text().strip().splitlines()) == 2


def test_jsonl_load_unknown_parent_raises(
    session: Session, tmp_path: Path
) -> None:
    import json as _json

    path = tmp_path / "orphan.jsonl"
    path.write_text(
        "\n"
        + _json.dumps(
            {
                "id": "orphan1",
                "kind": "decision",
                "payload": {},
                "parent_ids": ["ghost"],
                "run_id": "r:9",
                "created_at": None,
            }
        )
        + "\n"
    )
    with pytest.raises(UnknownParentError):
        EvidenceStore.load_jsonl(session, path)


def test_jsonl_load_unknown_kind_raises(session: Session, tmp_path: Path) -> None:
    import json as _json

    path = tmp_path / "badkind.jsonl"
    path.write_text(
        _json.dumps(
            {
                "id": "bad1",
                "kind": "gossip",
                "payload": {},
                "parent_ids": [],
                "run_id": "r:9",
                "created_at": None,
            }
        )
        + "\n\n"
    )
    with pytest.raises(UnknownKindError):
        EvidenceStore.load_jsonl(session, path)


# --- redact -----------------------------------------------------------------


PLANTED = {
    "HARBOR_API_KEY": "planted-harbor-key-9f8e",
    "ATLAS_BEARER": "planted-atlas-token-1234",
}

FIXTURE_PROMPT = """Sync HarborPMS reservations.
GET http://sim-harbor:8000/reservations
Authorization: Bearer planted-harbor-key-9f8e
Atlas check token: planted-atlas-token-1234
Retry with Bearer planted-harbor-key-9f8e on 401."""


def test_redact_prompt_strips_every_credential_ref_value() -> None:
    out = redact_prompt(FIXTURE_PROMPT, ["HARBOR_API_KEY", "ATLAS_BEARER"], PLANTED)
    for secret in PLANTED.values():
        assert secret not in out
    assert out.count("***") == 3
    assert "GET http://sim-harbor:8000/reservations" in out


def test_redact_prompt_ignores_missing_refs() -> None:
    out = redact_prompt("nothing secret here", ["MISSING_REF"], PLANTED)
    assert out == "nothing secret here"


def test_redact_skips_empty_secrets() -> None:
    assert redact("abc", ["", "b"]) == "a***c"


def test_redact_longest_first_for_overlapping_values() -> None:
    out = redact("token=abcdef", ["abc", "abcdef"])
    assert out == "token=***"


def test_redact_non_string_passthrough() -> None:
    assert redact(42, ["4"]) == 42  # type: ignore[arg-type]


def test_redact_payload_recursive() -> None:
    secret = PLANTED["HARBOR_API_KEY"]
    payload: dict[str, Any] = {
        "headers": {"Authorization": f"Bearer {secret}"},
        "items": [secret, {"deep": [f"x{secret}y"]}],
        secret: "key-position",
    }
    out = redact_payload(payload, [secret])
    assert secret not in str(out)
    assert out["headers"] == {"Authorization": "Bearer ***"}
    assert out["items"][1] == {"deep": ["x***y"]}
    assert out["***"] == "key-position"
    # Input not mutated.
    assert payload["headers"] == {"Authorization": f"Bearer {secret}"}
    # Non-container leaves pass through untouched.
    assert redact_payload(42, [secret]) == 42
    assert redact_payload(None, [secret]) is None


def test_secrets_for_refs_skips_missing() -> None:
    assert secrets_for_refs(["HARBOR_API_KEY", "NOPE"], PLANTED) == [
        PLANTED["HARBOR_API_KEY"]
    ]
    assert secrets_for_refs([], PLANTED) == []
