"""Repair rule registry — the 7 deterministic rules (P1.W4.3).

Each rule module in :mod:`autobridge.repair.rules` exposes:
  - RULE_ID: str
  - CONFIDENCE: float
  - VERIFICATION_LEVEL: str
  - PRODUCES_PATCH: bool
  - precondition(ctx) -> bool
  - generate_patch(ctx) -> list[dict]

The registry aggregates them and provides lookup helpers for the ladder (W4.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# Import rule modules to register them. Each is a deterministic module with
# precondition/patch generator, confidence and verification level.
from autobridge.repair.rules import cursor_to_offset as _cursor_to_offset
from autobridge.repair.rules import enum_extend_requires_evidence as _enum_extend
from autobridge.repair.rules import nested_path_shift as _nested_path_shift
from autobridge.repair.rules import offset_to_cursor as _offset_to_cursor
from autobridge.repair.rules import page_to_cursor as _page_to_cursor
from autobridge.repair.rules import rename_field as _rename_field
from autobridge.repair.rules import retry_backoff_only as _retry_backoff
from autobridge.repair.rules import type_coerce_string_number as _type_coerce

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext


@dataclass(frozen=True, slots=True)
class RuleEntry:
    """Registered rule descriptor."""

    id: str
    module: Any
    confidence: float
    verification_level: str
    produces_patch: bool

    def precondition(self, ctx: RepairContext) -> bool:
        return bool(self.module.precondition(ctx))

    def generate_patch(self, ctx: RepairContext) -> list[dict[str, Any]]:
        return list(self.module.generate_patch(ctx))


# Registry is insertion-ordered; ladder iterates in this order (priority).
RULE_REGISTRY: dict[str, RuleEntry] = {
    _rename_field.RULE_ID: RuleEntry(
        id=_rename_field.RULE_ID,
        module=_rename_field,
        confidence=_rename_field.CONFIDENCE,
        verification_level=_rename_field.VERIFICATION_LEVEL,
        produces_patch=_rename_field.PRODUCES_PATCH,
    ),
    _offset_to_cursor.RULE_ID: RuleEntry(
        id=_offset_to_cursor.RULE_ID,
        module=_offset_to_cursor,
        confidence=_offset_to_cursor.CONFIDENCE,
        verification_level=_offset_to_cursor.VERIFICATION_LEVEL,
        produces_patch=_offset_to_cursor.PRODUCES_PATCH,
    ),
    _page_to_cursor.RULE_ID: RuleEntry(
        id=_page_to_cursor.RULE_ID,
        module=_page_to_cursor,
        confidence=_page_to_cursor.CONFIDENCE,
        verification_level=_page_to_cursor.VERIFICATION_LEVEL,
        produces_patch=_page_to_cursor.PRODUCES_PATCH,
    ),
    _cursor_to_offset.RULE_ID: RuleEntry(
        id=_cursor_to_offset.RULE_ID,
        module=_cursor_to_offset,
        confidence=_cursor_to_offset.CONFIDENCE,
        verification_level=_cursor_to_offset.VERIFICATION_LEVEL,
        produces_patch=_cursor_to_offset.PRODUCES_PATCH,
    ),
    _retry_backoff.RULE_ID: RuleEntry(
        id=_retry_backoff.RULE_ID,
        module=_retry_backoff,
        confidence=_retry_backoff.CONFIDENCE,
        verification_level=_retry_backoff.VERIFICATION_LEVEL,
        produces_patch=_retry_backoff.PRODUCES_PATCH,
    ),
    _enum_extend.RULE_ID: RuleEntry(
        id=_enum_extend.RULE_ID,
        module=_enum_extend,
        confidence=_enum_extend.CONFIDENCE,
        verification_level=_enum_extend.VERIFICATION_LEVEL,
        produces_patch=_enum_extend.PRODUCES_PATCH,
    ),
    _nested_path_shift.RULE_ID: RuleEntry(
        id=_nested_path_shift.RULE_ID,
        module=_nested_path_shift,
        confidence=_nested_path_shift.CONFIDENCE,
        verification_level=_nested_path_shift.VERIFICATION_LEVEL,
        produces_patch=_nested_path_shift.PRODUCES_PATCH,
    ),
    _type_coerce.RULE_ID: RuleEntry(
        id=_type_coerce.RULE_ID,
        module=_type_coerce,
        confidence=_type_coerce.CONFIDENCE,
        verification_level=_type_coerce.VERIFICATION_LEVEL,
        produces_patch=_type_coerce.PRODUCES_PATCH,
    ),
}


def get_rule(rule_id: str) -> RuleEntry:
    """Return rule by id, raising ValueError if unknown."""
    try:
        return RULE_REGISTRY[rule_id]
    except KeyError as e:
        raise ValueError(f"unknown rule: {rule_id}") from e


def all_rules() -> list[RuleEntry]:
    """Return all registered rules in priority order."""
    return list(RULE_REGISTRY.values())


def matching_rules(ctx: RepairContext) -> list[RuleEntry]:
    """Return rules whose precondition holds for *ctx*."""
    out: list[RuleEntry] = []
    for entry in RULE_REGISTRY.values():
        if entry.precondition(ctx):
            out.append(entry)
    return out
