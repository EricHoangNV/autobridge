# Grand Plaza — PABX Interface Specification (FIAS)

> Interface specification for the telephone exchange (PABX) at **Grand Plaza** (`prop-001`), as provided by the
> hotel's IT team to integration partners. The PABX speaks **FIAS** (Fidelio Interface Application Specification),
> the long-standing hotel interface protocol. This document describes the subset this PABX implements and the
> settings of this installation. Field ids and record types follow FIAS; values marked *(site setting)* are this
> hotel's configuration and may differ at other properties.

Connection: TCP, plain (no TLS) on the hotel LAN. The **integration host listens** on port `5010` *(site setting)*
and the **PABX connects to it**; the PABX reconnects every 10 s *(site setting)* while no link is up. One logical link
per TCP connection. Character set: ASCII.

---

## 1. Framing

Every record is one frame:

```
STX  <record>  ETX
0x02 LS|DA261028|TI120000|  0x03
```

- `<record>` = two-letter **record type**, then `|`, then fields. Each field is a two-character **field id**
  immediately followed by its value, terminated by `|`. Example: `PS|RN101|P#000417|DA261028|TI231504|TA1250|`.
- Field order inside a record is the order announced in the record's `LR` (see §2). Unknown field ids must be
  ignored, never rejected.
- **ACK mode** is on at this site *(site setting)*. The receiver of any frame answers with a single byte:
  `ACK` (0x06) when the frame was accepted, `NAK` (0x15) when it could not be parsed. The sender resends a NAKed frame
  up to 3 times *(site setting)*, then drops the link.
- Bytes outside `STX … ETX` (other than `ACK` / `NAK`) are line noise and are discarded.
- Maximum frame length: 64 KB *(site setting)*. A longer frame is a protocol error: the PABX closes the connection.

---

## 2. Link control

### 2.1 Records

| Record | Name | Fields | Sent by |
|---|---|---|---|
| `LS` | Link Start | `DA` date, `TI` time | either side, to (re)start the link |
| `LD` | Link Description | `DA`, `TI`, `V#` interface version, `IF` interface family (`PB` = PABX) | host |
| `LR` | Link Record | `RI` record id, `FL` field list (concatenated field ids, in order) | host, one per record type it wants |
| `LA` | Link Alive | `DA`, `TI` | either side; marks the end of the handshake, then keep-alive |
| `LE` | Link End | `DA`, `TI` | either side, before closing |

### 2.2 Handshake

```
host  → PABX   LS|DA261028|TI120000|
PABX  → host   ACK
PABX  → host   LS|DA261028|TI120000|
host  → PABX   ACK
host  → PABX   LD|DA261028|TI120000|V#1.0|IFPB|
PABX  → host   ACK
host  → PABX   LR|RIPS|FLRNP#DATITADDDUSO|          (one LR per record type, each ACKed)
host  → PABX   LR|RIRE|FLRNRSDATI|
  …
host  → PABX   LA|DA261028|TI120000|
PABX  → host   ACK
PABX  → host   LA|DA261028|TI120000|               link is up
host  → PABX   ACK
```

- The host announces, with `LR`, every record type it wants to exchange and the fields it expects in each. The PABX
  only sends record types the host announced, and only the announced fields.
- **Site extension (not part of base FIAS): this PABX also announces its own layout for the records it originates** (`PS`, `RE`, `WA`, `DR`) by sending
  its own `LR` records after the host's `LA` and before its own `LA`. When the PABX's layout differs from what the host
  announced (for example after a PABX firmware update), **the PABX's layout wins**: it sends records in its own field
  order and may add or drop fields. A host must read the PABX's `LR` records on every new link and map fields by id.
- `V#` is the host's interface version string; this PABX accepts any value.

### 2.3 Keep-alive and resynchronisation

- After the link is up, each side sends `LA` every 5 s *(site setting)* when it has nothing else to send. Keep-alive
  frames are never queued behind business records.
- If no frame (or ACK) arrives for 30 s *(site setting)*, the PABX treats the link as dead and closes the connection.
- **All-zero bytes** (one or more `0x00` instead of a frame) mean the PABX has lost synchronisation (for example after
  a reboot of its interface card). The host must stop sending business records and restart the handshake from `LS`.
  Records not ACKed before the resync must be resent after the new `LA`.
- `LE` ends the link cleanly; either side may then close the socket.

---

## 3. Records from the PABX (to the host)

### 3.1 `PS` — Posting Simple (call or minibar charge)

Raised for every chargeable telephone call and for minibar items entered on the room phone.

| Field | Meaning | Format |
|---|---|---|
| `RN` | Room number | up to 6 chars, e.g. `101` |
| `P#` | Posting sequence number | 6 digits, increments per posting, wraps at 999999 |
| `DA` | Posting date | `YYMMDD`, property-local |
| `TI` | Posting time | `HHMMSS`, property-local |
| `TA` | Total amount | integer in **minor currency units** (cents): `1250` = 12.50 in the property currency |
| `DD` | Dialled digits (calls only) | digits, masked after the 6th digit, e.g. `004930XXXXXX` |
| `DU` | Duration (calls only) | `HHMMSS` |
| `SO` | Sales outlet / charge type | `TEL` call, `MINI` minibar *(site setting)* |

- The PABX expects a `PA` (§4.6) for every `PS`. Until it gets one, it **resends the same `PS` (same `P#`)** every
  60 s *(site setting)*, so the host will see duplicates and must post each `P#` once.
- Amounts never carry a decimal point. Currency is the property currency (USD at Grand Plaza).

### 3.2 `RE` — Room Equipment / room status

Sent when housekeeping dials a status code from the room phone.

| Field | Meaning | Format |
|---|---|---|
| `RN` | Room number | |
| `RS` | Room status | `1` dirty/vacant, `2` dirty/occupied, `3` clean/vacant, `4` clean/occupied, `5` inspected/vacant, `6` inspected/occupied |
| `DA`, `TI` | When the code was dialled | `YYMMDD`, `HHMMSS` |

### 3.3 `WA` — Wake-up Answer

Result of a wake-up call set with `WR` (§4.3).

| Field | Meaning | Format |
|---|---|---|
| `RN` | Room number | |
| `DA`, `TI` | Wake-up date and time that was requested | |
| `AS` | Answer status | `OK` guest answered, `NR` no reply after 3 attempts, `BY` line busy, `UR` unprocessable request |

### 3.4 `DR` — Database Resync request

The PABX asks the host to resend the current guest state (for example after a PABX restart).

| Field | Meaning |
|---|---|
| `DA`, `TI` | Time of the request |

The host answers with `DS` (Database Swap start, `DA`/`TI`), one `GI` per occupied room, then `DE` (Database Swap
end, `DA`/`TI`). The PABX does not raise postings for a room until it has received that room's `GI`.

---

## 4. Records from the host (to the PABX)

Unless stated, the PABX confirms these with `ACK` only. Send one business record at a time and wait for its `ACK`
(or answer record) before sending the next; the PABX does not queue.

### 4.1 `GI` / `GC` / `GO` — Guest In / Change / Out

| Field | Meaning | `GI` | `GC` | `GO` |
|---|---|---|---|---|
| `RN` | Room number | ✓ | ✓ (new room) | ✓ |
| `RO` | Old room number (room move) | | when moving | |
| `G#` | Reservation number | ✓ | ✓ | ✓ |
| `GN` | Guest name, `Last,First` | ✓ | ✓ | |
| `GL` | Guest language, ISO 639-1 | ✓ | ✓ | |
| `GA` | Arrival date `YYMMDD` | ✓ | ✓ | |
| `GD` | Departure date `YYMMDD` | ✓ | ✓ | |
| `CS` | Class of service: `0` barred, `1` local, `2` national, `3` international *(site setting)* | ✓ | ✓ | |
| `DA`, `TI` | Time of the change | ✓ | ✓ | ✓ |

`GI` opens the room extension for outgoing calls and shows the guest name on the operator console. `GO` bars the
extension and clears the name.

### 4.2 `XL` / `XD` — Message light on / off

| Field | Meaning |
|---|---|
| `RN` | Room number |
| `MI` | Message id (host reference) |
| `DA`, `TI` | Time |

### 4.3 `WR` / `WC` — Wake-up Request / Clear

| Field | Meaning |
|---|---|
| `RN` | Room number |
| `DA` | Wake-up date `YYMMDD` |
| `TI` | Wake-up time `HHMM00` |

A `WR` for a date and time already in the past is answered with `WA` … `AS|UR|` and not scheduled. `WC` cancels a
pending wake-up for that room, date and time.

### 4.4 `KR` / `KD` / `KZ` — Key write / delete / read

The PABX relays key-card commands to the encoder at the front desk.

| Field | Meaning | `KR` | `KD` | `KZ` |
|---|---|---|---|---|
| `KC` | Encoder id, e.g. `01` | ✓ | ✓ | ✓ |
| `RN` | Room number | ✓ | ✓ | |
| `K#` | Number of keys to write | ✓ | | |
| `GA`, `GD` | Key validity from / to `YYMMDD` | ✓ | | |
| `G#` | Reservation number | ✓ | ✓ | |
| `DA`, `TI` | Time | ✓ | ✓ | ✓ |

`KR` and `KD` are always answered with `KA` (§4.5), even when ACK mode is off. `KZ` (read the card in the encoder) is
answered with a `KZ` carrying the card's `RN`, `G#`, `GA`, `GD`. Allow 30 s for the guest to place the card
*(site setting)*.

### 4.5 `KA` — Key Answer (from the PABX)

| Field | Meaning |
|---|---|
| `KC` | Encoder id |
| `AS` | `OK`, `BY` encoder busy, `NC` no card in encoder, `UR` unprocessable request |
| `DA`, `TI` | Time |

### 4.6 `PA` — Posting Answer (host → PABX)

The host's answer to each `PS`.

| Field | Meaning |
|---|---|
| `RN` | Room number from the `PS` |
| `P#` | Posting sequence number from the `PS` |
| `AS` | `OK` posted, `NG` not posted (room not checked in), `UR` unprocessable |
| `CT` | Clear text shown on the operator console, optional, max 40 chars |
| `DA`, `TI` | Time |

---

## 5. Operational notes from the hotel

- The PABX keeps **one link at a time**: it opens a single connection to the host and does not open a second while a
  link is up.
- The PABX's interface card restarts during the nightly maintenance window (03:00–03:05 local) *(site setting)*;
  expect all-zero bytes or a dropped connection, then a new handshake.
- PABX firmware updates are applied by the PABX maintainer without notice to integration partners. Updates have in the
  past changed the `PS` field layout (fields added or reordered); the PABX announces the new layout in its `LR` records
  on the next link (§2.2).
- During peak hours the PABX may send bursts of up to 50 postings per second. A host that stops ACKing for more than
  30 s loses the link (§2.3).
- The PABX never sends personal data beyond what is listed above. Dialled numbers are masked (§3.1).

---

## 6. Worked example

```
→ STX GI|RN101|G#RES-000042|GNLovelace,Ada|GLen|GA261027|GD261030|CS3|DA261027|TI150212| ETX
← ACK
← STX PS|RN101|P#000417|DA261028|TI231504|TA1250|DD004930XXXXXX|DU000312|SOTEL| ETX
→ ACK
→ STX PA|RN101|P#000417|ASOK|DA261028|TI231505| ETX
← ACK
→ STX WR|RN101|DA261029|TI063000| ETX
← ACK
← STX WA|RN101|DA261029|TI063000|ASOK| ETX
→ ACK
← 0x00 0x00 0x00                                   PABX lost sync: restart from LS (§2.3)
```
