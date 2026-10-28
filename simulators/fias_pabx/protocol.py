"""FIAS frame codec: the wire format of §1 of ``fias-pabx.md`` (REQ-022 A).

Every record is one frame: ``STX <record> ETX`` where ``<record>`` is a
two-letter type, then ``|``, then fields — each a two-character field id
immediately followed by its value, terminated by ``|``::

    STX PS|RN101|P#000417|TA1250| ETX

The receiver answers every frame with a single byte: ``ACK`` (0x06) when
accepted, ``NAK`` (0x15) when it could not be parsed. Bytes outside
``STX … ETX`` (other than ``ACK``/``NAK``) are line noise and discarded.
Maximum frame length is 64 KB: a longer frame is a protocol error.

Pure functions and one incremental splitter — no I/O, no threads — so the
unit tests drive the whole codec without sockets.

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

from dataclasses import dataclass

STX = 0x02
ETX = 0x03
ACK = 0x06
NAK = 0x15
ZERO = 0x00

#: Site setting (§1): maximum frame length, payload bytes between STX/ETX.
MAX_FRAME_LEN = 64 * 1024

#: Single-byte answers and their meaning.
SINGLE_BYTES = {ACK: "ACK", NAK: "NAK"}


class FrameError(ValueError):
    """A frame could not be parsed (receiver answers NAK)."""


class OversizeFrameError(FrameError):
    """A frame exceeded the 64 KB site limit (receiver closes the link)."""


@dataclass(frozen=True)
class Record:
    """One parsed FIAS record: two-letter type plus ordered (id, value) fields."""

    record_type: str
    fields: tuple[tuple[str, str], ...]

    def field(self, field_id: str, default: str = "") -> str:
        """Return the first value for *field_id* (field ids repeat rarely)."""
        for fid, value in self.fields:
            if fid == field_id:
                return value
        return default

    def field_ids(self) -> list[str]:
        """Field ids in wire order."""
        return [fid for fid, _ in self.fields]


def encode_frame(record_type: str, fields: list[tuple[str, str]]) -> bytes:
    """Encode a record to wire bytes (STX … ETX, no trailing newline).

    Field values must be ASCII without ``|`` or control characters; the
    record type is exactly two characters.
    """
    if len(record_type) != 2 or not record_type.isascii():
        raise FrameError(f"record type must be 2 ASCII chars, got {record_type!r}")
    parts = [record_type, "|"]
    for fid, value in fields:
        if len(fid) != 2 or not fid.isascii():
            raise FrameError(f"field id must be 2 ASCII chars, got {fid!r}")
        if not value.isascii() or any(c in value for c in "|\x00\x02\x03\x06\x15"):
            raise FrameError(f"field {fid} value is not wire-safe: {value!r}")
        parts.append(fid)
        parts.append(value)
        parts.append("|")
    body = "".join(parts).encode("ascii")
    if len(body) > MAX_FRAME_LEN:
        raise OversizeFrameError(f"frame body {len(body)} bytes exceeds {MAX_FRAME_LEN}")
    return bytes((STX,)) + body + bytes((ETX,))


def parse_frame(body: bytes) -> Record:
    """Parse the bytes between STX and ETX into a :class:`Record`.

    Unknown field ids are kept (the caller ignores what it did not
    announce — §1 — never rejects). Raises :class:`FrameError` on any
    structural problem; the receiver answers NAK.
    """
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
    fields: list[tuple[str, str]] = []
    for chunk in rest[:-1].split("|"):
        if len(chunk) < 2:
            raise FrameError(f"field chunk too short: {chunk!r}")
        fields.append((chunk[:2], chunk[2:]))
    return Record(record_type=head, fields=tuple(fields))


@dataclass
class SplitResult:
    """One item pulled from the byte stream by :class:`FrameSplitter`."""

    kind: str  # "frame" | "bad" | "ack" | "nak" | "zeros"
    record: Record | None = None


class FrameSplitter:
    """Incremental byte-stream splitter (§1 framing + §2.3 resync signal).

    Feed received bytes via :meth:`feed`; pull items via :meth:`poll`.
    Items are complete frames, single ACK/NAK bytes, or a run of all-zero
    bytes (the PABX-is-out-of-sync signal — §2.3 — which the host answers
    with a fresh handshake, never with business records). Other stray
    bytes are line noise and are discarded silently.

    A frame whose body passes the site limit raises
    :class:`OversizeFrameError` on :meth:`feed` (the receiver closes the
    connection — §1); a structurally bad frame is returned as
    ``kind="bad"`` with the raw body attached so the receiver can NAK it.
    """

    def __init__(self, max_frame_len: int = MAX_FRAME_LEN) -> None:
        self._max = max_frame_len
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[SplitResult]:
        """Consume *data*, returning the newly completed stream items."""
        out: list[SplitResult] = []
        for byte in data:
            if self._buf and self._buf[0] == ZERO:
                # Inside a zero run: zeros extend it, anything else ends it.
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
                # else: line noise outside a frame — discarded (§1).
                continue
            # Inside a frame (STX seen, zero-run already handled above).
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
        # A zero run only becomes an item once a non-zero byte ends it or
        # flush() is called (zeros may still be arriving).
        return out

    def flush(self) -> list[SplitResult]:
        """Emit a trailing zero run, if any (end of data)."""
        if self._buf and self._buf[0] == ZERO:
            self._buf.clear()
            return [SplitResult(kind="zeros")]
        return []
