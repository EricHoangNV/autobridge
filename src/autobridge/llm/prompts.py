"""Engine model prompts — files under ``config/prompts/``, loaded once at startup (REQ-045).

Every engine prompt (system + user template) lives in its own Markdown
file with a small front-matter (``id``, ``version``, ``purpose``,
``tier``, ``max_tokens``, declared ``inputs``, ``answer_format``), then
the system text and the user template with named ``{placeholders}``.
No prompt text remains in Python: call sites render via
:func:`render_prompt`, supplying every declared input.

``config/`` already ships in the engine images (as ``demo_targets.yaml``
does); ``AUTOBRIDGE_PROMPTS_DIR`` overrides the directory. Control and
the worker load and validate all prompts at boot (see
:func:`ensure_prompts_loaded`, wired into the control lifespan); a
missing file or bad front-matter raises :class:`PromptError` naming the
file, failing startup. Editing a prompt takes effect on the next engine
restart — never hot-reloaded, never per call (the loaded set is cached
process-wide).

Each model call records the prompt ``id``, ``version`` and content hash
(see :func:`prompt_ref`); replay recordings stay keyed by the rendered
messages, so an edited prompt shows as "replay missing — re-record",
never a silent fallback.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

#: Env var overriding the prompts directory (mirrors demo_targets.py).
PROMPTS_ENV = "AUTOBRIDGE_PROMPTS_DIR"

#: Every engine prompt that must exist (file ``<id>.md``).
REQUIRED_PROMPT_IDS: tuple[str, ...] = ("l2_rank", "l3_hypothesis", "l3_patch")

#: Optional engine prompts, loaded when present (the L3 retry asks the
#: same question as ``l3_patch`` plus the rejection).
OPTIONAL_PROMPT_IDS: tuple[str, ...] = ("l3_patch_retry",)


class PromptError(RuntimeError):
    """A prompt file is missing or invalid — startup must fail, naming it."""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One parsed prompt file: front-matter plus system/user templates."""

    id: str
    version: str
    purpose: str
    tier: str
    max_tokens: int
    inputs: tuple[str, ...]
    answer_format: str
    system: str
    user_template: str
    #: sha256 of the raw file text — recorded on every model call so an
    #: edited prompt is traceable even without a version bump.
    content_hash: str


def default_prompts_dir() -> Path:
    """Prompts directory: env override, else the repo checkout."""
    override = os.environ.get(PROMPTS_ENV, "").strip()
    if override:
        return Path(override)
    # src/autobridge/llm/prompts.py -> repo root is parents[3].
    return Path(__file__).resolve().parents[3] / "config" / "prompts"


def _placeholders(text: str) -> set[str]:
    """``{name}`` placeholders in *text* (``{{``/``}}`` are not placeholders)."""
    import re as _re

    found: set[str] = set()
    for match in _re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", text):
        start = match.start()
        # Skip ``{{name}}`` (escaped literal braces).
        if start > 0 and text[start - 1] == "{":
            continue
        end = match.end()
        if end < len(text) and text[end] == "}":
            continue
        found.add(match.group(1))
    return found


def parse_prompt_file(path: str | Path) -> PromptTemplate:
    """Parse and validate one prompt file; raise :class:`PromptError` naming it."""
    resolved = Path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PromptError(f"engine prompt missing: {resolved} (set {PROMPTS_ENV} to override)") from exc
    except OSError as exc:
        raise PromptError(f"engine prompt unreadable: {resolved}: {exc}") from exc
    lines = raw.split("\n")
    if not lines or lines[0].strip() != "---":
        raise PromptError(f"engine prompt {resolved}: missing front-matter (must start with '---')")
    try:
        closing = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() in ("---", "..."))
    except StopIteration as exc:
        raise PromptError(f"engine prompt {resolved}: unterminated front-matter (no closing '---')") from exc
    import yaml  # noqa: PLC0415 (PyYAML; same seam as demo_targets.py)

    try:
        front = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        raise PromptError(f"engine prompt {resolved}: bad front-matter: {exc}") from exc
    if not isinstance(front, dict):
        raise PromptError(f"engine prompt {resolved}: bad front-matter (must be a mapping)")
    for key in ("id", "version", "purpose", "tier", "max_tokens", "inputs", "answer_format"):
        if key not in front or front[key] is None or (isinstance(front[key], str) and not front[key].strip()):
            raise PromptError(f"engine prompt {resolved}: bad front-matter (missing {key!r})")
    prompt_id = str(front["id"]).strip()
    if prompt_id != resolved.stem:
        raise PromptError(
            f"engine prompt {resolved}: bad front-matter (id {prompt_id!r} != file name {resolved.stem!r})"
        )
    tier = str(front["tier"]).strip()
    if tier not in ("small", "frontier"):
        raise PromptError(f"engine prompt {resolved}: bad front-matter (tier {tier!r} must be small|frontier)")
    try:
        max_tokens = int(front["max_tokens"])
    except (TypeError, ValueError) as exc:
        raise PromptError(
            f"engine prompt {resolved}: bad front-matter (max_tokens must be an int)"
        ) from exc
    if max_tokens <= 0:
        raise PromptError(f"engine prompt {resolved}: bad front-matter (max_tokens must be > 0)")
    raw_inputs = front["inputs"]
    if isinstance(raw_inputs, str):
        inputs = tuple(s.strip() for s in raw_inputs.split(",") if s.strip())
    elif isinstance(raw_inputs, list) and all(isinstance(x, str) for x in raw_inputs):
        inputs = tuple(x.strip() for x in raw_inputs if x.strip())
    else:
        raise PromptError(f"engine prompt {resolved}: bad front-matter (inputs must be a list of names)")
    if not inputs or len(set(inputs)) != len(inputs):
        raise PromptError(
            f"engine prompt {resolved}: bad front-matter (inputs must be non-empty with unique names)"
        )
    body = "\n".join(lines[closing + 1 :])
    system_marker, user_marker = "\n## System\n", "\n## User\n"
    system_at, user_at = body.find(system_marker), body.find(user_marker)
    if system_at == -1 or user_at == -1 or user_at < system_at:
        raise PromptError(f"engine prompt {resolved}: body needs '## System' then '## User' sections")
    system = body[system_at + len(system_marker) : user_at].strip("\n")
    user_template = body[user_at + len(user_marker) :].strip("\n")
    if not system.strip() or not user_template.strip():
        raise PromptError(f"engine prompt {resolved}: '## System' and '## User' must both be non-empty")
    used = _placeholders(system) | _placeholders(user_template)
    declared = set(inputs)
    if undeclared := sorted(used - declared):
        raise PromptError(
            f"engine prompt {resolved}: undeclared placeholder(s) {undeclared} "
            f"(declare them under inputs: or remove them)"
        )
    if unused := sorted(declared - used):
        raise PromptError(
            f"engine prompt {resolved}: declared input(s) {unused} never used "
            f"in the System/User text (every declared input must render)"
        )
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return PromptTemplate(
        id=prompt_id,
        version=str(front["version"]).strip(),
        purpose=str(front["purpose"]).strip(),
        tier=tier,
        max_tokens=max_tokens,
        inputs=inputs,
        answer_format=str(front["answer_format"]).strip(),
        system=system.strip(),
        user_template=user_template.strip(),
        content_hash=content_hash,
    )


def render_prompt(template: PromptTemplate, **values: str) -> list[dict[str, str]]:
    """Render a prompt to chat messages; every declared input must be supplied.

    Raises :class:`PromptError` (never silent) when a declared input is
    missing, an undeclared one is passed, or a placeholder would be left
    unrendered.
    """
    declared = set(template.inputs)
    missing = sorted(declared - set(values))
    if missing:
        raise PromptError(f"engine prompt {template.id!r}: missing input(s) {missing}")
    extra = sorted(set(values) - declared)
    if extra:
        raise PromptError(f"engine prompt {template.id!r}: undeclared input(s) {extra}")
    system = template.system
    user = template.user_template
    for name in template.inputs:
        token = "{" + name + "}"
        value = values[name] if isinstance(values[name], str) else str(values[name])
        system = system.replace(token, value)
        user = user.replace(token, value)
    leftover = _placeholders(system) | _placeholders(user)
    if leftover & declared:
        raise PromptError(
            f"engine prompt {template.id!r}: unrendered placeholder(s) "
            f"{sorted(leftover & declared)} (values must be strings without braces games)"
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def load_prompts(directory: str | Path | None = None) -> dict[str, PromptTemplate]:
    """Load and validate every required prompt; fail closed naming the file."""
    resolved = Path(directory) if directory is not None else default_prompts_dir()
    loaded: dict[str, PromptTemplate] = {}
    for prompt_id in REQUIRED_PROMPT_IDS:
        path = resolved / f"{prompt_id}.md"
        loaded[prompt_id] = parse_prompt_file(path)
    for prompt_id in OPTIONAL_PROMPT_IDS:
        path = resolved / f"{prompt_id}.md"
        if path.exists():
            loaded[prompt_id] = parse_prompt_file(path)
    return loaded


_LOADED: dict[str, PromptTemplate] | None = None


def get_prompts() -> dict[str, PromptTemplate]:
    """Process-wide loaded prompts (loaded once, never per call)."""
    global _LOADED
    if _LOADED is None:
        _LOADED = load_prompts()
    return _LOADED


def ensure_prompts_loaded() -> dict[str, PromptTemplate]:
    """Load and validate all prompts now — call at process startup.

    Raises :class:`PromptError` naming the file, so a missing file or bad
    front-matter fails startup instead of serving with a broken prompt.
    """
    return get_prompts()


def reset_prompts_cache() -> None:
    """Drop the process-wide cache (tests only; production never hot-reloads)."""
    global _LOADED
    _LOADED = None


def get_prompt(prompt_id: str) -> PromptTemplate:
    """One loaded prompt by id (``KeyError`` for unknown ids)."""
    return get_prompts()[prompt_id]


def prompt_ref(prompt_id: str) -> dict[str, str]:
    """Ledger reference for a prompt: ``id``, ``version``, content ``hash``."""
    template = get_prompt(prompt_id)
    return {"id": template.id, "version": template.version, "hash": template.content_hash}
