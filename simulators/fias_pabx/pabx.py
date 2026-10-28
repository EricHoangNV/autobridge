"""The PABX side of the FIAS link: dial-out, handshake, records, faults.

Implements ``simulators/docs/fias-pabx.md`` §2–§5 from the PABX's point of
view. The integration host listens (port 5010 per the site settings) and
this engine — running in the ``sim-fias-pabx`` container — connects to it,
reconnecting every ``reconnect_interval`` while no link is up (§5: one link
at a time, no second connection while a link is up).

Rooms and guests come from the shared seeded generator, so postings land
on the same in-house guests the PMS simulators serve (REQ-022 A).

Fault hooks (driven by the admin API; the scenario catalog grows in
REQ-022 D):

* ``ps_layout`` — ``"baseline"`` or ``"layout_v2"`` (firmware update:
  reordered ``PS`` fields plus an added ``CX`` field, announced in the
  PABX's own ``LR`` records — §2.2 site extension).
* ``send_zeros_then_drop`` — one-shot: emit all-zero bytes (the §2.3
  out-of-sync signal), drop the connection and reconnect; un-PA'd
  postings are resent after the new link.
* ``drop_link`` — one-shot: close the connection now.
* ``silent_types`` — record types the PABX never answers (ACK-timeout
  fault: the cloud side must report the timeout, never drop it).
* ``resync_on_reconnect`` — send ``DR`` after the next handshake and run
  the ``DS`` / ``GI`` … / ``DE`` database swap before postings resume.
* ``burst_postings`` — queue N extra call postings immediately.

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

import contextlib
import datetime
import random
import socket
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from simulators.common.generator import generate_dataset

from .protocol import ACK, NAK, FrameSplitter, OversizeFrameError, Record, encode_frame

if TYPE_CHECKING:
    from collections.abc import Callable

SITE_PROPERTY_ID = "prop-001"
SITE_TIMEZONE = "America/New_York"
SITE_CURRENCY = "USD"

#: Site settings from fias-pabx.md (§1–§2).
DEFAULT_RECONNECT_INTERVAL = 10.0
DEFAULT_KEEPALIVE_INTERVAL = 5.0
DEFAULT_DEAD_AFTER = 30.0
DEFAULT_ACK_WAIT = 5.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RESEND_INTERVAL = 60.0
DEFAULT_ZERO_BURST = b"\x00\x00\x00"

#: PABX-originated records and their baseline field layouts (wire order).
#: The host only receives record types it announced in its own ``LR``
#: records — but the PABX announces its own layout back (§2.2) and its
#: layout wins.
BASELINE_LAYOUTS: dict[str, list[str]] = {
    "PS": ["RN", "P#", "DA", "TI", "TA", "DD", "DU", "SO"],
    "RE": ["RN", "RS", "DA", "TI"],
    "WA": ["RN", "DA", "TI", "AS"],
    "DR": ["DA", "TI"],
}

#: Firmware-update layout (§2.2, §5): ``PS`` fields reordered, ``TA`` still
#: minor-units, plus an added ``CX`` (charge class) field. The mapping
#: repair in REQ-022 D keys off these ids.
LAYOUT_V2_PS = ["P#", "RN", "TI", "DA", "SO", "TA", "DU", "DD", "CX"]

MINIBAR_OUTLET = "MINI"
CALL_OUTLET = "TEL"

ROOM_NUMBERS = ["101", "102", "103", "104", "105", "106"]
LANGUAGES = ["en", "de", "fr"]


def _wire_date(moment: datetime.datetime) -> str:
    return moment.strftime("%y%m%d")


def _wire_time(moment: datetime.datetime) -> str:
    return moment.strftime("%H%M%S")


def _last_first(name: str) -> str:
    if "," in name:  # already in Last,First wire form
        last, _, first = name.partition(",")
        return f"{last.strip()},{first.strip()}"
    parts = name.split()
    if len(parts) < 2:
        return name
    return f"{parts[-1]},{parts[0]}"


@dataclass
class PabxConfig:
    """Connection and timing configuration (site settings in caps)."""

    host: str = "edge-worker"
    port: int = 5010
    reconnect_interval: float = DEFAULT_RECONNECT_INTERVAL
    keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL
    dead_after: float = DEFAULT_DEAD_AFTER
    ack_wait: float = DEFAULT_ACK_WAIT
    max_retries: int = DEFAULT_MAX_RETRIES
    resend_interval: float = DEFAULT_RESEND_INTERVAL
    seed: int = 0
    de_wait: float = 30.0


@dataclass
class LinkFaults:
    """Admin-driven fault hooks (REQ-022 A knobs; scenarios land in D).

    One-shot flags (``send_zeros_then_drop``, ``drop_link``) fire at the
    next serve-loop checkpoint; a link that drops first (no ACK, oversize)
    carries the still-armed flag to the next link — it fires exactly once.
    """

    ps_layout: str = "baseline"
    send_zeros_then_drop: bool = False
    drop_link: bool = False
    silent_types: tuple[str, ...] = ()
    resync_on_reconnect: bool = False
    burst_postings: int = 0


@dataclass
class _Room:
    rn: str
    guest_name: str | None
    reservation_no: str
    arrival: str  # YYMMDD
    departure: str  # YYMMDD
    language: str
    occupied: bool = True


class _LinkDropped(Exception):
    """Internal: the current connection is dead; reconnect."""


class PabxLink(threading.Thread):
    """A FIAS PABX link engine running in its own thread.

    ``start()`` dials the host and serves the link until ``stop()``.
    All mutable state is guarded by ``lock``; :meth:`snapshot` returns a
    JSON-safe view for the admin API.
    """

    def __init__(
        self,
        config: PabxConfig | None = None,
        *,
        clock: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        super().__init__(name="fias-pabx-link", daemon=True)
        self.config = config or PabxConfig()
        self._clock = clock  # callable returning aware datetime, or None
        self._halt = threading.Event()
        self._splitter = FrameSplitter()  # replaced per connection in _serve_one_link
        self.lock = threading.Lock()
        self.faults = LinkFaults()
        self._rng = random.Random(self.config.seed)
        self._rooms: dict[str, _Room] = {}
        self._seed_rooms()
        self._posting_seq = 0
        self._pending: list[Record] = []  # PS records queued to send
        self._unacked: dict[str, dict[str, object]] = {}  # P# -> {record, last, seq}
        self._stay_queued = False  # stay postings are planned once per process
        self._wakeups: dict[tuple[str, str, str], None] = {}  # (RN, DA, TI) scheduled
        self._key_card = {"KC": "01", "RN": "101", "G#": "RES-CARD-001"}
        self._awaiting_dbswap = False
        self._swapped_rooms: set[str] = set()
        self.link_up = False
        self.links_established = 0
        self.counters = {
            "frames_in": 0,
            "frames_out": 0,
            "acks_in": 0,
            "naks_in": 0,
            "resends": 0,
            "handshakes": 0,
            "links_dropped": 0,
            "postings_sent": 0,
            "postings_resent": 0,
            "duplicates_seen_by_host": 0,  # PAs referencing an already-PA'd P#
            "pa_ok": 0,
            "pa_not_posted": 0,
            "wakeups_fired": 0,
            "oversize_closed": 0,
            "silent_dropped": 0,
        }

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def _now(self) -> datetime.datetime:
        if self._clock is not None:
            return self._clock()
        return datetime.datetime.now(ZoneInfo(SITE_TIMEZONE))

    def _seed_rooms(self) -> None:
        """Occupy rooms with guests from the shared generator (prop-001)."""
        dataset = generate_dataset(seed=self.config.seed)
        reservations = [r for r in dataset["reservations"] if str(r["property_id"]) == SITE_PROPERTY_ID]
        reservations.sort(key=lambda r: str(r["reservation_id"]))
        self._rooms.clear()
        for idx, rn in enumerate(ROOM_NUMBERS):
            if idx < len(reservations):
                res = reservations[idx]
                guest = res["guest_name"]
                name = str(guest) if guest else "No Show"
                gno = str(res["reservation_id"])
                arr = str(res["arrival_date"]).replace("-", "")[2:]
                dep = str(res["departure_date"]).replace("-", "")[2:]
            else:  # pragma: no cover - generator always yields enough rows
                name, gno, arr, dep = "No Show", f"RES-{idx:06d}", "261001", "261003"
            self._rooms[rn] = _Room(
                rn=rn,
                guest_name=name,
                reservation_no=gno,
                arrival=arr,
                departure=dep,
                language=LANGUAGES[idx % len(LANGUAGES)],
            )

    def reset_faults(self) -> None:
        """Admin reset: baseline faults, fresh P# counter and posting plan."""
        with self.lock:
            self.faults = LinkFaults()
            self._posting_seq = 0
            self._pending.clear()
            self._unacked.clear()
            self._stay_queued = False
            self._wakeups.clear()
            self._awaiting_dbswap = False
            self._swapped_rooms.clear()
            self._rng = random.Random(self.config.seed)
            self._seed_rooms()

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._halt.set()
        # Join here (not just set the flag): a test that returns while the
        # link thread is still connecting or serving leaks the thread, and
        # any exception it raises afterwards surfaces as an unhandled
        # thread exception — fatal for forked children on macOS. Never
        # called from the link thread itself (only fixtures/tests/app).
        if self.is_alive():
            self.join(timeout=15.0)

    def run(self) -> None:  # noqa: D102
        while not self._halt.is_set():
            try:
                self._serve_one_link()
            except _LinkDropped:
                pass
            except Exception:  # pragma: no cover - defensive: never kill the thread
                with self.lock:
                    self.counters["links_dropped"] += 1
                self.link_up = False
            if not self._halt.is_set():
                self._halt.wait(self.config.reconnect_interval)

    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.config.host, self.config.port), timeout=5.0)
        sock.settimeout(0.2)
        return sock

    def _serve_one_link(self) -> None:
        sock = self._connect()
        # One splitter per connection: partial frames and coalesced bytes
        # must survive across individual send-and-wait calls — a fresh
        # splitter per call would silently swallow an oversize frame split
        # over several reads (and any coalesced peer frame).
        self._splitter = FrameSplitter()
        try:
            self._handshake(sock)
            with self.lock:
                self.link_up = True
                self.links_established += 1
                self.counters["handshakes"] += 1
                resync = self.faults.resync_on_reconnect
            if resync:
                self._run_dbswap(sock)
            self._requeue_unacked()
            self._queue_stay_postings()
            self._pump_burst_postings()
            self._serve(sock)
        finally:
            with self.lock:
                self.link_up = False
            with contextlib.suppress(OSError):
                sock.close()

    # ------------------------------------------------------------------
    # Handshake (§2.2, PABX side)
    # ------------------------------------------------------------------

    def _send(self, sock: socket.socket, record_type: str, fields: list[tuple[str, str]]) -> None:
        frame = encode_frame(record_type, fields)
        sock.sendall(frame)
        with self.lock:
            self.counters["frames_out"] += 1

    def _send_and_wait_ack(
        self, sock: socket.socket, record_type: str, fields: list[tuple[str, str]]
    ) -> None:
        """Send a frame; resend on NAK/timeout (3 tries), then drop the link.

        A peer frame arriving while we wait is ACKed and dispatched; only an
        ACK for our frame completes the wait. A closed socket drops the link
        immediately (never spins on ``b""``).
        """
        frame = encode_frame(record_type, fields)
        splitter = self._splitter
        sock.sendall(frame)
        with self.lock:
            self.counters["frames_out"] += 1
        deadline = time.monotonic() + self.config.ack_wait
        attempts = 0
        while True:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                chunk = None
            if chunk == b"":
                raise _LinkDropped("peer closed while awaiting ACK")
            if chunk:
                deadline = time.monotonic() + self.config.ack_wait
                resend_now = False
                acked = False
                try:
                    items = splitter.feed(chunk)
                except OversizeFrameError:
                    with self.lock:
                        self.counters["oversize_closed"] += 1
                    raise _LinkDropped("oversize frame: session closed") from None
                for item in items:
                    if item.kind == "ack":
                        with self.lock:
                            self.counters["acks_in"] += 1
                        acked = True
                    elif item.kind == "nak":
                        with self.lock:
                            self.counters["naks_in"] += 1
                        resend_now = True  # peer rejected it: resend now
                        break
                    elif item.kind == "frame" and item.record is not None:
                        # Tolerant: a peer frame arriving mid-wait is
                        # ACKed/handled; every item in the chunk is
                        # processed — returning on the ACK first would
                        # drop a coalesced peer frame (lost command).
                        self._dispatch_inbound(sock, item.record)
                    elif item.kind == "bad":
                        self._raw(sock, bytes((NAK,)))
                    # zeros: host-side line noise, discarded (§1).
                if acked:
                    return
                if not resend_now and time.monotonic() < deadline:
                    continue
            elif time.monotonic() < deadline:
                continue  # poll tick: no data yet, keep waiting
            attempts += 1
            with self.lock:
                self.counters["resends"] += 1
            if attempts > self.config.max_retries:
                raise _LinkDropped(f"no ACK for {record_type} after {attempts} tries")
            sock.sendall(frame)
            with self.lock:
                self.counters["frames_out"] += 1
            deadline = time.monotonic() + self.config.ack_wait

    def _read_frame(self, sock: socket.socket, timeout: float) -> Record:
        """Read the next peer frame (ACK/NAK/zeros handled inline)."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _LinkDropped("handshake read timeout")
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            if chunk == b"":
                raise _LinkDropped("peer closed during handshake")
            try:
                items = self._splitter.feed(chunk)
            except OversizeFrameError:
                with self.lock:
                    self.counters["oversize_closed"] += 1
                raise _LinkDropped("oversize frame in handshake") from None
            for item in items:
                if item.kind == "frame" and item.record is not None:
                    with self.lock:
                        self.counters["frames_in"] += 1
                    return item.record
                if item.kind == "bad":
                    self._raw(sock, bytes((NAK,)))
                elif item.kind == "zeros":
                    pass  # line noise from the host side is discarded (§1)
                # ACK/NAK outside a handshake read is ignored here.

    def _raw(self, sock: socket.socket, data: bytes) -> None:
        try:
            sock.sendall(data)
        except OSError:
            raise _LinkDropped("send failed") from None

    def _expect(self, sock: socket.socket, want: str) -> Record:
        rec = self._read_frame(sock, self.config.ack_wait * 2)
        if rec.record_type != want:
            self._raw(sock, bytes((NAK,)))
            raise _LinkDropped(f"handshake: want {want}, got {rec.record_type}")
        self._raw(sock, bytes((ACK,)))
        return rec

    def _current_layouts(self) -> dict[str, list[str]]:
        with self.lock:
            v2 = self.faults.ps_layout == "layout_v2"
        layouts = dict(BASELINE_LAYOUTS)
        if v2:
            layouts["PS"] = list(LAYOUT_V2_PS)
        return layouts

    def _handshake(self, sock: socket.socket) -> None:
        # 1. host LS -> ACK.
        self._expect(sock, "LS")
        # 2. our LS -> host ACK (retried).
        now = self._now()
        self._send_and_wait_ack(sock, "LS", [("DA", _wire_date(now)), ("TI", _wire_time(now))])
        # 3. host LD -> ACK (any interface version accepted).
        self._expect(sock, "LD")
        # 4. host LR records (one per wanted type) until its LA.
        while True:
            rec = self._read_frame(sock, self.config.ack_wait * 2)
            if rec.record_type == "LA":
                self._raw(sock, bytes((ACK,)))
                break
            if rec.record_type == "LR":
                self._raw(sock, bytes((ACK,)))
                continue
            self._raw(sock, bytes((NAK,)))
            raise _LinkDropped(f"handshake: want LR/LA, got {rec.record_type}")
        # 5. our LR records (site extension §2.2: the PABX's layout wins).
        for rtype, fields in self._current_layouts().items():
            self._send_and_wait_ack(sock, "LR", [("RI", rtype), ("FL", "".join(fields))])
        # 6. our LA -> host ACK: link is up.
        now = self._now()
        self._send_and_wait_ack(sock, "LA", [("DA", _wire_date(now)), ("TI", _wire_time(now))])

    # ------------------------------------------------------------------
    # Database swap (§3.4 DR / §4 DS+GI+DE)
    # ------------------------------------------------------------------

    def _run_dbswap(self, sock: socket.socket) -> None:
        now = self._now()
        with self.lock:
            self._awaiting_dbswap = True
            self._swapped_rooms.clear()
        self._send_and_wait_ack(sock, "DR", [("DA", _wire_date(now)), ("TI", _wire_time(now))])
        deadline = time.monotonic() + self.config.de_wait
        try:
            ds = self._read_frame(sock, self.config.de_wait)
            if ds.record_type != "DS":
                self._raw(sock, bytes((NAK,)))
                return
            self._raw(sock, bytes((ACK,)))
            while time.monotonic() < deadline:
                rec = self._read_frame(sock, deadline - time.monotonic())
                if rec.record_type == "DE":
                    self._raw(sock, bytes((ACK,)))
                    break
                if rec.record_type == "GI":
                    self._apply_gi(rec)
                    self._raw(sock, bytes((ACK,)))
                    continue
                self._raw(sock, bytes((NAK,)))
        except _LinkDropped:
            raise
        finally:
            with self.lock:
                self._awaiting_dbswap = False

    def _apply_gi(self, rec: Record) -> None:
        rn = rec.field("RN")
        if not rn:
            return
        with self.lock:
            room = self._rooms.get(rn)
            if room is None:
                room = _Room(rn=rn, guest_name=None, reservation_no="", arrival="", departure="",
                             language="en")
                self._rooms[rn] = room
            room.guest_name = _last_first(rec.field("GN")) if rec.field("GN") else room.guest_name
            room.reservation_no = rec.field("G#", room.reservation_no)
            room.arrival = rec.field("GA", room.arrival)
            room.departure = rec.field("GD", room.departure)
            room.language = rec.field("GL", room.language) or room.language
            room.occupied = True
            self._swapped_rooms.add(rn)

    # ------------------------------------------------------------------
    # Postings (PABX -> host §3.1, host answers PA §4.6)
    # ------------------------------------------------------------------

    def _next_posting(self, room: _Room, minibar: bool) -> Record:
        """Build the next PS posting for *room* (P# increments, wraps).

        Caller must hold :attr:`lock` (called from the stay planner and
        the burst pump while they hold it).
        """
        self._posting_seq = (self._posting_seq % 999999) + 1
        seq = self._posting_seq
        now = self._now()
        if minibar:
            amount = self._rng.choice([350, 450, 550, 750, 1200])
            outlet, dd, du = MINIBAR_OUTLET, "", ""
        else:
            amount = self._rng.choice([1250, 2340, 890, 3120, 1575])
            outlet = CALL_OUTLET
            dd = "004930" + "".join(str(self._rng.randint(0, 9)) for _ in range(6)) + "XXXXXX"
            du = f"00{self._rng.randint(0, 59):02d}{self._rng.randint(0, 59):02d}"
        values = {
            "RN": room.rn,
            "P#": f"{seq:06d}",
            "DA": _wire_date(now),
            "TI": _wire_time(now),
            "TA": str(amount),
            "DD": dd,
            "DU": du,
            "SO": outlet,
            "CX": "VOICE" if outlet == CALL_OUTLET else "SNACK",
        }
        layout = self._current_layouts_unlocked()["PS"]
        return Record("PS", tuple((fid, values[fid]) for fid in layout))

    def _queue_stay_postings(self) -> None:
        """Queue one posting per occupied room at link start (deterministic).

        Planned once per process: a resync replays un-PA'd postings (same
        P# — §2.3) instead of duplicating the plan.
        """
        with self.lock:
            if self._stay_queued:
                return
            self._stay_queued = True
            rooms = sorted(self._rooms.values(), key=lambda r: r.rn)
            for idx, room in enumerate(rooms):
                if room.occupied:
                    self._pending.append(self._next_posting(room, minibar=(idx % 2 == 1)))

    def _requeue_unacked(self) -> None:
        """Resend-after-resync (§2.3): un-PA'd postings go back to the queue.

        Same P#s, oldest first; never duplicated when the link flaps again
        before the host answers. Shaping to the announced layout happens at
        send time (a firmware update between links must also reshape stay
        postings that were queued but never sent).
        """
        with self.lock:
            pending_pnos = {r.field("P#") for r in self._pending}
            replay: list[Record] = []
            for pno in sorted(self._unacked, key=lambda p: int(self._unacked[p]["seq"])):  # type: ignore[arg-type]
                if pno in pending_pnos:
                    continue
                info = self._unacked[pno]
                assert isinstance(info["record"], Record)
                replay.append(info["record"])
                pending_pnos.add(pno)
            # Replays go first (oldest un-PA'd first); never-sent stay
            # postings keep their relative order behind them.
            self._pending[:] = replay + self._pending

    def _ps_in_layout(self, rec: Record, layout: list[str]) -> Record:
        if list(rec.field_ids()) == layout:
            return rec
        vals = {fid: value for fid, value in rec.fields}
        if "CX" not in vals:
            outlet = vals.get("SO", CALL_OUTLET)
            vals["CX"] = "VOICE" if outlet == CALL_OUTLET else "SNACK"
        return Record("PS", tuple((fid, vals.get(fid, "")) for fid in layout))

    def _pump_burst_postings(self) -> None:
        """Queue admin-requested burst postings (acquires the lock itself)."""
        with self.lock:
            faults = self.faults
            burst = faults.burst_postings
            faults.burst_postings = 0
        for _ in range(max(0, min(burst, 200))):
            with self.lock:
                rooms = sorted((r for r in self._rooms.values() if r.occupied), key=lambda r: r.rn)
                if not rooms:
                    break
                room = rooms[self._rng.randrange(len(rooms))]
                minibar = self._rng.random() < 0.3
                self._pending.append(self._next_posting(room, minibar=minibar))

    # ------------------------------------------------------------------
    # Serve loop (link is up)
    # ------------------------------------------------------------------

    def _serve(self, sock: socket.socket) -> None:
        splitter = self._splitter
        last_out = time.monotonic()
        while not self._halt.is_set():
            # One-shot faults.
            with self.lock:
                zeros = self.faults.send_zeros_then_drop
                drop = self.faults.drop_link
                if zeros or drop:
                    self.faults.send_zeros_then_drop = False
                    self.faults.drop_link = False
            if zeros:
                self._raw(sock, DEFAULT_ZERO_BURST)
                with self.lock:
                    self.counters["links_dropped"] += 1
                raise _LinkDropped("simulated interface restart (zeros)")
            if drop:
                with self.lock:
                    self.counters["links_dropped"] += 1
                raise _LinkDropped("admin drop_link")
            # Due wake-ups fire WA (fire-and-wait-ACK like any business record).
            self._fire_due_wakeups(sock)
            # Business output: one record at a time, each ACKed (§4 intro).
            self._pump_burst_postings()
            with self.lock:
                awaiting_swap = self._awaiting_dbswap
                pending = self._pending[:1]
            if pending and not awaiting_swap:
                rec = pending[0]
                if rec.record_type == "PS":
                    # Shape to the announced layout at send time: a layout
                    # change between links reshapes replays and never-sent
                    # stay postings alike (same P#, amounts, timestamps).
                    rec = self._ps_in_layout(rec, self._current_layouts()["PS"])
                self._send_record_and_wait_ack(sock, rec)
                with self.lock:
                    pno = rec.field("P#")
                    if self._pending and self._pending[0].field("P#") == pno:
                        self._pending.pop(0)
                    if rec.record_type == "PS" and pno:
                        self._unacked[pno] = {
                            "record": rec,
                            "last": time.monotonic(),
                            "seq": int(pno),
                        }
                        self.counters["postings_sent"] += 1
                last_out = time.monotonic()
            # Resend PS records the host never answered with PA (§3.1).
            self._resend_unacked(sock)
            # Inbound with a short poll so timers stay live.
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                chunk = None
            if chunk is None:
                pass  # idle tick
            elif chunk == b"":
                raise _LinkDropped("peer closed the connection")
            else:
                try:
                    items = splitter.feed(chunk)
                except OversizeFrameError:
                    # §1: a longer frame is a protocol error — close the
                    # connection (no LE: the peer is already misbehaving).
                    with self.lock:
                        self.counters["oversize_closed"] += 1
                        self.counters["links_dropped"] += 1
                    raise _LinkDropped("oversize frame: session closed") from None
                for item in items:
                    if item.kind == "frame" and item.record is not None:
                        with self.lock:
                            self.counters["frames_in"] += 1
                        try:
                            self._dispatch_inbound(sock, item.record)
                        except _LinkDropped:
                            raise
                        last_out = time.monotonic()
                    elif item.kind == "bad":
                        self._raw(sock, bytes((NAK,)))
                        last_out = time.monotonic()
                    elif item.kind == "zeros":
                        pass  # host-side zeros are line noise (§1)
                    # ACK/NAK arriving outside _send_and_wait_ack: ignore.
            # Keep-alive when idle; dead link detection (§2.3).
            now_mono = time.monotonic()
            if now_mono - last_out >= self.config.keepalive_interval:
                try:
                    moment = self._now()
                    self._send_and_wait_ack(sock, "LA", [("DA", _wire_date(moment)), ("TI", _wire_time(moment))])
                except _LinkDropped:
                    raise
                last_out = time.monotonic()
            if now_mono - last_out >= self.config.dead_after:
                with self.lock:
                    self.counters["links_dropped"] += 1
                raise _LinkDropped("dead link: no frame for 30s")

    def _send_record_and_wait_ack(self, sock: socket.socket, rec: Record) -> None:
        self._send_and_wait_ack(sock, rec.record_type, list(rec.fields))

    def _resend_unacked(self, sock: socket.socket) -> None:
        now_mono = time.monotonic()
        with self.lock:
            due = [pno for pno, info in self._unacked.items()
                   if now_mono - float(info["last"]) >= self.config.resend_interval]
        for pno in due:
            with self.lock:
                info = self._unacked.get(pno)
                if info is None:
                    continue
                record = info["record"]
                info["last"] = now_mono
            assert isinstance(record, Record)
            try:
                shaped = self._ps_in_layout(record, self._current_layouts()["PS"])
                self._send_record_and_wait_ack(sock, shaped)
                with self.lock:
                    self.counters["postings_resent"] += 1
            except _LinkDropped:
                raise

    # ------------------------------------------------------------------
    # Inbound dispatch (host -> PABX §4, plus link control §2)
    # ------------------------------------------------------------------

    def _dispatch_inbound(self, sock: socket.socket, rec: Record) -> None:
        with self.lock:
            silent = rec.record_type in self.faults.silent_types
        if silent:
            with self.lock:
                self.counters["silent_dropped"] += 1
            return  # ACK-timeout fault: never answer, never drop (§D)
        rtype = rec.record_type
        if rtype == "LA":
            self._raw(sock, bytes((ACK,)))
        elif rtype == "LE":
            self._raw(sock, bytes((ACK,)))
            with self.lock:
                self.counters["links_dropped"] += 1
            raise _LinkDropped("peer LE")
        elif rtype == "LS":
            # Peer restarts the handshake from LS (§2.3 resync).
            self._raw(sock, bytes((ACK,)))
            self._handshake_from_ls(sock)
        elif rtype in ("GI", "GC"):
            self._apply_guest_change(rec)
            self._raw(sock, bytes((ACK,)))
        elif rtype == "GO":
            self._apply_checkout(rec)
            self._raw(sock, bytes((ACK,)))
        elif rtype == "WR":
            self._apply_wakeup_request(sock, rec)
        elif rtype == "WC":
            with self.lock:
                self._wakeups.pop((rec.field("RN"), rec.field("DA"), rec.field("TI")), None)
            self._raw(sock, bytes((ACK,)))
        elif rtype in ("XL", "XD"):
            self._raw(sock, bytes((ACK,)))
        elif rtype in ("KR", "KD"):
            self._answer_key(sock, rec)
        elif rtype == "KZ":
            self._answer_key_read(sock, rec)
        elif rtype == "PA":
            self._apply_pa(rec)
            self._raw(sock, bytes((ACK,)))
        elif rtype in ("DS", "DE"):
            # Only meaningful inside a database swap we did not start:
            # accept and ACK, ignore otherwise.
            self._raw(sock, bytes((ACK,)))
        elif rtype in ("PS", "RE", "WA", "DR", "LD", "LR", "KA"):
            # PABX-originated (or handshake-link) records never come from
            # the host on a live link: NAK, never guess.
            self._raw(sock, bytes((NAK,)))
        else:
            self._raw(sock, bytes((NAK,)))

    def _handshake_from_ls(self, sock: socket.socket) -> None:
        """Continue a peer-initiated restart: our LS … our LA (link stays)."""
        now = self._now()
        self._send_and_wait_ack(sock, "LS", [("DA", _wire_date(now)), ("TI", _wire_time(now))])
        self._expect(sock, "LD")
        while True:
            rec = self._read_frame(sock, self.config.ack_wait * 2)
            if rec.record_type == "LA":
                self._raw(sock, bytes((ACK,)))
                break
            if rec.record_type == "LR":
                self._raw(sock, bytes((ACK,)))
                continue
            self._raw(sock, bytes((NAK,)))
            raise _LinkDropped(f"re-handshake: want LR/LA, got {rec.record_type}")
        for rtype, fields in self._current_layouts().items():
            self._send_and_wait_ack(sock, "LR", [("RI", rtype), ("FL", "".join(fields))])
        now = self._now()
        self._send_and_wait_ack(sock, "LA", [("DA", _wire_date(now)), ("TI", _wire_time(now))])

    def _apply_guest_change(self, rec: Record) -> None:
        rn = rec.field("RN")
        if not rn:
            return
        with self.lock:
            if rec.record_type == "GC" and rec.field("RO"):
                old = self._rooms.pop(rec.field("RO"), None)
                if old is not None:
                    old.occupied = False
            room = self._rooms.get(rn)
            if room is None:
                room = _Room(rn=rn, guest_name=None, reservation_no="", arrival="", departure="",
                             language="en")
                self._rooms[rn] = room
            if rec.field("GN"):
                room.guest_name = _last_first(rec.field("GN"))
            room.reservation_no = rec.field("G#", room.reservation_no)
            room.arrival = rec.field("GA", room.arrival)
            room.departure = rec.field("GD", room.departure)
            room.language = rec.field("GL", room.language) or room.language
            room.occupied = True

    def _apply_checkout(self, rec: Record) -> None:
        with self.lock:
            room = self._rooms.get(rec.field("RN"))
            if room is not None:
                room.occupied = False
                room.guest_name = None

    def _apply_wakeup_request(self, sock: socket.socket, rec: Record) -> None:
        rn, da, ti = rec.field("RN"), rec.field("DA"), rec.field("TI")
        key = (rn, da, ti)
        try:
            want = datetime.datetime.strptime(da + ti, "%y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo(SITE_TIMEZONE))
        except ValueError:
            want = None
        if want is None or want <= self._now():
            # Past (or unparseable) wake-up: answered UR, never scheduled.
            self._send_and_wait_ack(sock, "WA", [("RN", rn), ("DA", da), ("TI", ti), ("AS", "UR")])
            return
        with self.lock:
            self._wakeups[key] = None
        self._raw(sock, bytes((ACK,)))

    def _fire_due_wakeups(self, sock: socket.socket) -> None:
        now = self._now().replace(second=0, microsecond=0)
        with self.lock:
            due = [k for k in self._wakeups if self._parse_wire_dt(k[1], k[2]) <= now]
            for k in due:
                del self._wakeups[k]
        for rn, da, ti in due:
            try:
                self._send_record_and_wait_ack(
                    sock, Record("WA", (("RN", rn), ("DA", da), ("TI", ti), ("AS", "OK"))))
                with self.lock:
                    self.counters["wakeups_fired"] += 1
            except _LinkDropped:
                with self.lock:
                    self._wakeups[(rn, da, ti)] = None  # retry after reconnect
                raise

    @staticmethod
    def _parse_wire_dt(da: str, ti: str) -> datetime.datetime:
        try:
            return datetime.datetime.strptime(da + ti, "%y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo(SITE_TIMEZONE))
        except ValueError:
            return datetime.datetime.max.replace(tzinfo=ZoneInfo(SITE_TIMEZONE))

    def _answer_key(self, sock: socket.socket, rec: Record) -> None:
        now = self._now()
        # KR/KD are always answered with KA, even when ACK mode is off (§4.4).
        self._send_and_wait_ack(
            sock, "KA",
            [("KC", rec.field("KC", "01")), ("AS", "OK"),
             ("DA", _wire_date(now)), ("TI", _wire_time(now))],
        )

    def _answer_key_read(self, sock: socket.socket, rec: Record) -> None:
        now = self._now()
        with self.lock:
            card = dict(self._key_card)
        room = self._rooms.get(card["RN"])
        gno = room.reservation_no if room else card["G#"]
        ga = room.arrival if room else ""
        gd = room.departure if room else ""
        self._send_and_wait_ack(
            sock, "KZ",
            [("KC", rec.field("KC", card["KC"])), ("RN", card["RN"]), ("G#", gno),
             ("GA", ga), ("GD", gd),
             ("DA", _wire_date(now)), ("TI", _wire_time(now))],
        )

    def _apply_pa(self, rec: Record) -> None:
        pno = rec.field("P#")
        with self.lock:
            if pno in self._unacked:
                del self._unacked[pno]
                if rec.field("AS") == "OK":
                    self.counters["pa_ok"] += 1
                else:
                    self.counters["pa_not_posted"] += 1
            else:
                self.counters["duplicates_seen_by_host"] += 1

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """JSON-safe admin view of link state (no secrets, no key material)."""
        with self.lock:
            return {
                "link_up": self.link_up,
                "host": f"{self.config.host}:{self.config.port}",
                "links_established": self.links_established,
                "rooms": {
                    rn: {"guest": r.guest_name, "occupied": r.occupied,
                         "reservation": r.reservation_no}
                    for rn, r in sorted(self._rooms.items())
                },
                "pending_postings": len(self._pending),
                "unacked": sorted(self._unacked),
                "posting_seq": self._posting_seq,
                "scheduled_wakeups": len(self._wakeups),
                "ps_layout": self.faults.ps_layout,
                "ps_field_list": "".join(self._current_layouts_unlocked()["PS"]),
                "counters": dict(self.counters),
                "faults": {
                    "ps_layout": self.faults.ps_layout,
                    "send_zeros_then_drop": self.faults.send_zeros_then_drop,
                    "drop_link": self.faults.drop_link,
                    "silent_types": list(self.faults.silent_types),
                    "resync_on_reconnect": self.faults.resync_on_reconnect,
                    "burst_postings": self.faults.burst_postings,
                },
            }

    def _current_layouts_unlocked(self) -> dict[str, list[str]]:
        layouts = dict(BASELINE_LAYOUTS)
        if self.faults.ps_layout == "layout_v2":
            layouts["PS"] = list(LAYOUT_V2_PS)
        return layouts
