"""FIAS PABX simulator (REQ-022 sub-step A).

The on-premise telephone exchange (PABX) at Grand Plaza speaks FIAS over
TCP. This package implements the PABX side of
``simulators/docs/fias-pabx.md`` (the single source of truth): framing,
link handshake, the PABX-originated records (``PS``/``RE``/``WA``/``DR``),
answers to host commands, keep-alive, resync and the fault hooks the
admin API drives.

This module must never import autobridge or verifier (trust boundary).
"""
