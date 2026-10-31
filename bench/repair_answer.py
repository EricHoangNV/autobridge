"""Frontier repair-answer parsing — REQ-060 item 1.

Both frontier arms answer what the engine's L3 may answer: a JSON Patch
on the given pre-drift spec, or an escalation with one question
(optionally carrying the change the operator could approve). The shapes
mirror the engine's L3 contract (``config/prompts/l3_patch.md``) so a
frontier patch is graded exactly like an engine patch:

* ``{"patch": [...], "rationale": "..."}`` — direct patch,
* ``{"escalate": "<question>", "rationale": "..."}`` — hold back,
* ``{"escalate": "<question>", "rationale": "...", "proposal": {"label",
  "effect", "patch"}}`` — escalation with a tested proposal,
* a bare patch list (tolerated, same as the engine's pre-044 shape).

Anything else (prose, an unparseable block, a malformed member) is
``kind == "none"`` with a one-line *detail* — never guessed, never
raised. A full ConnectorSpec document is NOT a valid repair answer
(that was the pre-060 question); when one is detected the detail says
so explicitly.

Trust boundary (DIRECTIVES §2): this module parses text only — it never
imports ``verifier`` and never reads hidden cases.
"""

from __future__ import annotations

from typing import Any


def _is_patch_list(parsed: Any) -> bool:
    """True when *parsed* JSON is a JSON Patch-shaped list of operations."""
    return isinstance(parsed, list) and all(
        isinstance(op, dict) and "op" in op and "path" in op for op in parsed
    )


def _extract_json_candidates(stripped: str) -> list[str]:
    """Whole text, fenced block, then largest ``{...}``/``[...]`` span."""
    import re as _re  # noqa: PLC0415

    candidates: list[str] = [stripped]
    fence = _re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, _re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])
    return candidates


def _parse_proposal(value: Any) -> tuple[dict[str, Any] | None, str]:
    """Validate an escalation's ``proposal`` member (same rules as L3).

    Returns ``(proposal_dict | None, detail)``; a malformed proposal is
    dropped and labelled, the escalation stands on the question alone.
    """
    if value is None:
        return None, "no proposal member"
    if not isinstance(value, dict):
        return None, "proposal is not an object (dropped)"
    label = value.get("label")
    effect = value.get("effect")
    patch = value.get("patch")
    if not isinstance(label, str) or not label.strip():
        return None, "proposal has no label (dropped)"
    if not isinstance(effect, str) or not effect.strip():
        return None, "proposal has no effect (dropped)"
    if not isinstance(patch, list) or not patch:
        return None, "proposal has no patch ops (dropped)"
    for op in patch:
        if not isinstance(op, dict):
            return None, "proposal patch op is not an object (dropped)"
        if not isinstance(op.get("op"), str) or not op.get("op"):
            return None, "proposal patch op has no op (dropped)"
        if not isinstance(op.get("path"), str) or not op.get("path"):
            return None, "proposal patch op has no path (dropped)"
    return (
        {
            "label": " ".join(str(label).split())[:120],
            "effect": " ".join(str(effect).split())[:300],
            "patch": [dict(op) for op in patch if isinstance(op, dict)],
        },
        "parsed",
    )


def _looks_like_full_spec(parsed: Any) -> bool:
    """A whole ConnectorSpec document — not a valid repair answer."""
    return isinstance(parsed, dict) and (
        "operations" in parsed or "spec_version" in parsed or "connector" in parsed
    )


def parse_repair_answer(text: str) -> dict[str, Any]:
    """Parse a frontier arm's repair answer (REQ-060 item 1).

    Returns ``{"kind", "patch", "rationale", "question", "proposal",
    "proposal_label", "proposal_effect", "detail"}`` where *kind* is
    ``"patch"``, ``"escalate"`` or ``"none"``. Never raises on
    unparseable input.
    """
    import json as _json  # noqa: PLC0415 - keep module import-light

    stripped = (text or "").strip()
    if not stripped:
        return {
            "kind": "none",
            "patch": None,
            "rationale": "",
            "question": "",
            "proposal": None,
            "proposal_label": "",
            "proposal_effect": "",
            "detail": "empty response: the model returned no text",
        }
    for candidate in _extract_json_candidates(stripped):
        try:
            parsed = _json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            patch = parsed.get("patch")
            escalate = parsed.get("escalate")
            rationale = parsed.get("rationale") or ""
            if isinstance(patch, list) and _is_patch_list(patch):
                return {
                    "kind": "patch",
                    "patch": [dict(op) for op in patch],
                    "rationale": str(rationale),
                    "question": "",
                    "proposal": None,
                    "proposal_label": "",
                    "proposal_effect": "",
                    "detail": "parsed",
                }
            if isinstance(patch, list):
                return {
                    "kind": "none",
                    "patch": None,
                    "rationale": str(rationale),
                    "question": "",
                    "proposal": None,
                    "proposal_label": "",
                    "proposal_effect": "",
                    "detail": "response patch member is not a patch list (no op/path operations)",
                }
            if isinstance(escalate, str) and escalate.strip():
                proposal_dict, proposal_detail = _parse_proposal(parsed.get("proposal"))
                out: dict[str, Any] = {
                    "kind": "escalate",
                    "patch": None,
                    "rationale": str(rationale),
                    "question": escalate.strip(),
                    "proposal": None,
                    "proposal_label": "",
                    "proposal_effect": "",
                    "detail": "parsed",
                }
                if proposal_dict is not None:
                    out["proposal"] = list(proposal_dict["patch"])
                    out["proposal_label"] = str(proposal_dict["label"])
                    out["proposal_effect"] = str(proposal_dict["effect"])
                elif "proposal" in parsed:
                    out["detail"] = f"parsed ({proposal_detail})"
                return out
            if "escalate" in parsed:
                return {
                    "kind": "none",
                    "patch": None,
                    "rationale": str(rationale),
                    "question": "",
                    "proposal": None,
                    "proposal_label": "",
                    "proposal_effect": "",
                    "detail": "response escalate member is not a question string",
                }
            if _looks_like_full_spec(parsed):
                return {
                    "kind": "none",
                    "patch": None,
                    "rationale": str(rationale),
                    "question": "",
                    "proposal": None,
                    "proposal_label": "",
                    "proposal_effect": "",
                    "detail": "response is a whole ConnectorSpec, not a JSON Patch on the given spec",
                }
            return {
                "kind": "none",
                "patch": None,
                "rationale": str(rationale),
                "question": "",
                "proposal": None,
                "proposal_label": "",
                "proposal_effect": "",
                "detail": "response JSON is neither a patch nor an escalation object",
            }
        if _is_patch_list(parsed):
            return {
                "kind": "patch",
                "patch": [dict(op) for op in parsed],
                "rationale": "",
                "question": "",
                "proposal": None,
                "proposal_label": "",
                "proposal_effect": "",
                "detail": "parsed bare patch list",
            }
    if "[" in stripped or "{" in stripped:
        return {
            "kind": "none",
            "patch": None,
            "rationale": "",
            "question": "",
            "proposal": None,
            "proposal_label": "",
            "proposal_effect": "",
            "detail": "response contains JSON-like content that does not parse as a patch or escalation",
        }
    return {
        "kind": "none",
        "patch": None,
        "rationale": "",
        "question": "",
        "proposal": None,
        "proposal_label": "",
        "proposal_effect": "",
        "detail": "response contains no JSON patch or escalation object",
    }


def healthz() -> dict[str, str]:
    """Health check for the repair-answer module."""
    return {"status": "ok", "service": "repair_answer"}
