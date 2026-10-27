"""JSON Patch apply / validate / reverse for ConnectorSpec (P1.W1.3).

A repair is data: a JSON Patch (RFC 6902) applied to a *copy* of the spec,
validated against :class:`autobridge.spec.models.ConnectorSpec`, diffable and
reversible. No rule or model ever writes Python.
"""

from __future__ import annotations

import copy
from typing import Any

import jsonpatch
import jsonpointer
from pydantic import ValidationError

from autobridge.spec.models import ConnectorSpec, load_spec_from_dict

_PATCH_OPS = ("add", "remove", "replace", "move", "copy", "test")


class PatchError(ValueError):
    """A patch is malformed or cannot be applied; carries the op index."""

    def __init__(self, message: str, *, op_index: int | None = None) -> None:
        super().__init__(message)
        self.op_index = op_index


class SpecValidationError(ValueError):
    """A patch applied cleanly but the result is not a valid ConnectorSpec."""

    def __init__(self, message: str, *, errors: list[Any]) -> None:
        super().__init__(message)
        self.errors = errors


def validate_patch_shape(patch: Any) -> list[dict[str, Any]]:
    """Check a patch is a non-empty list of well-formed RFC 6902 operations."""
    if not isinstance(patch, list) or not patch:
        raise PatchError("patch must be a non-empty list of operations")
    for i, op in enumerate(patch):
        if not isinstance(op, dict):
            raise PatchError(f"op {i} must be a mapping", op_index=i)
        if op.get("op") not in _PATCH_OPS:
            raise PatchError(
                f"op {i}: unknown op {op.get('op')!r}; expected one of {list(_PATCH_OPS)}",
                op_index=i,
            )
        if "path" not in op or not isinstance(op["path"], str) or not op["path"].startswith(
            "/"
        ):
            raise PatchError(f"op {i}: 'path' must be a JSON Pointer starting with /", op_index=i)
        if op["op"] in ("add", "replace", "test") and "value" not in op:
            raise PatchError(f"op {i}: '{op['op']}' requires a value", op_index=i)
        if op["op"] in ("move", "copy") and (
            "from" not in op or not isinstance(op["from"], str) or not op["from"].startswith("/")
        ):
            raise PatchError(f"op {i}: '{op['op']}' requires a 'from' pointer", op_index=i)
    return patch


def apply_patch(
    spec: dict[str, Any] | ConnectorSpec, patch: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply *patch* to a copy of *spec*; validate and return the new document.

    The input is never mutated. Raises :class:`PatchError` when the patch is
    malformed or does not apply, and :class:`SpecValidationError` (with
    path-level ``errors``) when the result is not a valid ConnectorSpec.
    """
    ops = validate_patch_shape(patch)
    base: dict[str, Any] = (
        spec.model_dump(mode="json", by_alias=True)
        if isinstance(spec, ConnectorSpec)
        else copy.deepcopy(spec)
    )
    try:
        result = jsonpatch.JsonPatch(ops).apply(base, in_place=False)
    except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
        raise PatchError(f"patch does not apply: {e}") from e
    if not isinstance(result, dict):  # pragma: no cover - shape check forbids root ops
        raise SpecValidationError("patched spec must be a mapping", errors=[])
    try:
        load_spec_from_dict(result)
    except ValidationError as e:
        raise SpecValidationError(
            f"patched spec is invalid: {e}",
            errors=e.errors(),
        ) from e
    return result


def diff_specs(
    before: dict[str, Any] | ConnectorSpec, after: dict[str, Any] | ConnectorSpec
) -> list[dict[str, Any]]:
    """Build the JSON Patch turning *before* into *after*."""
    b = before.model_dump(mode="json", by_alias=True) if isinstance(before, ConnectorSpec) else before
    a = after.model_dump(mode="json", by_alias=True) if isinstance(after, ConnectorSpec) else after
    return list(jsonpatch.make_patch(b, a).patch)


def reverse_patch(
    spec_before: dict[str, Any] | ConnectorSpec, patch: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build the inverse patch: applied to ``apply(spec_before, patch)`` it restores the original."""
    after = apply_patch(spec_before, patch)
    base: dict[str, Any] = (
        spec_before.model_dump(mode="json", by_alias=True)
        if isinstance(spec_before, ConnectorSpec)
        else spec_before
    )
    return diff_specs(after, base)
