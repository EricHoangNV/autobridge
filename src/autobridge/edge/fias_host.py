"""Host-side FIAS link engine (REQ-022 sub-step B).

The on-premise agent listens on TCP (default ``:5010`` — the site setting)
and the PABX dials out to it. This engine is the host mirror of
``simulators/fias_pabx/pabx.py``, implemented from the same public spec
(``simulators/docs/fias-pabx.md``) without importing it — the engine
package must never import ``simulators`` (DIRECTIVES §2), so the wire
codec below is a small, self-contained re-implementation with the spec
sections cited.

Link lifecycle (§2.2, host side):

1. accept one connection (a second connection while a link is up is
   closed immediately — §5: one link at a time);
2. handshake: send ``LS`` → read PABX ``LS`` → send ``LD`` → send own
   ``LR`` records (the types this agent consumes) + ``LA`` → read the
   PABX's ``LR`` records (site extension §2.2: its layout wins — stored
   in :attr:`peer_layouts`, the drift surface for REQ-022 D) + ``LA``;
3. serve: every inbound ``PS``/``RE``/``WA``/``DR`` is durably written
   through the :class:`LinkSink` *before* its ACK goes out (a slow cloud
   never stalls the PABX — REQ-022 B), ``PS`` is then answered with
   ``PA``; cloud commands go out one at a time with a bounded wait;
4. supervision: keep-alive ``LA`` when idle, dead-link detection,
   all-zero resync (§2.3) via a fresh handshake, oversize frames and
   identical-record floods close the session (the PABX redials).

Outcomes for one command: ``sent`` (ACK, or the expected answer record
for ``KR``/``KD``/``KZ``), ``ack_timeout`` (bounded wait exceeded — the
link stays up; keep-alive proves liveness), ``link_down`` (the session
died mid-command — retried on the new link, never broadcast), or
``expired`` (the serve loop drops it before transmitting).
"""

from __future__ import annotations

import contextlib
import datetime
import functools
import logging
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol
from zoneinfo import ZoneInfo

log = logging.getLogger("autobridge.edge.fias")


def _contain_thread_errors(fn: Any) -> Any:
    """A link thread never raises: a session ending is a normal return.

    Expected session ends surface as :class:`LinkDropped` (peer closed,
    oversize/flood session close, resync, dead link) and are logged at
    debug; anything else is logged with a traceback and contained. An
    uncaught thread exception becomes a ``PytestUnhandledThreadException``
    in the suite and poisons forked children on macOS (fork-safety crash),
    so containment here is load-bearing, not cosmetic.
    """

    @functools.wraps(fn)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(self, *args, **kwargs)
        except LinkDropped as exc:
            log.debug("link session ended: %s", exc)
            return None
        except Exception:
            log.exception("fias host thread error (contained)")
            return None

    return wrapper

STX = 0x02
ETX = 0x03
ACK = 0x06
NAK = 0x15
ZERO = 0x00

#: Site setting (§1): maximum frame length, payload bytes between STX/ETX.
MAX_FRAME_LEN = 64 * 1024

#: Record types the host announces interest in (§2.2 host ``LR``).
HOST_WANTED_TYPES = ("PS", "RE", "WA", "DR")

#: Default property timezone for wire timestamps (site setting).
DEFAULT_TZ = "America/New_York"


class FrameError(ValueError):
    """A frame could not be parsed (receiver answers NAK)."""


class OversizeFrameError(FrameError):
    """A frame exceeded the 64 KB site limit (receiver closes the session)."""


class LinkDropped(Exception):
    """Internal: the current session is dead; accept the next one."""


@dataclass(frozen=True)
class Record:
    """One parsed FIAS record: two-letter type plus ordered (id, value) fields."""

    record_type: str
    fields: tuple[tuple[str, str], ...] = ()

    def field(self, field_id: str, default: str = "") -> str:
        for fid, value in self.fields:
            if fid == field_id:
                return value
        return default

    def as_dict(self) -> dict[str, str]:
        return {fid: value for fid, value in self.fields}


def encode_frame(record_type: str, fields: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> bytes:
    """Encode a record to wire bytes (STX … ETX)."""
    if len(record_type) != 2 or not record_type.isascii():
        raise FrameError(f"record type must be 2 ASCII chars, got {record_type!r}")
    parts = [record_type, "|"]
    for fid, value in fields:
        if len(fid) != 2 or not fid.isascii():
            raise FrameError(f"field id must be 2 ASCII chars, got {fid!r}")
        if not value.isascii() or any(c in value for c in "|\x00\x02\x03\x06\x15"):
            raise FrameError(f"field {fid} value is not wire-safe: {value!r}")
        parts.extend((fid, value, "|"))
    body = "".join(parts).encode("ascii")
    if len(body) > MAX_FRAME_LEN:
        raise OversizeFrameError(f"frame body {len(body)} bytes exceeds {MAX_FRAME_LEN}")
    return bytes((STX,)) + body + bytes((ETX,))


def parse_frame(body: bytes) -> Record:
    """Parse the bytes between STX and ETX (unknown field ids are kept)."""
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FrameError(f"frame is not ASCII: {exc}") from exc
    if any(ord(c) < 0x20 for c in text):
        raise FrameError("frame contains control characters")
    if "|" not in text:
        raise FrameError(f"frame has no field separator: {text!r}")
    head, _, rest = text.partition("|")
    if len(head) != 2:
        raise FrameError(f"record type must be 2 chars, got {head!r}")
    if not rest.endswith("|"):
        raise FrameError(f"frame must end with '|': {text!r}")
    fields = []
    for chunk in rest[:-1].split("|"):
        if len(chunk) < 2:
            raise FrameError(f"field chunk too short: {chunk!r}")
        fields.append((chunk[:2], chunk[2:]))
    return Record(record_type=head, fields=tuple(fields))


@dataclass
class SplitResult:
    kind: str  # "frame" | "bad" | "ack" | "nak" | "zeros"
    record: Record | None = None


class FrameSplitter:
    """Incremental byte-stream splitter (§1 framing + §2.3 resync signal)."""

    def __init__(self, max_frame_len: int = MAX_FRAME_LEN) -> None:
        self._max = max_frame_len
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[SplitResult]:
        out: list[SplitResult] = []
        for byte in data:
            if self._buf and self._buf[0] == ZERO:
                if byte == ZERO:
                    self._buf.append(byte)
                    continue
                out.append(SplitResult(kind="zeros"))
                self._buf.clear()
            if not self._buf:
                if byte == STX:
                    self._buf.append(byte)
                elif byte in (ACK, NAK):
                    out.append(SplitResult(kind="ack" if byte == ACK else "nak"))
                elif byte == ZERO:
                    self._buf.append(byte)
                continue
            self._buf.append(byte)
            if len(self._buf) - 1 > self._max + 1:
                self._buf.clear()
                raise OversizeFrameError(f"frame exceeds {self._max} bytes")
            if byte == ETX:
                body = bytes(self._buf[1:-1])
                self._buf.clear()
                try:
                    out.append(SplitResult(kind="frame", record=parse_frame(body)))
                except FrameError:
                    out.append(SplitResult(kind="bad", record=None))
        return out

    def flush(self) -> list[SplitResult]:
        if self._buf and self._buf[0] == ZERO:
            self._buf.clear()
            return [SplitResult(kind="zeros")]
        return []


def _wire_now(tz_name: str) -> tuple[str, str]:
    try:
        now = datetime.datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.datetime.now(datetime.UTC)
    return now.strftime("%y%m%d"), now.strftime("%H%M%S")


class LinkSink(Protocol):
    """Durable-write boundary between the link and the cloud path.

    :meth:`on_record` runs on the link thread and must be fast and local
    (SQLite write): the ACK goes out only after it returns, so a slow
    cloud never stalls the PABX. It returns the ``PA`` answer status for
    ``PS`` records (``"OK"`` when durably stored); other records ignore
    the return value.
    """

    def on_record(self, record: Record) -> str: ...


@dataclass
class CommandSlot:
    record_type: str
    fields: list[tuple[str, str]]
    expect: str  # "ack" or an answer record type
    expires_at: str | None
    event: threading.Event = field(default_factory=threading.Event)
    outcome: str = ""
    answer: Record | None = None


class FiasHost:
    """A host-side FIAS link engine running its listener in a thread."""

    def __init__(
        self,
        sink: LinkSink,
        *,
        listen_host: str = "0.0.0.0",
        listen_port: int = 5010,
        timezone: str = DEFAULT_TZ,
        keepalive_interval: float = 5.0,
        dead_after: float = 30.0,
        ack_wait: float = 5.0,
        max_retries: int = 3,
        max_identical: int = 100,
        max_frame_len: int = MAX_FRAME_LEN,
    ) -> None:
        self._sink = sink
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._tz = timezone
        self._keepalive_interval = keepalive_interval
        self._dead_after = dead_after
        self._ack_wait = ack_wait
        self._max_retries = max_retries
        self._max_identical = max_identical
        self._max_frame_len = max_frame_len
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self._session_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cmd_queue: deque[CommandSlot] = deque()
        self._inflight: CommandSlot | None = None
        self._pa_pending: dict[str, tuple[str, str]] = {}  # P# -> (RN, P#)
        self.link_up = False
        self.links_accepted = 0
        self.links_dropped = 0
        self.last_handshake_at: str | None = None
        self.peer_layouts: dict[str, list[str]] = {}
        self.counters = {
            "frames_in": 0,
            "frames_out": 0,
            "acks_in": 0,
            "naks_in": 0,
            "pa_sent": 0,
            "commands_sent": 0,
            "commands_ack_timeout": 0,
            "resyncs": 0,
            "oversize_closed": 0,
            "flood_closed": 0,
        }

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._halt.clear()
        self._thread = threading.Thread(name="fias-host", daemon=True, target=self._listen)
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        # Unblock a link-idle accept() by connecting to ourselves.
        with _suppress_oserror():
            socket.create_connection((self._listen_host if self._listen_host != "0.0.0.0" else "127.0.0.1",
                                      self._bound_port()), timeout=1.0).close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        session = None
        with self._lock:
            session = self._session_thread
        if session is not None:
            session.join(timeout=5.0)

    def _bound_port(self) -> int:
        with self._lock:
            return int(getattr(self, "_actual_port", self._listen_port))

    def bound_port(self) -> int:
        """Actual TCP port bound (useful with ``listen_port=0`` in tests)."""
        return self._bound_port()

    # -- command path (poller thread) -----------------------------------

    def send_command(
        self,
        record_type: str,
        fields: list[tuple[str, str]],
        expect: str = "ack",
        timeout: float | None = None,
        expires_at: str | None = None,
    ) -> tuple[str, Record | None]:
        """Queue one command; block until it completes on the link.

        Returns ``(outcome, answer)`` with outcome ``sent`` / ``ack_timeout``
        / ``link_down`` / ``expired``. At most one command is in flight per
        link; concurrent callers serialize on the queue. A command whose
        ``expires_at`` has passed is dropped before transmitting (``expired``)
        — a stale wake-up is never sent (REQ-022 B).
        """
        slot = CommandSlot(record_type=record_type, fields=list(fields), expect=expect, expires_at=expires_at)
        with self._lock:
            self._cmd_queue.append(slot)
        # Bounded wait (REQ-022 B): the caller never blocks forever. When
        # the wait expires the slot was not picked up (no live session —
        # a picked-up slot always completes first via the shorter link
        # wait), so it is withdrawn and reported link_down; the poller
        # requeues it for the next session (at-least-once).
        if not slot.event.wait(timeout if timeout is not None else self._ack_wait + 5.0):
            with self._lock, contextlib.suppress(ValueError):
                # picked up in the race window; its completion wins
                self._cmd_queue.remove(slot)
            if not slot.event.is_set():
                slot.outcome = "link_down"
                slot.event.set()
        assert slot.outcome, "command slot completed without an outcome"
        return slot.outcome, slot.answer

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._cmd_queue) + (1 if self._inflight is not None else 0)

    def link_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "link_up": self.link_up,
                "last_handshake_at": self.last_handshake_at,
                "peer_layouts": {k: list(v) for k, v in self.peer_layouts.items()},
                "queue_depth": len(self._cmd_queue) + (1 if self._inflight is not None else 0),
                "links_accepted": self.links_accepted,
                "links_dropped": self.links_dropped,
                "counters": dict(self.counters),
            }

    # -- listener -------------------------------------------------------

    def _listen(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self._listen_host, self._listen_port))
        with self._lock:
            self._actual_port = server.getsockname()[1]
        server.listen(5)
        server.settimeout(0.5)
        try:
            while not self._halt.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                if self._halt.is_set():  # our own unblock connect in stop()
                    with _suppress_oserror():
                        conn.close()
                    break
                with self._lock:
                    session = self._session_thread
                    busy = session is not None and session.is_alive()
                if busy:
                    # §5: one link at a time — refuse the second connection
                    # immediately instead of letting it wait in the backlog.
                    with _suppress_oserror():
                        conn.close()
                    continue
                conn.settimeout(0.2)
                worker = threading.Thread(
                    name="fias-host-session", daemon=True,
                    target=self._serve_session, args=(conn,))
                with self._lock:
                    self._session_thread = worker
                worker.start()
        finally:
            with _suppress_oserror():
                server.close()

    def _serve_session(self, conn: socket.socket) -> None:
        try:
            self._serve_one_session(conn)
        except LinkDropped:
            pass
        except Exception:  # pragma: no cover - defensive: keep listening
            log.exception("fias host session error")
        finally:
            with self._lock:
                self.link_up = False
                if self._session_thread is threading.current_thread():
                    self._session_thread = None
            with _suppress_oserror():
                conn.close()

    # -- session --------------------------------------------------------

    def _serve_one_session(self, sock: socket.socket) -> None:
        splitter = FrameSplitter(self._max_frame_len)
        stashed: deque[Record] = deque()
        try:
            self._handshake(sock, splitter, stashed)
        except LinkDropped:
            raise
        with self._lock:
            self.link_up = True
            self.links_accepted += 1
            self.last_handshake_at = _utcnow()
        self._serve(sock, splitter)

    def _raw(self, sock: socket.socket, data: bytes) -> None:
        try:
            sock.sendall(data)
        except OSError:
            raise LinkDropped("send failed") from None

    def _send_frame(self, sock: socket.socket, record_type: str, fields: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> None:
        frame = encode_frame(record_type, list(fields))
        self._raw(sock, frame)
        with self._lock:
            self.counters["frames_out"] += 1

    def _read_frame(self, sock: socket.socket, splitter: FrameSplitter, timeout: float,
                    stashed: deque[Record]) -> Record:
        """Next peer frame (bare ACKs/NAKs consumed by transact waits)."""
        if stashed:
            return stashed.popleft()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LinkDropped("read timeout")
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            if chunk == b"":
                raise LinkDropped("peer closed")
            try:
                items = splitter.feed(chunk)
            except OversizeFrameError:
                with self._lock:
                    self.counters["oversize_closed"] += 1
                    self.links_dropped += 1
                raise LinkDropped("oversize frame: session closed") from None
            for item in items:
                if item.kind == "frame" and item.record is not None:
                    with self._lock:
                        self.counters["frames_in"] += 1
                    return item.record
                if item.kind == "bad":
                    self._raw(sock, bytes((NAK,)))
                elif item.kind == "zeros":
                    raise LinkDropped("zeros: peer out of sync")

    def _send_and_wait_ack(self, sock: socket.socket, splitter: FrameSplitter, stashed: deque[Record],
                           record_type: str, fields: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> None:
        """Send a frame; resend on NAK/timeout (bounded retries)."""
        frame = encode_frame(record_type, list(fields))
        self._raw(sock, frame)
        with self._lock:
            self.counters["frames_out"] += 1
        deadline = time.monotonic() + self._ack_wait
        attempts = 0
        while True:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                chunk = None
            if chunk == b"":
                raise LinkDropped("peer closed while awaiting ACK")
            if chunk:
                deadline = time.monotonic() + self._ack_wait
                acked = False
                try:
                    items = splitter.feed(chunk)
                except OversizeFrameError:
                    with self._lock:
                        self.counters["oversize_closed"] += 1
                    raise LinkDropped("oversize frame: session closed") from None
                for item in items:
                    if item.kind == "ack":
                        with self._lock:
                            self.counters["acks_in"] += 1
                        acked = True
                    elif item.kind == "nak":
                        with self._lock:
                            self.counters["naks_in"] += 1
                        break  # resend now
                    elif item.kind == "frame" and item.record is not None:
                        # A peer frame arriving mid-wait is stashed for the
                        # next _read_frame (handshake reads strictly alternate
                        # sends and expects, so nothing is lost).
                        with self._lock:
                            self.counters["frames_in"] += 1
                        stashed.append(item.record)
                    elif item.kind == "bad":
                        self._raw(sock, bytes((NAK,)))
                    # zeros: host-side line noise is discarded (§1).
                if acked:
                    return
                if time.monotonic() >= deadline:
                    pass  # fall through to resend below
                else:
                    continue
            elif time.monotonic() < deadline:
                continue
            attempts += 1
            if attempts > self._max_retries:
                raise LinkDropped(f"no ACK for {record_type} after {attempts} tries")
            self._raw(sock, frame)
            with self._lock:
                self.counters["frames_out"] += 1
            deadline = time.monotonic() + self._ack_wait

    def _expect(self, sock: socket.socket, splitter: FrameSplitter, stashed: deque[Record], want: str) -> Record:
        rec = self._read_frame(sock, splitter, self._ack_wait * 2, stashed)
        if rec.record_type != want:
            self._raw(sock, bytes((NAK,)))
            raise LinkDropped(f"handshake: want {want}, got {rec.record_type}")
        self._raw(sock, bytes((ACK,)))
        return rec

    def _handshake(self, sock: socket.socket, splitter: FrameSplitter, stashed: deque[Record]) -> None:
        # 1. our LS -> PABX ACK.
        da, ti = _wire_now(self._tz)
        self._send_and_wait_ack(sock, splitter, stashed, "LS", [("DA", da), ("TI", ti)])
        # 2. PABX LS -> ACK.
        self._expect(sock, splitter, stashed, "LS")
        # 3. our LD -> ACK.
        self._send_and_wait_ack(sock, splitter, stashed, "LD", [("DA", da), ("TI", ti)])
        # 4. our LR records (the types this agent consumes) + LA.
        for rtype in HOST_WANTED_TYPES:
            self._send_and_wait_ack(sock, splitter, stashed, "LR", [("RI", rtype)])
        self._send_and_wait_ack(sock, splitter, stashed, "LA", [("DA", da), ("TI", ti)])
        # 5. PABX LR records (site extension §2.2) until its LA.
        layouts: dict[str, list[str]] = {}
        while True:
            rec = self._read_frame(sock, splitter, self._ack_wait * 2, stashed)
            if rec.record_type == "LA":
                self._raw(sock, bytes((ACK,)))
                break
            if rec.record_type == "LR":
                self._raw(sock, bytes((ACK,)))
                announced = rec.field("RI")
                fields = rec.field("FL")
                if announced:
                    layouts[announced] = [fields[i:i + 2] for i in range(0, len(fields), 2) if len(fields[i:i + 2]) == 2]
                continue
            self._raw(sock, bytes((NAK,)))
            raise LinkDropped(f"handshake: want LR/LA, got {rec.record_type}")
        with self._lock:
            self.peer_layouts = layouts
            self.counters["resyncs"] += 0  # handshake counted by links_accepted

    # -- serve loop ------------------------------------------------------

    @_contain_thread_errors
    def _serve(self, sock: socket.socket, splitter: FrameSplitter) -> None:
        last_out = time.monotonic()
        last_identical: tuple[str, tuple[tuple[str, str], ...]] | None = None
        identical_run = 0
        stashed: deque[Record] = deque()

        def _handle(rec: Record) -> None:
            nonlocal last_identical, identical_run, last_out
            with self._lock:
                self.counters["frames_in"] += 1
            key = (rec.record_type, rec.fields)
            identical_run = identical_run + 1 if key == last_identical else 1
            last_identical = key
            if identical_run > self._max_identical:
                with self._lock:
                    self.counters["flood_closed"] += 1
                    self.links_dropped += 1
                self._fail_inflight("link_down")
                raise LinkDropped(f"flood: {identical_run} identical records")
            try:
                self._dispatch_inbound(sock, rec)
            except LinkDropped:
                raise
            last_out = time.monotonic()
        while not self._halt.is_set():
            # One queued command at a time (bounded wait per command).
            with self._lock:
                slot = self._cmd_queue.popleft() if self._cmd_queue and self._inflight is None else None
                if slot is not None:
                    self._inflight = slot
            if slot is not None:
                if _is_expired(slot.expires_at):
                    self._complete_slot(slot, "expired", None)
                    with self._lock:
                        self._inflight = None
                else:
                    try:
                        self._send_frame(sock, slot.record_type, slot.fields)
                        with self._lock:
                            self.counters["commands_sent"] += 1
                        # Wait for ACK (or the expected answer) inline: peer
                        # business frames arriving meanwhile are handled live.
                        self._await_slot(sock, splitter, slot)
                        last_out = time.monotonic()
                    except LinkDropped:
                        self._complete_slot(slot, "link_down", None)
                        raise
                    finally:
                        with self._lock:
                            self._inflight = None
            else:
                # No command to run: flush one deferred PA (idle moments
                # only, with its own ACK wait — see _flush_pa).
                try:
                    if self._flush_pa(sock, splitter, stashed):
                        last_out = time.monotonic()
                except LinkDropped:
                    self._fail_inflight("link_down")
                    raise
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                chunk = None
            if chunk is None:
                pass
            elif chunk == b"":
                raise LinkDropped("peer closed the connection")
            else:
                try:
                    items = splitter.feed(chunk)
                except OversizeFrameError:
                    with self._lock:
                        self.counters["oversize_closed"] += 1
                        self.links_dropped += 1
                    self._fail_inflight("link_down")
                    raise LinkDropped("oversize frame: session closed") from None
                for item in items:
                    if item.kind == "frame" and item.record is not None:
                        _handle(item.record)
                    elif item.kind == "bad":
                        self._raw(sock, bytes((NAK,)))
                        last_out = time.monotonic()
                    elif item.kind == "zeros":
                        # §2.3: the peer is out of sync — fresh handshake.
                        with self._lock:
                            self.counters["resyncs"] += 1
                        self._fail_inflight("link_down")
                        raise LinkDropped("zeros: peer out of sync")
                    elif item.kind in ("ack", "nak"):
                        self._on_wire_ack(item.kind == "ack")
            # Keep-alive when idle; dead link detection (§2.3). The wait
            # stashes peer frames into the session stash, drained below —
            # a PABX business record arriving during the wait is never lost.
            now_mono = time.monotonic()
            if now_mono - last_out >= self._keepalive_interval:
                try:
                    da, ti = _wire_now(self._tz)
                    self._send_and_wait_ack(sock, splitter, stashed, "LA", [("DA", da), ("TI", ti)])
                except LinkDropped:
                    self._fail_inflight("link_down")
                    raise
                last_out = time.monotonic()
            while stashed:
                _handle(stashed.popleft())
            if now_mono - last_out >= self._dead_after:
                with self._lock:
                    self.links_dropped += 1
                self._fail_inflight("link_down")
                raise LinkDropped("dead link: no frame answered")

    def _await_slot(self, sock: socket.socket, splitter: FrameSplitter, slot: CommandSlot) -> None:
        """Wait for a slot's ACK/answer with a bounded wait (one command)."""
        deadline = time.monotonic() + self._ack_wait
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    self.counters["commands_ack_timeout"] += 1
                self._complete_slot(slot, "ack_timeout", None)
                return
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            if chunk == b"":
                raise LinkDropped("peer closed awaiting command answer")
            try:
                items = splitter.feed(chunk)
            except OversizeFrameError:
                with self._lock:
                    self.counters["oversize_closed"] += 1
                raise LinkDropped("oversize frame awaiting command answer") from None
            for item in items:
                if item.kind == "ack":
                    with self._lock:
                        self.counters["acks_in"] += 1
                    if slot.expect == "ack":
                        self._complete_slot(slot, "sent", None)
                        return
                    deadline = time.monotonic() + self._ack_wait  # answer still due
                elif item.kind == "nak":
                    with self._lock:
                        self.counters["naks_in"] += 1
                    # The PABX rejected the frame outright: resend once via
                    # the normal path would loop on a deterministic NAK, so
                    # report it and let the cloud decide (never guess).
                    self._complete_slot(slot, "rejected", None)
                    return
                elif item.kind == "frame" and item.record is not None:
                    with self._lock:
                        self.counters["frames_in"] += 1
                    if slot.expect != "ack" and item.record.record_type == slot.expect:
                        self._raw(sock, bytes((ACK,)))
                        self._complete_slot(slot, "sent", item.record)
                        return
                    self._dispatch_inbound(sock, item.record)
                elif item.kind == "bad":
                    self._raw(sock, bytes((NAK,)))
                elif item.kind == "zeros":
                    with self._lock:
                        self.counters["resyncs"] += 1
                    raise LinkDropped("zeros: peer out of sync")

    def _complete_slot(self, slot: CommandSlot, outcome: str, answer: Record | None) -> None:
        # First write wins: a bounded-wait expiry on the caller side must
        # never be overwritten by a late link answer (at-least-once: the
        # poller requeues on link_down and the relay result is
        # first-write-wins, so a retry can never double-report).
        if slot.event.is_set():
            return
        slot.outcome = outcome
        slot.answer = answer
        slot.event.set()

    def _fail_inflight(self, outcome: str) -> None:
        with self._lock:
            slot = self._inflight
        if slot is not None and not slot.event.is_set():
            slot.outcome = outcome
            slot.event.set()

    def _on_wire_ack(self, ack: bool) -> None:
        # Bare ACK/NAK outside a transact wait: a stale ACK for a command
        # that already timed out is ignored (never completes a later
        # command — only the in-flight slot consumes answers).
        _ = ack

    def _dispatch_inbound(self, sock: socket.socket, rec: Record) -> None:
        rtype = rec.record_type
        if rtype == "LA":
            self._raw(sock, bytes((ACK,)))
        elif rtype == "LE":
            self._raw(sock, bytes((ACK,)))
            with self._lock:
                self.links_dropped += 1
            raise LinkDropped("peer LE (nightly interface restart)")
        elif rtype == "LS":
            # Peer restarts the handshake from LS (§2.3 resync).
            self._raw(sock, bytes((ACK,)))
            raise LinkDropped("peer LS: re-handshake")
        elif rtype in ("PS", "RE", "WA", "DR", "KA", "KZ"):
            # Durable local write FIRST, ACK only after it returns: a slow
            # cloud never stalls the PABX (REQ-022 B).
            answer_status = self._sink.on_record(rec)
            self._raw(sock, bytes((ACK,)))
            if rtype == "PS":
                # The PA answer is deferred to an idle moment and sent with
                # its own ACK wait (see _flush_pa): the PABX ACKs every
                # host frame at link level, and a PA flushed mid-command
                # would donate a stray ACK to the command's wait —
                # a false "sent". One entry per P# (deduped).
                with self._lock:
                    self._pa_pending.setdefault(rec.field("P#"), (rec.field("RN"), rec.field("P#")))
                _ = answer_status
        elif rtype in ("GI", "GC", "GO", "WR", "WC", "XL", "XD", "KR", "KD", "DS", "DE", "LD", "LR", "PA"):
            # Host-originated (or handshake-link) records never come from
            # the PABX on a live link: NAK, never guess.
            self._raw(sock, bytes((NAK,)))
        else:
            self._raw(sock, bytes((NAK,)))

    def _flush_pa(self, sock: socket.socket, splitter: FrameSplitter, stashed: deque[Record]) -> bool:
        """Send one deferred PA with its own ACK wait (idle moments only).

        Called from the serve loop when no command is in flight and none
        is queued, so the PA's link-level ACK can never be mistaken for a
        command answer. Fire-and-forget would leave the PABX resending at
        its 60 s interval; the wait here is milliseconds on a live link.
        Returns True when a PA was sent (the serve loop refreshes its
        keep-alive timer only then).
        """
        with self._lock:
            if self._inflight is not None or self._cmd_queue or not self._pa_pending:
                return False
            pno, (rn, _) = next(iter(self._pa_pending.items()))
            del self._pa_pending[pno]
        da, ti = _wire_now(self._tz)
        wait_stash: deque[Record] = deque()
        self._send_and_wait_ack(sock, splitter, wait_stash, "PA", [
            ("RN", rn), ("P#", pno), ("AS", "OK"), ("DA", da), ("TI", ti),
        ])
        with self._lock:
            self.counters["pa_sent"] += 1
        # A business frame may have arrived during the PA wait; stash it
        # for the serve loop drain (never dropped, never double-handled).
        stashed.extend(wait_stash)
        return True


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        deadline = datetime.datetime.fromisoformat(expires_at)
    except ValueError:
        return False  # unparseable expiry never blocks: the cloud decides
    now = datetime.datetime.now(datetime.UTC)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=datetime.UTC)
    return now > deadline


def _utcnow() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


class _suppress_oserror:
    def __enter__(self) -> _suppress_oserror:
        return self

    def __exit__(self, *args: object) -> bool:
        return True
