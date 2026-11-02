"""REQ-083 A1: harness self-test — every vendor's pre-drift spec over the
unfaulted simulator passes the records judge.

Parametrised over every vendor in ``config/beacon_vendors.yaml``: the
apparatus (spec + serving + judge) must credit a correct answer before
it measures anyone. HTTP vendors judge against the oracle dataset;
``fias-pabx`` judges against its own unfaulted feed output (the one
judging regime — no feed sync can reproduce the 71-row folio dataset).
"""

from __future__ import annotations

import pytest

import verifier.beacon as vbeacon
from bench import beacon_run as beacon_run

EVAL_SEED_HEX = "0123456789abcdef" * 4  # 32 bytes, fixed for the preflight


def _vendors() -> list:
    return beacon_run.load_vendors()


@pytest.mark.parametrize("pos", range(5), ids=lambda p: f"vendor-{p}")
def test_preflight_unfaulted_spec_passes_judge(pos: int) -> None:
    vendors = beacon_run.load_vendors()
    entry = vendors[pos % len(vendors)]
    vendor = str(entry.vendor)
    desc = {"index": pos, "vendor": vendor}
    spec = beacon_run.engine_spec_for(desc, vendors)
    assert spec is not None, f"no pre-drift spec for vendor {vendor!r}"
    # The judged instance must expect a repair (an escalate key fails the
    # records judge by design).
    index = beacon_run._preflight_repair_index(vendor, EVAL_SEED_HEX, pos)
    produced = beacon_run.unfaulted_baseline_records(vendor, EVAL_SEED_HEX, index, spec)
    n_records = len(produced.get("reservations", [])) + len(produced.get("folios", []))
    assert n_records > 0, f"{vendor}: unfaulted sync produced no records"
    import simulators.common.faultgen as faultgen

    instance = faultgen.generate_instance(bytes.fromhex(EVAL_SEED_HEX), index)
    if entry.http:
        # The unfaulted sync agrees with the oracle (A1 side-fixes: money
        # totals, single-zone baseline, no sim-side nulling).
        judged = vbeacon.judge_produced_records(instance, produced, None)
    else:
        # FIAS: the unfaulted feed output is the comparison baseline.
        judged = vbeacon.judge_produced_records(instance, produced, None, produced)
    assert judged["verdict"] == "pass", (
        f"{vendor}: unfaulted pre-drift sync fails the judge: {judged['failed_checks']}"
    )
