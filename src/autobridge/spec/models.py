"""ConnectorSpec v1/v2/v3 pydantic models (P1.W1.3, P1.W3.1, REQ-020.5a, REQ-022.C).

A connector is a declarative document the runtime interprets — never code.
Every spec validates against ``schemas/connector-spec.v3.json`` before it is
stored, executed or patched; invalid specs are rejected with path-level
errors. Repairs are JSON Patches on the spec (see :mod:`autobridge.spec.patch`).

Version 2 adds POST operations with a JSON body template, ``body_fields``
auth and ``body_cursor`` pagination. Version 3 adds the event-driven
transport: ``protocol: fias_tcp`` sources (framing/ACK/link records as data),
event feeds (records pushed to the agent instead of polled), multi-path
field mapping, and command templates for host-originated records. Every
version 1 document still validates and behaves exactly as before, and every
version 2 document validates and behaves exactly as before (the v2
vocabulary gate rejects every v3-only construct).

Secrets policy: auth carries ``credential_ref`` (an env key name) only —
inline secret values are rejected, as are credentials embedded in URLs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Closed transform set (plan §2.4). Adding one is a plan change, not a
#: task-level decision; the runtime registry must accept exactly these.
TRANSFORMS = (
    "date_local",
    "datetime_utc",
    "money",
    "enum_map",
    "flatten",
    "coalesce",
    "const",
    "concat",
    "business_date",
)

TransformName = Literal[
    "date_local",
    "datetime_utc",
    "money",
    "enum_map",
    "flatten",
    "coalesce",
    "const",
    "concat",
    "business_date",
]

StopCondition = Literal[
    "null_cursor",
    "repeated_cursor",
    "empty_page",
    "page_limit",
    "no_next",
]

CheckKind = Literal[
    "unique_key",
    "no_loss",
    "date_order",
    "money_invariant",
    "business_date_invariant",
    "count_invariant",
]

# JSONPath subset: root plus child segments only — no filters, slices,
# recursive descent, expressions or script. Anything with parentheses,
# whitespace or operators fails the match below.
_JSONPATH_SEGMENT = r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\]|\['[^'\]]+'\]|\[\"[^\"]+\"\]|\[\*\])"
JSONPATH_SUBSET_RE = re.compile(rf"^\$({_JSONPATH_SEGMENT})*$")

CREDENTIAL_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
CONNECTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
OPERATION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ENTITY_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ENV_PLACEHOLDER_RE = re.compile(r"\$\{([^}]*)\}")
EMBEDDED_CREDS_RE = re.compile(r"://[^/\s]*@")
TIMEZONE_RE = re.compile(r"^(?:UTC|UTC[+-]\d{1,2}(?::\d{2})?|[A-Za-z_]+(?:/[A-Za-z0-9_+-]+)+)$")
INTERVAL_RE = re.compile(r"^\d+[smh]$")

#: Dotted request-body path (e.g. ``Limitation.Cursor``): one or more
#: ``[A-Za-z_][A-Za-z0-9_]*`` segments joined by dots. Unlike JSONPath
#: (which addresses responses with ``$.``), body paths address fields
#: inside the outgoing JSON body template.
BODY_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")

#: Source transport protocol. ``http`` is the polled vendor-API transport;
#: ``fias_tcp`` is the event-driven on-premise transport (framing, ACK mode
#: and link records carried as data in ``SourceSpec.link``; records arrive
#: as event feeds instead of paginated HTTP responses). New transports are
#: a vocabulary change (spec v3), never a per-vendor branch.
ProtocolName = Literal["http", "fias_tcp"]

#: Two-letter record code of a framed protocol (e.g. ``PS``, ``RE``).
RECORD_CODE_RE = re.compile(r"^[A-Z]{2}$")
#: Two-character wire field id (letters, digits, ``#`` — e.g. ``RN``, ``P#``).
WIRE_FIELD_ID_RE = re.compile(r"^[A-Za-z0-9#]{2}$")
#: Command template kind: bundle-style dotted names (e.g. ``wakeup.set``).
COMMAND_NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
#: ``{name}`` placeholders inside command field templates (same shape the
#: edge bundle executor substitutes; ``#`` never appears in a name).
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")

SCHEMA_FILENAME = "connector-spec.v3.json"


def check_jsonpath(value: str, what: str = "path") -> str:
    """Validate the JSONPath subset; raise ``ValueError`` naming the field."""
    if not JSONPATH_SUBSET_RE.match(value):
        raise ValueError(
            f"{what} must use the JSONPath subset "
            f"(e.g. $.data, $.meta.next_cursor) with no filters or expressions; "
            f"got {value!r}"
        )
    return value


def check_body_path(value: str, what: str = "body path") -> str:
    """Validate a dotted request-body path; raise ``ValueError`` naming the field."""
    if not BODY_PATH_RE.match(value):
        raise ValueError(
            f"{what} must be dotted field names "
            f"(e.g. Limitation.Cursor) with no expressions; "
            f"got {value!r}"
        )
    return value


JsonPath = Annotated[str, Field(description="JSONPath subset, no expressions")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TransformStep(_Strict):
    """One closed-set transform call with its arguments."""

    name: TransformName = Field(description="Closed transform name (plan §2.4)")
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_args(self) -> TransformStep:
        if self.name == "enum_map":
            mapping = self.args.get("map")
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError("enum_map requires a non-empty 'map' argument")
            if self.args.get("on_unknown") != "block":
                raise ValueError(
                    "enum_map requires on_unknown: block "
                    "(unknown enum values Block, never guessed)"
                )
        if self.name == "const" and "value" not in self.args:
            raise ValueError("const requires a 'value' argument")
        if self.name == "money" and "scale" in self.args:
            scale = self.args["scale"]
            if not isinstance(scale, int) or isinstance(scale, bool) or not 0 <= scale <= 6:
                raise ValueError("money 'scale' must be an int in 0..6")
        return self


def normalize_transform(value: Any) -> list[dict[str, Any]]:
    """Normalize the compact plan form into explicit ``{name, args}`` dicts.

    Accepts a bare name (``money``), an explicit mapping
    (``{name: money, args: {scale: 2}}``), ``[money, {scale: 2}]`` (name plus
    argument mappings merged into it), and chains such as
    ``[flatten, {name: money, args: {scale: 2}}]``.

    Returns plain data so pydantic validates each step with full path-level
    locations; only structural problems raise ``ValueError`` here.
    """
    if value is None:
        return []
    items = [value] if isinstance(value, (str, dict)) else value
    if not isinstance(items, list):
        raise ValueError("transform must be a name or a list of steps")
    groups: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        loc = f"transform[{i}]"
        if isinstance(item, str):
            groups.append({"name": item, "args": {}})
        elif isinstance(item, dict):
            if "name" in item:
                step = dict(item)
                args = step.get("args", {})
                if not isinstance(args, dict):
                    raise ValueError(f"{loc}: 'args' must be a mapping")
                step["args"] = dict(args)
                groups.append(step)
            else:
                if not groups:
                    raise ValueError(
                        f"{loc}: bare mapping is transform arguments "
                        "and must follow a transform name"
                    )
                args = groups[-1].setdefault("args", {})
                args.update(item)
        else:
            raise ValueError(f"{loc}: transform step must be a name or a {{name, args}} mapping")
    # An empty list means "no transform" (it is also what a dumped model
    # carries for fields without one, so dumps must re-validate).
    return groups


class FieldMapping(_Strict):
    """One canonical field: JSONPath subset plus transform pipeline.

    Exactly one of ``path``/``paths`` — unless the pipeline starts with
    ``const``, which needs no input (site facts like the property code or
    currency the docs state outright). ``paths`` (spec v3) extracts one
    value per listed path and feeds the list into the pipeline — e.g.
    ``paths: [$.DA, $.TI]`` plus ``concat`` with a space separator turns a
    wire date and time into one datetime string for the date transforms.
    """

    path: JsonPath | None = None
    paths: list[JsonPath] | None = None
    transform: list[TransformStep] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _path_subset(cls, v: str | None) -> str | None:
        return check_jsonpath(v, "field path") if v is not None else v

    @field_validator("paths")
    @classmethod
    def _paths_subset(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if not v:
            raise ValueError("field 'paths' must list at least one path")
        return [check_jsonpath(p, "field path") for p in v]

    @field_validator("transform", mode="before")
    @classmethod
    def _normalize_transform(cls, v: Any) -> Any:
        if isinstance(v, list) and v and all(isinstance(s, TransformStep) for s in v):
            return v
        return normalize_transform(v)

    @model_validator(mode="after")
    def _one_of_path_or_paths(self) -> FieldMapping:
        has_path = self.path is not None
        has_paths = self.paths is not None
        if has_path and has_paths:
            raise ValueError("field mapping needs exactly one of 'path' or 'paths'")
        if not has_path and not has_paths:
            # const-led pipelines need no input (site facts stated outright).
            if self.transform and self.transform[0].name == "const":
                return self
            raise ValueError("field mapping needs exactly one of 'path' or 'paths'")
        return self


class EntityCheck(_Strict):
    """One invariant check on an entity's emitted records.

    Semantic invariants (P1.W6.1):
    - money_invariant: money field must be present, positive Decimal (detects gross->net drift via business rule)
    - business_date_invariant: business_date must correctly derive from posted_at via cutoff
    - count_invariant: alias for no_loss with stricter counting (kept for semantic drift naming)
    """

    kind: CheckKind
    compare_to: JsonPath | None = None
    before: str | None = None
    after: str | None = None
    # money_invariant / business_date_invariant / count_invariant
    field: str | None = None
    reference_field: str | None = None
    cutoff: int | None = None
    # money_invariant: expected total sum for semantic drift detection (Decimal string)
    expected_sum: str | None = None
    tolerance_pct: float | None = None

    @field_validator("compare_to")
    @classmethod
    def _compare_subset(cls, v: str | None) -> str | None:
        return check_jsonpath(v, "compare_to") if v is not None else v

    @field_validator("field")
    @classmethod
    def _field_name(cls, v: str | None) -> str | None:
        if v is not None and not FIELD_NAME_RE.match(v):
            raise ValueError(f"check field {v!r} must match [a-z][a-z0-9_]*")
        return v

    @field_validator("reference_field")
    @classmethod
    def _ref_field_name(cls, v: str | None) -> str | None:
        if v is not None and not FIELD_NAME_RE.match(v):
            raise ValueError(f"reference_field {v!r} must match [a-z][a-z0-9_]*")
        return v

    @model_validator(mode="after")
    def _check_operands(self) -> EntityCheck:
        if self.kind == "no_loss" and self.compare_to is None:
            raise ValueError("no_loss requires compare_to (expected-count path)")
        if self.kind == "date_order" and (self.before is None or self.after is None):
            raise ValueError("date_order requires before and after field names")
        if self.kind == "money_invariant" and not self.field:
            raise ValueError("money_invariant requires field (money field name)")
        if self.kind == "business_date_invariant" and (not self.field or not self.reference_field):
            raise ValueError("business_date_invariant requires field (business_date) and reference_field (posted_at)")
        if self.expected_sum is not None:
            try:
                from decimal import Decimal as _D

                _D(str(self.expected_sum))
            except Exception as exc:
                raise ValueError(f"expected_sum {self.expected_sum!r} must be a valid Decimal string") from exc
        if self.tolerance_pct is not None and (
            not isinstance(self.tolerance_pct, (int, float)) or not 0 < self.tolerance_pct < 100
        ):
            raise ValueError("tolerance_pct must be a number in (0, 100)")
        return self


#: Join aggregation operators (spec v2, ``many`` cardinality only).
#: ``first`` takes the first non-null value, ``sum`` adds exact Decimals,
#: ``count`` counts matched records, ``min``/``max`` compare values.
JoinAggregate = Literal["first", "sum", "count", "min", "max"]


class JoinField(_Strict):
    """One computed value from the matched joined record(s) (spec v2).

    ``one`` cardinality: exactly one of ``path``/``paths``; ``paths``
    extracts several values from the single matched record and feeds them
    as a list into the transform pipeline (e.g. ``concat`` over
    ``[FirstName, LastName]``). ``many`` cardinality: ``path`` is read
    from every matched record and reduced by ``aggregate`` (required),
    e.g. ``sum`` over line-item gross amounts. ``transform`` reuses the
    closed transform set (``money``, ``concat``, ...), never code.
    """

    path: JsonPath | None = None
    paths: list[JsonPath] | None = None
    aggregate: JoinAggregate | None = None
    transform: list[TransformStep] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _path_subset(cls, v: str | None) -> str | None:
        return check_jsonpath(v, "join field path") if v is not None else v

    @field_validator("paths")
    @classmethod
    def _paths_subset(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if not v:
            raise ValueError("join field 'paths' must list at least one path")
        return [check_jsonpath(p, "join field path") for p in v]

    @field_validator("transform", mode="before")
    @classmethod
    def _normalize_transform(cls, v: Any) -> Any:
        if isinstance(v, list) and v and all(isinstance(s, TransformStep) for s in v):
            return v
        return normalize_transform(v)

    @model_validator(mode="after")
    def _one_of_path_or_paths(self) -> JoinField:
        if (self.path is None) == (self.paths is None):
            raise ValueError("join field needs exactly one of 'path' or 'paths'")
        return self


class JoinSpec(_Strict):
    """Declarative enrichment of one entity from another operation (spec v2).

    Records from ``from_operation`` are indexed by ``foreign_key``; each
    base record looks up ``local_key``. ``one`` attaches computed scalars
    from the first match under ``_joined.<as>``; ``many`` aggregates over
    all matches. The attached namespace is reserved: vendor records never
    carry ``_joined``, so enrichment cannot collide with wire data.
    """

    from_operation: str
    local_key: JsonPath
    foreign_key: JsonPath
    cardinality: Literal["one", "many"] = "one"
    as_: str = Field(alias="as", description="Namespace attached under _joined")
    fields: dict[str, JoinField] = Field(min_length=1)

    @field_validator("from_operation")
    @classmethod
    def _op_name(cls, v: str) -> str:
        if not OPERATION_NAME_RE.match(v):
            raise ValueError(f"join from_operation {v!r} must match [a-z][a-z0-9_]*")
        return v

    @field_validator("local_key", "foreign_key")
    @classmethod
    def _key_subset(cls, v: str) -> str:
        return check_jsonpath(v, "join key")

    @field_validator("as_")
    @classmethod
    def _as_name(cls, v: str) -> str:
        if not FIELD_NAME_RE.match(v):
            raise ValueError(f"join 'as' {v!r} must match [a-z][a-z0-9_]*")
        return v

    @field_validator("fields")
    @classmethod
    def _field_names(cls, v: dict[str, JoinField]) -> dict[str, JoinField]:
        for name in v:
            if not FIELD_NAME_RE.match(name):
                raise ValueError(f"join field name {name!r} must match [a-z][a-z0-9_]*")
        return v

    @model_validator(mode="after")
    def _cardinality_rules(self) -> JoinSpec:
        for name, jf in self.fields.items():
            if self.cardinality == "one":
                if jf.aggregate is not None:
                    raise ValueError(
                        f"join field {name!r}: 'aggregate' needs cardinality many"
                    )
            else:
                if jf.paths is not None:
                    raise ValueError(
                        f"join field {name!r}: 'paths' needs cardinality one"
                    )
                if jf.aggregate is None:
                    raise ValueError(
                        f"join field {name!r}: cardinality many needs 'aggregate'"
                    )
        return self


class EntitySpec(_Strict):
    """Mapping from one source operation to one canonical entity."""

    from_operation: str = Field(alias="from", description="Source operation name")
    key: JsonPath
    fields: dict[str, FieldMapping] = Field(min_length=1)
    checks: list[EntityCheck] = Field(default_factory=list)
    #: Declarative joins (spec v2): enrich base records from other
    #: operations by key before field mapping runs.
    joins: list[JoinSpec] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _key_subset(cls, v: str) -> str:
        return check_jsonpath(v, "entity key")

    @field_validator("fields")
    @classmethod
    def _field_names(cls, v: dict[str, FieldMapping]) -> dict[str, FieldMapping]:
        for name in v:
            if not FIELD_NAME_RE.match(name):
                raise ValueError(f"field name {name!r} must match [a-z][a-z0-9_]*")
        return v


class RetryPolicy(_Strict):
    # NOTE: named `statuses`, not `on` — a bare `on:` key parses as boolean
    # True under YAML 1.1, a trap no spec author should have to quote around.
    statuses: list[int] = Field(default_factory=lambda: [429, 503])
    backoff: Literal["exponential", "linear", "constant"] = "exponential"
    max_attempts: int = Field(default=5, ge=1, le=10)

    @field_validator("statuses")
    @classmethod
    def _statuses(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("retry.statuses must list at least one status")
        for s in v:
            if not 100 <= s <= 599:
                raise ValueError(f"retry status {s!r} is not a valid HTTP status")
        return v


class RateLimit(_Strict):
    max_rps: float = Field(default=5.0, gt=0)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


class AuthSpec(_Strict):
    """Credentials by reference only — never inline values (DIRECTIVES §6).

    ``body_fields`` (spec v2): ``{body field path: credential_ref}``. Each
    value is an env key name holding the secret; at runtime the secret is
    read from the environment and placed into the named JSON body field.

    ``edge_relay`` (spec v3): the connector is served by the on-premise
    edge relay queue instead of a vendor API. ``credential_ref`` names the
    env key holding the relay operator token (read at runtime, never
    stored); no vendor credential exists on this transport.
    """

    type: Literal[
        "bearer", "api_key_header", "basic", "oauth2_client_credentials", "body_fields", "edge_relay"
    ]
    credential_ref: str | None = Field(
        default=None, description="Env key name holding the secret, never the value"
    )
    header_name: str | None = None
    username: str | None = None
    token_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    body_fields: dict[str, str] | None = Field(
        default=None,
        description="Body field path -> credential_ref env key (body_fields auth only)",
    )

    @field_validator("credential_ref")
    @classmethod
    def _ref_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not CREDENTIAL_REF_RE.match(v):
            raise ValueError(f"credential_ref {v!r} must be an env key name like HARBOR_API_KEY")
        return v

    @field_validator("body_fields")
    @classmethod
    def _body_fields_shape(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        for field_path, ref in v.items():
            check_body_path(field_path, "auth body field")
            if not CREDENTIAL_REF_RE.match(ref):
                raise ValueError(f"auth body field {field_path!r} ref {ref!r} must be an env key name")
        if not v:
            raise ValueError("body_fields auth requires at least one body field")
        return v

    @model_validator(mode="after")
    def _per_type(self) -> AuthSpec:
        if self.type == "api_key_header" and not self.header_name:
            raise ValueError("api_key_header auth requires header_name")
        if self.type == "basic" and not self.username:
            raise ValueError("basic auth requires username (the password stays in credential_ref)")
        if self.type == "oauth2_client_credentials" and not self.token_url:
            raise ValueError("oauth2_client_credentials auth requires token_url")
        if self.type == "body_fields":
            if not self.body_fields:
                raise ValueError("body_fields auth requires body_fields mapping")
            if self.credential_ref is not None:
                raise ValueError("body_fields auth uses body_fields refs, not credential_ref")
        else:
            if self.body_fields is not None:
                raise ValueError(f"{self.type} auth must not set body_fields (body_fields auth only)")
            if self.credential_ref is None:
                raise ValueError(f"{self.type} auth requires credential_ref")
        return self


class TimezoneSource(_Strict):
    kind: Literal["static", "field"]
    value: str | None = None
    path: JsonPath | None = None

    @field_validator("path")
    @classmethod
    def _tz_path_subset(cls, v: str | None) -> str | None:
        return check_jsonpath(v, "timezone path") if v is not None else v

    @model_validator(mode="after")
    def _operands(self) -> TimezoneSource:
        if self.kind == "static":
            if not self.value or not TIMEZONE_RE.match(self.value):
                raise ValueError(f"static timezone {self.value!r} must be UTC or an IANA name")
        elif self.path is None:
            raise ValueError("field timezone_source requires path")
        return self


class LinkSpec(_Strict):
    """Framed-transport settings as data (spec v3, ``fias_tcp`` only).

    ``announced_records`` are the two-letter record codes the host tells
    the peer it wants to exchange (the ``LR`` announcement); the peer only
    sends those types, in the announced field order. ``ack_mode`` and
    ``max_frame_bytes`` are the installation's framing contract.
    """

    ack_mode: bool = True
    max_frame_bytes: int = Field(default=65536, ge=1)
    announced_records: list[str] = Field(
        min_length=1, description="Record codes the host announces interest in"
    )

    @field_validator("announced_records")
    @classmethod
    def _record_codes(cls, v: list[str]) -> list[str]:
        for code in v:
            if not RECORD_CODE_RE.match(code):
                raise ValueError(f"announced record {code!r} must be a two-letter code like PS")
        return v


class FeedSpec(_Strict):
    """An event-driven source (spec v3): records pushed to the agent.

    ``record`` names the framed record type consumed (e.g. ``PS``);
    ``via`` names the queue the control plane drains (the edge relay);
    ``dedupe_keys`` are wire field ids identifying one posting instance,
    so a resent record (same keys) is folded instead of doubled.
    """

    record: str = Field(description="Framed record type consumed, e.g. PS")
    via: Literal["edge_relay"] = Field(
        default="edge_relay", description="Queue the control plane drains for this feed"
    )
    dedupe_keys: list[str] = Field(
        default_factory=list, description="Wire field ids forming the idempotency key"
    )

    @field_validator("record")
    @classmethod
    def _record_code(cls, v: str) -> str:
        if not RECORD_CODE_RE.match(v):
            raise ValueError(f"feed record {v!r} must be a two-letter code like PS")
        return v

    @field_validator("dedupe_keys")
    @classmethod
    def _dedupe_ids(cls, v: list[str]) -> list[str]:
        for fid in v:
            if not WIRE_FIELD_ID_RE.match(fid):
                raise ValueError(f"feed dedupe key {fid!r} must be a two-character wire field id")
        return v


class SourceSpec(_Strict):
    base_url: str
    protocol: ProtocolName = Field(
        default="http",
        description="Transport: http (polled vendor API) or fias_tcp (event-driven framed protocol, spec v3)",
    )
    auth: AuthSpec
    rate_limit: RateLimit = Field(default_factory=RateLimit)
    timezone_source: TimezoneSource
    link: LinkSpec | None = Field(
        default=None,
        description="Framed-transport settings as data (spec v3, fias_tcp only)",
    )

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not v:
            raise ValueError("base_url must not be empty")
        if v.startswith("fias://"):
            # Scheme checked against protocol below; shape-checked here.
            bare = v[len("fias://") :]
            if not bare or " " in v or "$" in v:
                raise ValueError("fias:// base_url must look like fias://listen:5010")
            return v
        if not v.startswith(("http://", "https://", "${")):
            raise ValueError("base_url must be an http(s) URL or start with a ${ENV} placeholder")
        if EMBEDDED_CREDS_RE.search(v):
            raise ValueError("base_url must not embed credentials; use credential_ref")
        for name in ENV_PLACEHOLDER_RE.findall(v):
            if not CREDENTIAL_REF_RE.match(name):
                raise ValueError(f"env placeholder ${{{name}}} must be an UPPER_SNAKE key name")
        bare = ENV_PLACEHOLDER_RE.sub("", v)
        if "$" in bare:
            raise ValueError("base_url may only use ${ENV_KEY} placeholders, no bare $")
        return v

    @model_validator(mode="after")
    def _protocol_link(self) -> SourceSpec:
        if self.protocol == "fias_tcp":
            if not self.base_url.startswith("fias://"):
                raise ValueError("fias_tcp sources use a fias:// base_url (e.g. fias://listen:5010)")
            if self.link is None:
                raise ValueError("fias_tcp sources require link settings (framing, ACK mode, announced records)")
        else:
            if self.base_url.startswith("fias://"):
                raise ValueError("fias:// base_url requires protocol fias_tcp")
            if self.link is not None:
                raise ValueError("link settings require protocol fias_tcp")
        return self


class Pagination(_Strict):
    strategy: Literal["offset", "cursor", "page", "body_cursor"]
    cursor_param: str | None = None
    offset_param: str | None = None
    page_param: str | None = None
    limit_param: str | None = None
    next_path: JsonPath | None = None
    items_path: JsonPath
    total_path: JsonPath | None = None
    stop_when: list[StopCondition] = Field(min_length=1)
    # body_cursor (spec v2): cursor and page size live in named JSON body
    # paths going in, and the cursor comes back via next_path. Stop on null
    # cursor or a short page (fewer items than the requested count).
    cursor_path: str | None = Field(
        default=None, description="Dotted body path holding the request cursor (body_cursor only)"
    )
    count_path: str | None = Field(
        default=None, description="Dotted body path holding the page size (body_cursor only)"
    )
    count_value: int | None = Field(
        default=None, description="Page size sent at count_path (body_cursor only)"
    )

    @field_validator("next_path")
    @classmethod
    def _next_subset(cls, v: str | None) -> str | None:
        return check_jsonpath(v, "next_path") if v is not None else v

    @field_validator("total_path")
    @classmethod
    def _total_subset(cls, v: str | None) -> str | None:
        return check_jsonpath(v, "total_path") if v is not None else v

    @field_validator("items_path")
    @classmethod
    def _items_subset(cls, v: str) -> str:
        return check_jsonpath(v, "items_path")

    @field_validator("cursor_path", "count_path")
    @classmethod
    def _body_path_subset(cls, v: str | None) -> str | None:
        return check_body_path(v, "pagination body path") if v is not None else v

    @field_validator("count_value")
    @classmethod
    def _count_range(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 1000:
            raise ValueError(f"count_value {v!r} must be in 1..1000")
        return v

    @model_validator(mode="after")
    def _per_strategy(self) -> Pagination:
        if self.strategy == "cursor" and (not self.cursor_param or self.next_path is None):
            raise ValueError("cursor pagination requires cursor_param and next_path")
        if self.strategy == "offset" and not self.offset_param:
            raise ValueError("offset pagination requires offset_param")
        if self.strategy == "page" and not self.page_param:
            raise ValueError("page pagination requires page_param")
        if self.strategy == "body_cursor":
            if not self.cursor_path or self.next_path is None:
                raise ValueError("body_cursor pagination requires cursor_path and next_path")
        elif self.cursor_path is not None or self.count_path is not None or self.count_value is not None:
            raise ValueError(f"{self.strategy} pagination must not set cursor_path/count_path/count_value")
        return self


class Checkpoint(_Strict):
    field: JsonPath
    mode: Literal["max", "min"] = "max"

    @field_validator("field")
    @classmethod
    def _field_subset(cls, v: str) -> str:
        return check_jsonpath(v, "checkpoint field")


class OperationSpec(_Strict):
    method: Literal["GET", "POST", "EVENT"] = "GET"
    path: str = ""
    params: dict[str, str] = Field(default_factory=dict)
    # POST body template (spec v2): static JSON data sent as the request
    # body. Pagination (body_cursor) and body_fields auth inject their
    # fields into a copy per request; checkpoint ``{{checkpoint.X}}``
    # templates resolve the same way as query params.
    body: dict[str, Any] | None = None
    pagination: Pagination | None = None
    checkpoint: Checkpoint | None = None
    # Event feed (spec v3): records pushed to the agent instead of polled.
    # An EVENT operation names no path, body, pagination, checkpoint or
    # params — the feed declaration is the whole request surface.
    feed: FeedSpec | None = Field(
        default=None, description="Event-driven source (spec v3, EVENT method only)"
    )

    @field_validator("path")
    @classmethod
    def _relative(cls, v: str) -> str:
        if not v:
            # Empty only for EVENT feed operations (checked below).
            return v
        if not v.startswith("/") or " " in v or "$" in v:
            raise ValueError(f"operation path {v!r} must be a relative path like /v2/bookings")
        return v

    @model_validator(mode="after")
    def _method_body(self) -> OperationSpec:
        if self.method == "GET" and self.body is not None:
            raise ValueError("GET operations must not set body (POST only)")
        if self.feed is not None:
            if self.method != "EVENT":
                raise ValueError("feed operations use method EVENT")
            if self.path:
                raise ValueError("feed operations must not set path (the feed is the request surface)")
            if self.params:
                raise ValueError("feed operations must not set params")
            if self.body is not None:
                raise ValueError("feed operations must not set body")
            if self.pagination is not None:
                raise ValueError("feed operations must not set pagination (pushed records are not paged)")
            if self.checkpoint is not None:
                raise ValueError("feed operations must not set checkpoint (delivery is idempotent, not positional)")
        elif self.method == "EVENT":
            raise ValueError("method EVENT requires a feed declaration")
        elif not self.path:
            raise ValueError("HTTP operations require a relative path")
        return self


class CommandTemplate(_Strict):
    """One host-originated record template (spec v3): declarative command data.

    Mirrors the edge bundle's command shape so a spec can carry what the
    agent executes: ``record`` names the framed record type, ``fields``
    maps wire field ids to ``{param}`` templates, ``defaults`` fill
    missing params, ``now`` names params filled with the current
    property-local wire date (``date`` → ``YYMMDD``) or time (anything
    else → ``HHMMSS``), and ``expect`` is the answer waited for (``ack``
    or a two-letter record). Placeholders substitute strictly — a missing
    param rejects the command before anything reaches the link.
    """

    record: str = Field(description="Framed record type sent, e.g. WR")
    fields: dict[str, str] = Field(min_length=1, description="Wire field id -> {param} template")
    defaults: dict[str, str] = Field(default_factory=dict)
    now: list[str] = Field(default_factory=list)
    expect: str = Field(default="ack", description="ack or the two-letter answer record")

    @field_validator("record")
    @classmethod
    def _record_code(cls, v: str) -> str:
        if not RECORD_CODE_RE.match(v):
            raise ValueError(f"command record {v!r} must be a two-letter code like WR")
        return v

    @field_validator("fields")
    @classmethod
    def _field_templates(cls, v: dict[str, str]) -> dict[str, str]:
        for fid, template in v.items():
            if not WIRE_FIELD_ID_RE.match(fid):
                raise ValueError(f"command field id {fid!r} must be a two-character wire id")
            if not isinstance(template, str) or not template:
                raise ValueError(f"command field {fid!r} template must be a non-empty string")
            if "|" in template or "\x02" in template or "\x03" in template:
                raise ValueError(f"command field {fid!r} template must not carry framing characters")
            names = PLACEHOLDER_RE.findall(template)
            if not names:
                raise ValueError(
                    f"command field {fid!r} template must name at least one {{param}}"
                )
            stripped = PLACEHOLDER_RE.sub("", template)
            if "{" in stripped or "}" in stripped:
                raise ValueError(f"command field {fid!r} template has a malformed placeholder")
        return v

    @field_validator("expect")
    @classmethod
    def _expect_shape(cls, v: str) -> str:
        if v != "ack" and not RECORD_CODE_RE.match(v):
            raise ValueError(f"command expect {v!r} must be 'ack' or a two-letter record code")
        return v


class ConnectorMeta(_Strict):
    id: str
    vendor: str = Field(min_length=1)
    runtime_target: Literal["cloud", "local", "sandbox", "edge"] = "cloud"

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not CONNECTOR_ID_RE.match(v):
            raise ValueError(f"connector id {v!r} must be a slug [a-z0-9_-]")
        return v


class Capabilities(_Strict):
    read: list[str] = Field(min_length=1)
    action: list[str] = Field(default_factory=list)


class Canary(_Strict):
    every: str
    operation: str
    max_pages: int = Field(default=1, ge=1)

    @field_validator("every")
    @classmethod
    def _interval(cls, v: str) -> str:
        if not INTERVAL_RE.match(v):
            raise ValueError(f"canary.every {v!r} must look like 60s, 5m or 1h")
        return v


class Fingerprint(_Strict):
    keys: bool = True
    types: bool = True
    pagination_shape: bool = True


class HealthSpec(_Strict):
    canary: Canary
    fingerprint: Fingerprint = Field(default_factory=Fingerprint)


class ConnectorSpec(_Strict):
    """The declarative connector document (spec_version 1 | 2 | 3).

    Version 2 adds POST operations with a JSON body template, ``body_fields``
    auth, and ``body_cursor`` pagination. Version 3 adds the event-driven
    transport: ``protocol: fias_tcp`` sources with ``link`` settings, EVENT
    operations over event feeds, multi-path field mapping, and ``commands``
    templates. Every version 1 document still validates and behaves exactly
    as before, and every version 2 document still validates and behaves
    exactly as before.
    """

    spec_version: Literal[1, 2, 3]
    connector: ConnectorMeta
    source: SourceSpec
    operations: dict[str, OperationSpec] = Field(min_length=1)
    entities: dict[str, EntitySpec] = Field(min_length=1)
    capabilities: Capabilities
    health: HealthSpec
    commands: dict[str, CommandTemplate] = Field(
        default_factory=dict,
        description="Host-originated record templates, keyed by command kind (spec v3)",
    )

    @field_validator("operations")
    @classmethod
    def _op_names(cls, v: dict[str, OperationSpec]) -> dict[str, OperationSpec]:
        for name in v:
            if not OPERATION_NAME_RE.match(name):
                raise ValueError(f"operation name {name!r} must match [a-z][a-z0-9_]*")
        return v

    @field_validator("commands")
    @classmethod
    def _command_names(cls, v: dict[str, CommandTemplate]) -> dict[str, CommandTemplate]:
        for name in v:
            if not COMMAND_NAME_RE.match(name):
                raise ValueError(f"command kind {name!r} must match [a-z][a-z0-9_.]*")
        return v

    @field_validator("entities")
    @classmethod
    def _entity_names(cls, v: dict[str, EntitySpec]) -> dict[str, EntitySpec]:
        for name in v:
            if not ENTITY_NAME_RE.match(name):
                raise ValueError(f"entity name {name!r} must be PascalCase")
        return v

    @model_validator(mode="after")
    def _v1_vocabulary(self) -> ConnectorSpec:
        """Version 1 cannot express POST, body templates, body_fields auth, body_cursor, joins or v3."""
        if self.spec_version == 1:
            self._reject_newer_than(1)
        return self

    @model_validator(mode="after")
    def _v2_vocabulary(self) -> ConnectorSpec:
        """Version 2 is HTTP-only: it cannot express the v3 event-driven transport."""
        if self.spec_version == 2:
            self._reject_newer_than(2)
        return self

    def _reject_newer_than(self, version: int) -> None:
        """Reject constructs newer than *version* (keeps old vocabularies frozen)."""
        for op_name, op in self.operations.items():
            if op.method == "EVENT" or op.feed is not None:
                raise ValueError(
                    f"operations.{op_name}: event feeds require spec_version 3"
                )
        if version < 2:
            if self.source.auth.type == "body_fields":
                raise ValueError("body_fields auth requires spec_version 2")
            for op_name, op in self.operations.items():
                if op.method != "GET":
                    raise ValueError(
                        f"operations.{op_name}: method {op.method!r} requires spec_version 2"
                    )
                if op.body is not None:
                    raise ValueError(f"operations.{op_name}: body requires spec_version 2")
                if op.pagination is not None and op.pagination.strategy == "body_cursor":
                    raise ValueError(
                        f"operations.{op_name}: body_cursor pagination requires spec_version 2"
                    )
            for entity_name, entity in self.entities.items():
                if entity.joins:
                    raise ValueError(
                        f"entities.{entity_name}: joins require spec_version 2"
                    )
        if version < 3:
            if self.source.protocol != "http":
                raise ValueError(
                    f"protocol {self.source.protocol!r} requires spec_version 3"
                )
            if self.source.link is not None:
                raise ValueError("link settings require spec_version 3")
            if self.source.auth.type == "edge_relay":
                raise ValueError("edge_relay auth requires spec_version 3")
            for op_name, op in self.operations.items():
                if op.feed is not None:
                    raise ValueError(
                        f"operations.{op_name}: event feeds require spec_version 3"
                    )
            for entity_name, entity in self.entities.items():
                for field_name, field in entity.fields.items():
                    if field.paths is not None:
                        raise ValueError(
                            f"entities.{entity_name}.fields.{field_name}: "
                            "multi-path mapping requires spec_version 3"
                        )
            if self.commands:
                raise ValueError("command templates require spec_version 3")

    @model_validator(mode="after")
    def _cross_refs(self) -> ConnectorSpec:
        for entity_name, entity in self.entities.items():
            if entity.from_operation not in self.operations:
                raise ValueError(
                    f"entities.{entity_name}.from: "
                    f"unknown operation {entity.from_operation!r}"
                )
            for join in entity.joins:
                if join.from_operation not in self.operations:
                    raise ValueError(
                        f"entities.{entity_name}.joins.{join.as_}: "
                        f"unknown operation {join.from_operation!r}"
                    )
            for check in entity.checks:
                if check.kind == "date_order":
                    assert check.before is not None and check.after is not None
                    for which in (check.before, check.after):
                        if which not in entity.fields:
                            raise ValueError(
                                f"entities.{entity_name}: date_order references "
                                f"unknown field {which!r}"
                            )
                if (
                    check.kind in ("money_invariant", "count_invariant")
                    and check.field is not None
                    and check.field not in entity.fields
                ):
                    raise ValueError(
                        f"entities.{entity_name}: {check.kind} references "
                        f"unknown field {check.field!r}"
                    )
                if check.kind == "business_date_invariant":
                    assert check.field is not None and check.reference_field is not None
                    for which in (check.field, check.reference_field):
                        if which not in entity.fields:
                            raise ValueError(
                                f"entities.{entity_name}: business_date_invariant references "
                                f"unknown field {which!r}"
                            )
        for name in self.capabilities.read:
            if name not in self.entities:
                raise ValueError(f"capabilities.read: unknown entity {name!r}")
        if self.commands:
            for name in self.capabilities.action:
                if name not in self.commands:
                    raise ValueError(
                        f"capabilities.action: unknown command {name!r} "
                        "(action names must be keys of commands)"
                    )
        if self.health.canary.operation not in self.operations:
            raise ValueError(
                f"health.canary.operation: unknown operation "
                f"{self.health.canary.operation!r}"
            )
        return self


def load_spec_from_dict(data: dict[str, Any]) -> ConnectorSpec:
    """Validate a plain mapping into a :class:`ConnectorSpec`."""
    if not isinstance(data, dict):
        raise TypeError(f"spec must be a mapping, got {type(data).__name__}")
    return ConnectorSpec.model_validate(data)


def load_spec_from_yaml(path: str | Path) -> ConnectorSpec:
    """Load a YAML file and validate it into a :class:`ConnectorSpec`."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}") from e
    try:
        return load_spec_from_dict(data)
    except TypeError as e:
        raise ValueError(f"{path}: {e}") from e


def spec_to_json_data(spec: ConnectorSpec) -> dict[str, Any]:
    """Dump a spec to JSON-compatible data ( aliases kept: ``from`` )."""
    return spec.model_dump(mode="json", by_alias=True)


def signature_canonical_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Strip spec-v2/v3-only fields at their defaults for signature hashing.

    REQ-020 sub-steps 5a/5c added ``operations.*.body``, ``body_fields``
    auth, ``body_cursor`` pagination paths (``cursor_path``/``count_path``/
    ``count_value``) and ``entities.*.joins``. REQ-022 sub-step C adds the
    v3 vocabulary: ``source.protocol``/``source.link``, ``edge_relay``
    auth, EVENT ``operations.*.feed``, ``entities.*.fields.*.paths`` and
    ``commands``. A v1 spec always carries them at their defaults — the
    vocabulary gates (``_v1_vocabulary``/``_v2_vocabulary``) reject anything
    else — but ``model_dump`` emits them, so hashing the raw dump changed
    every v1 signature (REQ-020 owner check 2026-10-28: committed reference
    releases stopped verifying). Dropping defaulted newer-version keys
    restores the exact pre-v2 canonical bytes; a spec that actually sets
    one of them still changes the signature. The same holds for v2 specs
    and v3-only keys.
    """
    import copy

    snap = copy.deepcopy(dict(data))
    operations = snap.get("operations")
    if isinstance(operations, dict):
        for op in operations.values():
            if not isinstance(op, dict):
                continue
            if op.get("body") is None and "body" in op:
                del op["body"]
            if op.get("feed") is None and "feed" in op:
                del op["feed"]
            if not op.get("path") and "path" in op:
                del op["path"]
            pagination = op.get("pagination")
            if isinstance(pagination, dict):
                for key in ("cursor_path", "count_path", "count_value"):
                    if pagination.get(key) is None and key in pagination:
                        del pagination[key]
    source = snap.get("source")
    if isinstance(source, dict):
        if source.get("protocol", "http") == "http" and "protocol" in source:
            del source["protocol"]
        if source.get("link") is None and "link" in source:
            del source["link"]
        auth = source.get("auth")
        if isinstance(auth, dict) and auth.get("body_fields") is None and "body_fields" in auth:
            del auth["body_fields"]
    entities = snap.get("entities")
    if isinstance(entities, dict):
        for entity in entities.values():
            if not isinstance(entity, dict):
                continue
            if not entity.get("joins") and "joins" in entity:
                del entity["joins"]
            fields = entity.get("fields")
            if isinstance(fields, dict):
                for field in fields.values():
                    if not isinstance(field, dict):
                        continue
                    if field.get("path") is None and "path" in field:
                        del field["path"]
                    if field.get("paths") is None and "paths" in field:
                        del field["paths"]
    if not snap.get("commands") and "commands" in snap:
        del snap["commands"]
    return snap


def format_validation_errors(error: Any) -> list[str]:
    """Render a pydantic ``ValidationError`` as dotted-path strings."""
    lines: list[str] = []
    for err in error.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"{loc}: {err['msg']}")
    return lines


def default_schema_path() -> Path:
    """Repo ``schemas/connector-spec.v3.json`` resolved from this file."""
    return Path(__file__).resolve().parents[3] / "schemas" / SCHEMA_FILENAME


def validate_against_schema_file(
    data: dict[str, Any], schema_path: str | Path | None = None
) -> None:
    """Validate dumped spec data against the committed JSON Schema file."""
    import json

    import jsonschema

    path = Path(schema_path) if schema_path is not None else default_schema_path()
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)
