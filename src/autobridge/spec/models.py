"""ConnectorSpec v1 pydantic models (P1.W1.3, plan §2.4).

A connector is a declarative document the runtime interprets — never code.
Every spec validates against ``schemas/connector-spec.v1.json`` before it is
stored, executed or patched; invalid specs are rejected with path-level
errors. Repairs are JSON Patches on the spec (see :mod:`autobridge.spec.patch`).

Secrets policy: auth carries ``credential_ref`` (an env key name) only —
inline secret values are rejected, as are credentials embedded in URLs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

SCHEMA_FILENAME = "connector-spec.v1.json"


def check_jsonpath(value: str, what: str = "path") -> str:
    """Validate the JSONPath subset; raise ``ValueError`` naming the field."""
    if not JSONPATH_SUBSET_RE.match(value):
        raise ValueError(
            f"{what} must use the JSONPath subset "
            f"(e.g. $.data, $.meta.next_cursor) with no filters or expressions; "
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
    """One canonical field: source JSONPath plus transform pipeline."""

    path: JsonPath
    transform: list[TransformStep] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _path_subset(cls, v: str) -> str:
        return check_jsonpath(v, "field path")

    @field_validator("transform", mode="before")
    @classmethod
    def _normalize_transform(cls, v: Any) -> Any:
        if isinstance(v, list) and v and all(isinstance(s, TransformStep) for s in v):
            return v
        return normalize_transform(v)


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


class EntitySpec(_Strict):
    """Mapping from one source operation to one canonical entity."""

    from_operation: str = Field(alias="from", description="Source operation name")
    key: JsonPath
    fields: dict[str, FieldMapping] = Field(min_length=1)
    checks: list[EntityCheck] = Field(default_factory=list)

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
    """Credentials by reference only — never inline values (DIRECTIVES §6)."""

    type: Literal["bearer", "api_key_header", "basic", "oauth2_client_credentials"]
    credential_ref: str = Field(description="Env key name holding the secret, never the value")
    header_name: str | None = None
    username: str | None = None
    token_url: str | None = None
    scopes: list[str] = Field(default_factory=list)

    @field_validator("credential_ref")
    @classmethod
    def _ref_shape(cls, v: str) -> str:
        if not CREDENTIAL_REF_RE.match(v):
            raise ValueError(f"credential_ref {v!r} must be an env key name like HARBOR_API_KEY")
        return v

    @model_validator(mode="after")
    def _per_type(self) -> AuthSpec:
        if self.type == "api_key_header" and not self.header_name:
            raise ValueError("api_key_header auth requires header_name")
        if self.type == "basic" and not self.username:
            raise ValueError("basic auth requires username (the password stays in credential_ref)")
        if self.type == "oauth2_client_credentials" and not self.token_url:
            raise ValueError("oauth2_client_credentials auth requires token_url")
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


class SourceSpec(_Strict):
    base_url: str
    auth: AuthSpec
    rate_limit: RateLimit = Field(default_factory=RateLimit)
    timezone_source: TimezoneSource

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not v or not v.startswith(("http://", "https://", "${")):
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


class Pagination(_Strict):
    strategy: Literal["offset", "cursor", "page"]
    cursor_param: str | None = None
    offset_param: str | None = None
    page_param: str | None = None
    limit_param: str | None = None
    next_path: JsonPath | None = None
    items_path: JsonPath
    stop_when: list[StopCondition] = Field(min_length=1)

    @field_validator("next_path")
    @classmethod
    def _next_subset(cls, v: str | None) -> str | None:
        return check_jsonpath(v, "next_path") if v is not None else v

    @field_validator("items_path")
    @classmethod
    def _items_subset(cls, v: str) -> str:
        return check_jsonpath(v, "items_path")

    @model_validator(mode="after")
    def _per_strategy(self) -> Pagination:
        if self.strategy == "cursor" and (not self.cursor_param or self.next_path is None):
            raise ValueError("cursor pagination requires cursor_param and next_path")
        if self.strategy == "offset" and not self.offset_param:
            raise ValueError("offset pagination requires offset_param")
        if self.strategy == "page" and not self.page_param:
            raise ValueError("page pagination requires page_param")
        return self


class Checkpoint(_Strict):
    field: JsonPath
    mode: Literal["max", "min"] = "max"

    @field_validator("field")
    @classmethod
    def _field_subset(cls, v: str) -> str:
        return check_jsonpath(v, "checkpoint field")


class OperationSpec(_Strict):
    method: Literal["GET"] = "GET"
    path: str
    params: dict[str, str] = Field(default_factory=dict)
    pagination: Pagination | None = None
    checkpoint: Checkpoint | None = None

    @field_validator("path")
    @classmethod
    def _relative(cls, v: str) -> str:
        if not v.startswith("/") or " " in v or "$" in v:
            raise ValueError(f"operation path {v!r} must be a relative path like /v2/bookings")
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
    """The declarative connector document (spec_version 1)."""

    spec_version: Literal[1]
    connector: ConnectorMeta
    source: SourceSpec
    operations: dict[str, OperationSpec] = Field(min_length=1)
    entities: dict[str, EntitySpec] = Field(min_length=1)
    capabilities: Capabilities
    health: HealthSpec

    @field_validator("operations")
    @classmethod
    def _op_names(cls, v: dict[str, OperationSpec]) -> dict[str, OperationSpec]:
        for name in v:
            if not OPERATION_NAME_RE.match(name):
                raise ValueError(f"operation name {name!r} must match [a-z][a-z0-9_]*")
        return v

    @field_validator("entities")
    @classmethod
    def _entity_names(cls, v: dict[str, EntitySpec]) -> dict[str, EntitySpec]:
        for name in v:
            if not ENTITY_NAME_RE.match(name):
                raise ValueError(f"entity name {name!r} must be PascalCase")
        return v

    @model_validator(mode="after")
    def _cross_refs(self) -> ConnectorSpec:
        for entity_name, entity in self.entities.items():
            if entity.from_operation not in self.operations:
                raise ValueError(
                    f"entities.{entity_name}.from: "
                    f"unknown operation {entity.from_operation!r}"
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


def format_validation_errors(error: Any) -> list[str]:
    """Render a pydantic ``ValidationError`` as dotted-path strings."""
    lines: list[str] = []
    for err in error.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"{loc}: {err['msg']}")
    return lines


def default_schema_path() -> Path:
    """Repo ``schemas/connector-spec.v1.json`` resolved from this file."""
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
