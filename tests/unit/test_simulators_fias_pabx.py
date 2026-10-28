"""REQ-022 sub-step A — FIAS PABX simulator unit tests.

Protocol codec (no sockets), the PABX link engine against an in-process
spec-faithful host over real TCP loopback, the admin router, the public
app and the launcher hook. The live link against Compose is covered by
``tests/e2e/test_fias_pabx_admin.py``; the edge agent that speaks this
protocol lands in sub-step B.
"""

from __future__ import annotations

import contextlib
import datetime
import socket
import time
from collections import Counter
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from simulators.common import admin as common_admin
from simulators.common.admin import ADMIN_TOKEN_ENV_VAR
from simulators.fias_pabx import admin as fias_admin
from simulators.fias_pabx import protocol
from simulators.fias_pabx.pabx import PabxConfig, PabxLink
from simulators.fias_pabx.protocol import (
    ACK,
    NAK,
    FrameSplitter,
    OversizeFrameError,
    Record,
    encode_frame,
    parse_frame,
)

SITE_TZ = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


def test_encode_worked_example_from_spec() -> None:
    frame = encode_frame("PS", [("RN", "101"), ("P#", "000417"), ("TA", "1250")])
    assert frame == b"\x02PS|RN101|P#000417|TA1250|\x03"


def test_roundtrip_keeps_field_order_and_unknown_ids() -> None:
    rec = parse_frame(encode_frame("PS", [("RN", "101"), ("ZZ", "x"), ("TA", "1250")])[1:-1])
    assert rec.record_type == "PS"
    assert rec.field_ids() == ["RN", "ZZ", "TA"]
    assert rec.field("ZZ") == "x"
    assert rec.field("missing") == ""


def test_parse_rejects_structural_problems() -> None:
    for bad in (
        b"PSRN101|",  # no separator between type and fields
        b"PX RN101|",  # bad separator inside head
        b"TOOLONG|RN101|",  # record type not 2 chars
        b"PS|RN101",  # missing trailing pipe
        b"PS|X|",  # field chunk too short
        b"PS|RN\x00101|",  # control character inside a frame
        b"PS|\xff|",  # not ASCII
    ):
        with pytest.raises(protocol.FrameError):
            parse_frame(bad)


def test_encode_rejects_unsafe_values() -> None:
    with pytest.raises(protocol.FrameError):
        encode_frame("PSS", [("RN", "101")])
    with pytest.raises(protocol.FrameError):
        encode_frame("PS", [("R", "101")])
    with pytest.raises(protocol.FrameError):
        encode_frame("PS", [("RN", "10|1")])
    with pytest.raises(protocol.FrameError):
        encode_frame("PS", [("RN", "héllo")])


def test_encode_rejects_oversize_frame() -> None:
    with pytest.raises(OversizeFrameError):
        encode_frame("PS", [("RN", "x" * (protocol.MAX_FRAME_LEN + 1))])


def test_splitter_separates_frames_bytes_and_noise() -> None:
    sp = FrameSplitter()
    out = sp.feed(b"\x00noise\x02PS|RN101|\x03\x06junk\x15\x02LA|DA261028|\x03")
    kinds = [r.kind for r in out]
    # The leading zero run is a resync signal (§2.3); "noise"/"junk" are
    # line noise and discarded (§1).
    assert kinds == ["zeros", "frame", "ack", "nak", "frame"]
    assert out[1].record is not None and out[1].record.record_type == "PS"
    assert out[4].record is not None and out[4].record.record_type == "LA"


def test_splitter_zero_run_is_resync_signal() -> None:
    sp = FrameSplitter()
    assert sp.feed(b"\x00\x00") == []
    out = sp.feed(b"\x00\x02PS|RN101|\x03")
    assert [r.kind for r in out] == ["zeros", "frame"]
    assert sp.flush() == []


def test_splitter_frame_split_across_feeds() -> None:
    sp = FrameSplitter()
    assert sp.feed(b"\x02PS|RN1") == []
    out = sp.feed(b"01|\x03")
    assert len(out) == 1 and out[0].record is not None
    assert out[0].record.field("RN") == "101"


def test_splitter_bad_frame_reports_for_nak() -> None:
    sp = FrameSplitter()
    out = sp.feed(b"\x02PS|RN101\x03")
    assert [r.kind for r in out] == ["bad"]


def test_splitter_oversize_closes() -> None:
    sp = FrameSplitter()
    with pytest.raises(OversizeFrameError):
        sp.feed(b"\x02" + b"X" * (protocol.MAX_FRAME_LEN + 8))


# ---------------------------------------------------------------------------
# Link engine against an in-process host (real TCP loopback)
# ---------------------------------------------------------------------------


class Host:
    """Minimal spec-faithful host side for link tests.

    Listens on 127.0.0.1:0, runs one handshake per accepted connection,
    ACKs every frame (unless ``no_ack``), and queues received records in
    ``inbox``. Tests drive it explicitly — no background threads.
    """

    def __init__(self) -> None:
        self._lsnr = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsnr.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsnr.bind(("127.0.0.1", 0))
        self._lsnr.listen(5)
        self.port = self._lsnr.getsockname()[1]
        self.conn: socket.socket | None = None
        self.splitter = FrameSplitter()
        self.inbox: list[Record] = []
        self.acks = 0
        self.naks = 0
        self.no_ack = False
        self.nak_types: set[str] = set()

    def close(self) -> None:
        for sock in (self.conn, self._lsnr):
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()
        self.conn = None

    def accept(self, timeout: float = 5.0) -> None:
        if self.conn is not None:
            with contextlib.suppress(OSError):
                self.conn.close()
            self.conn = None
        self._lsnr.settimeout(timeout)
        self.conn, _ = self._lsnr.accept()
        assert self.conn is not None
        self.conn.settimeout(2.0)
        self.splitter = FrameSplitter()

    # -- primitives ------------------------------------------------------

    def _write(self, data: bytes) -> None:
        assert self.conn is not None
        self.conn.sendall(data)

    def send_frame(self, record_type: str, fields: list[tuple[str, str]]) -> None:
        self._write(encode_frame(record_type, fields))

    def pump(self, duration: float = 0.5) -> list[Record]:
        """Read for *duration*; ACK/NAK per policy; return new records."""
        assert self.conn is not None
        deadline = time.monotonic() + duration
        new: list[Record] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Bound the recv by the remaining budget: a fixed socket
            # timeout would overshoot every quiet poll by seconds.
            self.conn.settimeout(max(0.01, min(remaining, 2.0)))
            try:
                chunk = self.conn.recv(65536)
            except TimeoutError:
                continue
            if chunk == b"":
                raise ConnectionError("pabx closed the connection")
            for item in self.splitter.feed(chunk):
                if item.kind == "frame" and item.record is not None:
                    rec = item.record
                    new.append(rec)
                    self.inbox.append(rec)
                    if not self.no_ack:
                        if rec.record_type in self.nak_types:
                            self._write(bytes((NAK,)))
                            self.naks += 1
                        else:
                            self._write(bytes((ACK,)))
                            self.acks += 1
                elif item.kind == "bad":
                    self._write(bytes((NAK,)))
                # ACK/NAK/zeros from the PABX side: ACKs counted below.
                elif item.kind == "ack":
                    self.acks += 1
                elif item.kind == "nak":
                    self.naks += 1
        return new

    def wait_for_records(self, count: int, timeout: float = 5.0, want: str | None = None) -> list[Record]:
        """Pump until *count* new records arrive (or fail).

        With *want*, only records of that type count (others are ACKed and
        queued in ``inbox`` as usual).
        """
        deadline = time.monotonic() + timeout
        new: list[Record] = []
        while time.monotonic() < deadline and len(new) < count:
            for rec in self.pump(0.2):
                if want is None or rec.record_type == want:
                    new.append(rec)
        assert len(new) >= count, f"only {len(new)} records arrived: {[r.record_type for r in new]}"
        return new

    def pop(self, record_type: str) -> Record:
        for idx, rec in enumerate(self.inbox):
            if rec.record_type == record_type:
                return self.inbox.pop(idx)
        raise AssertionError(f"no {record_type} in inbox: {[r.record_type for r in self.inbox]}")

    # -- handshake (host side of §2.2) ------------------------------------

    def handshake(self, host_lrs: list[tuple[str, str]] | None = None) -> dict[str, str]:
        """Run the host side; return the PABX's announced {record: FL} layouts."""
        now = datetime.datetime.now(SITE_TZ)
        da, ti = now.strftime("%y%m%d"), now.strftime("%H%M%S")
        self.send_frame("LS", [("DA", da), ("TI", ti)])
        self._expect_ack()
        self._expect_frame("LS")
        self.send_frame("LD", [("DA", da), ("TI", ti), ("V#", "9.9"), ("IF", "PB")])
        self._expect_ack()
        for rtype, fl in host_lrs or [("PS", "RNP#DATITADDUSO"), ("PA", "RNP#ASDATI")]:
            self.send_frame("LR", [("RI", rtype), ("FL", fl)])
            self._expect_ack()
        self.send_frame("LA", [("DA", da), ("TI", ti)])
        self._expect_ack()
        layouts: dict[str, str] = {}
        for _ in range(4):  # the PABX announces PS, RE, WA, DR
            rec = self._expect_frame("LR")
            layouts[rec.field("RI")] = rec.field("FL")
        self._expect_frame("LA")
        return layouts

    def _expect_ack(self, timeout: float = 5.0) -> None:
        before = self.acks
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(0.1)
            if self.acks > before:
                return
        raise AssertionError("no ACK from the PABX")

    def _expect_frame(self, record_type: str, timeout: float = 5.0) -> Record:
        # Inbox first: an earlier pump may have consumed it (TCP coalesces
        # the PABX's ACK with its next frame).
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for idx, rec in enumerate(self.inbox):
                if rec.record_type == record_type:
                    return self.inbox.pop(idx)
            self.pump(0.1)
        raise AssertionError(f"no {record_type} from the PABX (got {[r.record_type for r in self.inbox]})")


def _link_config(host: Host, **overrides: object) -> PabxConfig:
    kwargs: dict[str, object] = {
        "host": "127.0.0.1",
        "port": host.port,
        "reconnect_interval": 0.2,
        "ack_wait": 1.0,
        "keepalive_interval": 30.0,
        "dead_after": 60.0,
        "resend_interval": 60.0,
        "seed": 0,
        "de_wait": 3.0,
    }
    kwargs.update(overrides)
    return PabxConfig(**kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def host() -> Host:
    h = Host()
    try:
        yield h
    finally:
        h.close()


def _start_link(host: Host, **overrides: object) -> PabxLink:
    link = PabxLink(_link_config(host, **overrides))
    link.start()
    host.accept()
    return link


@pytest.fixture()
def linked(host: Host) -> tuple[Host, PabxLink]:
    link = _start_link(host)
    try:
        layouts = host.handshake()
        assert set(layouts) == {"PS", "RE", "WA", "DR"}, layouts
        deadline = time.monotonic() + 5
        while not link.link_up and time.monotonic() < deadline:
            time.sleep(0.05)
        assert link.link_up, "link never came up after the handshake"
        yield host, link
    finally:
        link.stop()
        link.join(timeout=5)


def _pa(host: Host, rec: Record, answer: str = "OK") -> None:
    host.send_frame("PA", [("RN", rec.field("RN")), ("P#", rec.field("P#")),
                           ("AS", answer), ("DA", rec.field("DA")), ("TI", rec.field("TI"))])


def _drain_until_close(host: Host, timeout: float = 8.0) -> None:
    """Behave like a live host: keep ACKing until the PABX drops the link.

    One-shot faults fire at the PABX's serve-loop checkpoints, which it
    only reaches while the host keeps answering — a host that goes silent
    first would trip the no-ACK drop instead and carry the armed flag to
    the next link.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            host.pump(0.2)
        except ConnectionError:
            return
    raise AssertionError("link never dropped")


def test_handshake_announces_baseline_layouts(linked: tuple[Host, PabxLink]) -> None:
    host, link = linked
    assert link.link_up
    # §2.2 site extension: the PABX's own LR wins; baseline field order.
    assert link.snapshot()["ps_field_list"] == "RNP#DATITADDDUSO"
    # Stay postings flow: one PS per occupied room, P# increments from 1.
    # (000001 is usually consumed into the inbox by the handshake pumps,
    # so look at inbox + new arrivals together.)
    host.wait_for_records(1)
    seen = [r for r in host.inbox if r.record_type == "PS"]
    assert seen, "no stay postings flowed"
    first = min(seen, key=lambda r: r.field("P#"))
    assert first.field("P#") == "000001"
    assert first.field_ids()[0] == "RN"


def _wait_stay_sent(host: Host, link: PabxLink, timeout: float = 10.0) -> None:
    """Wait until the stay plan is fully sent (engine ground truth).

    Every occupied room posts once: the pending queue is empty and every
    posting sits un-PA'd. Afterwards the link is quiet (no resends for
    60 s, no keep-alive for 30 s), so ACK/frame counters are stable.
    """
    rooms = link.snapshot()["rooms"]
    total = sum(1 for r in rooms.values() if r["occupied"])
    assert total > 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = link.snapshot()
        if snap["pending_postings"] == 0 and len(snap["unacked"]) == total:
            return
        host.pump(0.2)
    raise AssertionError(f"stay plan never completed: {link.snapshot()}")


def test_postings_acked_and_pa_clears_unacked(linked: tuple[Host, PabxLink]) -> None:
    host, link = linked
    _wait_stay_sent(host, link)  # all 6 postings sent, none PA'd yet
    # PA every posting (inbox holdings and new arrivals alike); each P#
    # must clear exactly once and count exactly one OK.
    seen_pa: set[str] = set()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        for rec in host.pump(0.2):
            if rec.record_type == "PS" and rec.field("P#") not in seen_pa:
                seen_pa.add(rec.field("P#"))
                _pa(host, rec)
        for rec in list(host.inbox):
            if rec.record_type == "PS" and rec.field("P#") not in seen_pa:
                seen_pa.add(rec.field("P#"))
                _pa(host, rec)
                host.inbox.remove(rec)
        if link.snapshot()["unacked"] == []:
            break
    assert link.snapshot()["unacked"] == []
    assert link.snapshot()["counters"]["pa_ok"] == 6  # one stay posting per room


def test_ps_resent_until_pa_same_sequence(host: Host) -> None:
    link = _start_link(host, resend_interval=0.3)
    try:
        host.handshake()
        first = next(r for r in host.wait_for_records(1) if r.record_type == "PS")
        # No PA: the same P# must come back (§3.1 resend).
        deadline = time.monotonic() + 5
        seqs: list[str] = []
        while time.monotonic() < deadline:
            for rec in host.pump(0.2):
                if rec.record_type == "PS":
                    seqs.append(rec.field("P#"))
            if seqs.count(first.field("P#")) >= 2:
                break
        assert seqs.count(first.field("P#")) >= 2
        assert link.snapshot()["counters"]["postings_resent"] >= 1
        # PA every (re)sent posting so the plan completes, then the resend
        # counter must stand still.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for rec in host.pump(0.2):
                if rec.record_type == "PS":
                    _pa(host, rec)
            snap = link.snapshot()
            if snap["unacked"] == [] and snap["pending_postings"] == 0:
                break
        assert link.snapshot()["unacked"] == []
        resent_before = link.snapshot()["counters"]["postings_resent"]
        host.pump(1.0)
        assert link.snapshot()["counters"]["postings_resent"] == resent_before
    finally:
        link.stop()
        link.join(timeout=5)


def test_guest_in_change_out_roundtrip(linked: tuple[Host, PabxLink]) -> None:
    host, link = linked
    host.pump(0.5)
    host.send_frame("GO", [("RN", "101"), ("G#", "RES-1"), ("DA", "261028"), ("TI", "120000")])
    host.pump(0.5)
    assert link.snapshot()["rooms"]["101"]["occupied"] is False
    host.send_frame("GI", [("RN", "101"), ("G#", "RES-42"), ("GN", "Hopper,Grace"),
                           ("GL", "en"), ("GA", "261028"), ("GD", "261030"),
                           ("CS", "3"), ("DA", "261028"), ("TI", "120500")])
    host.pump(0.5)
    room = link.snapshot()["rooms"]["101"]
    assert room["occupied"] is True
    assert room["reservation"] == "RES-42"


def test_wakeup_future_fires_wa_ok() -> None:
    host = Host()
    try:
        moment = datetime.datetime(2026, 10, 28, 12, 0, 0, tzinfo=SITE_TZ)
        clock = {"now": moment}
        link = PabxLink(_link_config(host), clock=lambda: clock["now"])
        link.start()
        host.accept()
        host.handshake()
        host.pump(1.0)  # drain the stay postings (ACKed, never PA'd)
        host.send_frame("WR", [("RN", "102"), ("DA", "261028"), ("TI", "120100")])
        host.pump(0.5)
        assert link.snapshot()["scheduled_wakeups"] == 1
        clock["now"] = moment + datetime.timedelta(minutes=2)
        wa = host.wait_for_records(1, timeout=8, want="WA")
        assert wa and wa[0].field("AS") == "OK" and wa[0].field("RN") == "102"
        assert link.snapshot()["counters"]["wakeups_fired"] >= 1
        link.stop()
        link.join(timeout=5)
    finally:
        host.close()


def test_wakeup_in_past_answered_ur_never_scheduled(linked: tuple[Host, PabxLink]) -> None:
    host, link = linked
    host.pump(0.5)
    host.send_frame("WR", [("RN", "102"), ("DA", "200101"), ("TI", "000000")])
    wa = host.wait_for_records(1, timeout=8, want="WA")
    assert wa and wa[0].field("AS") == "UR"
    assert link.snapshot()["scheduled_wakeups"] == 0


def test_key_write_answered_with_ka_and_read_with_kz(linked: tuple[Host, PabxLink]) -> None:
    host, link = linked
    host.pump(0.5)
    host.send_frame("KR", [("KC", "01"), ("RN", "103"), ("K#", "2"),
                           ("GA", "261028"), ("GD", "261030"), ("G#", "RES-7"),
                           ("DA", "261028"), ("TI", "121000")])
    ka = host.wait_for_records(1, timeout=8, want="KA")
    assert ka and ka[0].field("AS") == "OK"
    host.send_frame("KZ", [("KC", "01"), ("DA", "261028"), ("TI", "121100")])
    kz = host.wait_for_records(1, timeout=8, want="KZ")
    assert kz and kz[0].field("RN") == "101"  # the card in the encoder


def test_unknown_record_type_gets_nak(linked: tuple[Host, PabxLink]) -> None:
    host, _link = linked
    host.pump(0.5)
    before = host.naks
    host.send_frame("ZZ", [("DA", "261028")])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and host.naks == before:
        host.pump(0.1)
    assert host.naks > before


def test_layout_v2_announced_after_reconnect(host: Host) -> None:
    link = _start_link(host)
    try:
        layouts = host.handshake()
        assert layouts["PS"] == "RNP#DATITADDDUSO"
        with link.lock:
            link.faults.ps_layout = "layout_v2"
            link.faults.drop_link = True
        _drain_until_close(host)  # live host keeps answering; the drop fires fast
        host.accept(timeout=8)  # the PABX reconnects on its own
        layouts2 = host.handshake()
        assert layouts2["PS"] == "P#RNTIDASOTADUDDCX", layouts2
        assert link.snapshot()["ps_field_list"] == "P#RNTIDASOTADUDDCX"
        ps = next(r for r in host.wait_for_records(1) if r.record_type == "PS")
        assert ps.field_ids()[0] == "P#"
        assert ps.field("CX") in ("VOICE", "SNACK")
    finally:
        link.stop()
        link.join(timeout=5)


def test_zeros_resync_replays_unacked_with_same_pno(host: Host) -> None:
    link = _start_link(host)
    try:
        host.handshake()
        first = next(r for r in host.wait_for_records(1) if r.record_type == "PS")
        pno = first.field("P#")
        with link.lock:
            link.faults.send_zeros_then_drop = True
        # Zeros arrive, the link drops, the PABX reconnects and re-handshakes.
        _drain_until_close(host)
        # Engine ground truth: every P# still un-PA'd must be received
        # AGAIN after the resync (counters prove the resend: pre-drop
        # inbox holdings do not count). No P# beyond the drop-time
        # sequence may appear (no invention, only replay + continuations).
        unacked_before = set(link.snapshot()["unacked"])
        seq = int(link.snapshot()["posting_seq"])
        assert pno in unacked_before
        pre = Counter(r.field("P#") for r in host.inbox if r.record_type == "PS")
        host.accept(timeout=8)
        host.handshake()
        # Replays race the handshake's own pumps (the first replayed P#s
        # land in the inbox during hs2), so diff inbox counts across hs2
        # and add everything pumped afterwards.
        mid = Counter(r.field("P#") for r in host.inbox if r.record_type == "PS")
        allowed = {f"{i:06d}" for i in range(1, seq + 1)}
        post: Counter[str] = Counter()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for rec in host.pump(0.3):
                if rec.record_type == "PS":
                    post[rec.field("P#")] += 1
            fresh = {p for p in set(mid) | set(post) if mid.get(p, 0) - pre.get(p, 0) + post.get(p, 0) > 0}
            if unacked_before <= fresh:
                break
        fresh = {p for p in set(mid) | set(post) if mid.get(p, 0) - pre.get(p, 0) + post.get(p, 0) > 0}
        assert unacked_before <= fresh, f"replay missed {unacked_before - fresh}"
        assert fresh <= allowed, f"replay invented {fresh - allowed}"
    finally:
        link.stop()
        link.join(timeout=5)


def test_silent_wakeup_commands_are_never_answered(linked: tuple[Host, PabxLink]) -> None:
    host, link = linked
    _wait_stay_sent(host, link)  # link goes quiet; later ACK counts are meaningful
    with link.lock:
        link.faults.silent_types = ("WR",)
    acks_before = host.acks
    host.send_frame("WR", [("RN", "104"), ("DA", "261028"), ("TI", "130000")])
    host.pump(1.0)
    assert host.acks == acks_before  # ACK-timeout fault: never answered, never dropped
    assert link.snapshot()["counters"]["silent_dropped"] >= 1


def test_oversize_host_frame_closes_session(host: Host) -> None:
    link = _start_link(host)
    try:
        host.handshake()
        links_before = link.links_established
        assert links_before >= 1
        host.conn.sendall(b"\x02" + b"Y" * 70000)  # over the 64 KB site limit
        with contextlib.suppress(ConnectionError):
            host.pump(2.0)  # the session is closed, cleanly or not
        assert link.snapshot()["counters"]["oversize_closed"] >= 1
        # The PABX reconnects on its own; complete the new handshake.
        for _attempt in range(2):
            try:
                host.accept(timeout=8)
                host.handshake()
                break
            except (ConnectionError, AssertionError):
                continue
        assert link.links_established > links_before, "the PABX reconnects after the close"
    finally:
        link.stop()
        link.join(timeout=5)


def test_peer_le_ends_link_cleanly_then_reconnect(host: Host) -> None:
    link = _start_link(host)
    try:
        host.handshake()
        links_before = link.links_established
        host.send_frame("LE", [("DA", "261028"), ("TI", "140000")])
        host.accept(timeout=8)  # clean close, then the scheduled reconnect
        host.handshake()
        assert link.links_established > links_before
    finally:
        link.stop()
        link.join(timeout=5)


# ---------------------------------------------------------------------------
# Admin router, public app, launcher hook
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient over the shared admin app + FIAS router (no sockets)."""
    from simulators.fias_pabx.pabx import PabxConfig, PabxLink  # noqa: PLC0415

    monkeypatch.delenv(ADMIN_TOKEN_ENV_VAR, raising=False)
    link = PabxLink(PabxConfig(host="127.0.0.1", port=1))
    fias_admin.set_link(link)
    try:
        app = common_admin.create_admin_app(fias_admin.router)
        with TestClient(app) as client:
            yield client
    finally:
        fias_admin.set_link(None)


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Network": "admin-net"}


def test_fias_admin_requires_admin_network(admin_client: TestClient) -> None:
    assert admin_client.get("/admin/fias/state").status_code == 403
    assert admin_client.post("/admin/fias/faults", json={}).status_code == 403
    assert admin_client.post("/admin/fias/reset").status_code == 403


def test_fias_admin_state_and_faults_roundtrip(admin_client: TestClient) -> None:
    r = admin_client.get("/admin/fias/state", headers=_admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["link_up"] is False
    assert body["ps_layout"] == "baseline"
    assert len(body["rooms"]) == 6

    r = admin_client.post(
        "/admin/fias/faults",
        json={"ps_layout": "layout_v2", "silent_types": ["WR"], "burst_postings": 3},
        headers=_admin_headers(),
    )
    assert r.status_code == 200
    assert r.json()["ps_layout"] == "layout_v2"
    assert r.json()["ps_field_list"] == "P#RNTIDASOTADUDDCX"
    assert r.json()["faults"]["silent_types"] == ["WR"]

    r = admin_client.post("/admin/fias/reset", headers=_admin_headers())
    assert r.status_code == 200
    assert r.json()["ps_layout"] == "baseline"
    assert r.json()["faults"]["silent_types"] == []


def test_fias_admin_rejects_bad_faults(admin_client: TestClient) -> None:
    bad = [
        {"ps_layout": "v3"},
        {"silent_types": ["TOOLONG"]},
        {"burst_postings": 999},
    ]
    for payload in bad:
        r = admin_client.post("/admin/fias/faults", json=payload, headers=_admin_headers())
        assert r.status_code == 400, payload


def test_fias_admin_token_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulators.fias_pabx.pabx import PabxConfig, PabxLink

    monkeypatch.setenv(ADMIN_TOKEN_ENV_VAR, "test-token")
    fias_admin.set_link(PabxLink(PabxConfig(host="127.0.0.1", port=1)))
    try:
        app = common_admin.create_admin_app(fias_admin.router)
        with TestClient(app) as client:
            assert client.get("/admin/fias/state", headers=_admin_headers()).status_code == 403
            ok = dict(_admin_headers())
            ok["X-Admin-Token"] = "test-token"
            assert client.get("/admin/fias/state", headers=ok).status_code == 200
    finally:
        fias_admin.set_link(None)


def test_public_app_health_and_readyz_without_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIAS_NO_LINK", "1")
    from simulators.fias_pabx import app as fias_app_module

    with TestClient(fias_app_module.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        body = client.get("/readyz").json()
        assert body["status"] == "ok"
        assert body["checks"]["fias_link"].startswith("disabled")
        # Docs page + single source + raw mount.
        page = client.get("/developers")
        assert page.status_code == 200
        assert "Grand Plaza" in page.text
        src = client.get("/developers/source")
        assert src.status_code == 200
        assert src.text.startswith("# Grand Plaza")
        assert client.get("/docs/fias-pabx.md").status_code == 200


def test_run_launcher_loads_valid_extra_router() -> None:
    """The ADMIN_EXTRA_ROUTER ref main() consumes must load and mount."""
    import simulators.run as run_module

    router = run_module._load_app("simulators.fias_pabx.admin:router")
    assert "/admin/fias/state" in {r.path for r in router.routes}
    # And it mounts onto the shared admin app (served paths proven by the
    # admin_client tests above).
    common_admin.create_admin_app(router)


def test_run_launcher_rejects_bad_extra_router(monkeypatch: pytest.MonkeyPatch) -> None:
    import simulators.run as run_module

    monkeypatch.setenv("ADMIN_EXTRA_ROUTER", "no.such.module:router")
    with pytest.raises(SystemExit):
        run_module.main()


def test_send_and_wait_ack_survives_idle_poll_tick() -> None:
    """A recv tick with no data retries the wait (never a premature resend).

    Pins the poll-tick branch deterministically: the peer answers 0.45 s
    after the frame, so the 0.2 s recv tick must fire (and loop) exactly
    once before the ACK lands. Without it the simulators coverage gate
    flaps across 90% run to run.
    """
    import socket as _socket
    import threading

    from simulators.fias_pabx.pabx import PabxConfig, PabxLink
    from simulators.fias_pabx.protocol import ACK, encode_frame

    link = PabxLink(PabxConfig())
    a, b = _socket.socketpair()
    try:
        a.settimeout(0.2)
        b.settimeout(5.0)

        def _late_ack() -> None:
            data = b.recv(65536)
            assert data.startswith(bytes((0x02,)))
            time.sleep(0.45)  # past one 0.2 s recv tick, inside ack_wait
            b.sendall(bytes((ACK,)))

        peer = threading.Thread(target=_late_ack, daemon=True)
        peer.start()
        before = link.counters["resends"]
        link._send_and_wait_ack(a, "LA", [("DA", "261028"), ("TI", "120000")])
        peer.join(timeout=5.0)
        assert not peer.is_alive()
        assert link.counters["resends"] == before  # no resend, just a tick
        assert link.counters["acks_in"] == 1
    finally:
        a.close()
        b.close()
