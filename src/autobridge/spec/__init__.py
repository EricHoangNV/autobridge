"""ConnectorSpec package — declarative connector documents, JSON Patch repairs, releases."""

from autobridge.health import healthz
from autobridge.spec.models import (
    TRANSFORMS,
    ConnectorSpec,
    format_validation_errors,
    load_spec_from_dict,
    load_spec_from_yaml,
    spec_to_json_data,
    validate_against_schema_file,
)
from autobridge.spec.patch import (
    PatchError,
    SpecValidationError,
    apply_patch,
    diff_specs,
    reverse_patch,
    validate_patch_shape,
)
from autobridge.spec.registry import (
    ReleaseCorruptError,
    ReleaseExistsError,
    ReleaseNotFoundError,
    list_releases,
    load_release,
    release_exists,
    spec_signature,
    store_release,
)

__all__ = [
    "TRANSFORMS",
    "ConnectorSpec",
    "PatchError",
    "ReleaseCorruptError",
    "ReleaseExistsError",
    "ReleaseNotFoundError",
    "SpecValidationError",
    "apply_patch",
    "diff_specs",
    "format_validation_errors",
    "healthz",
    "list_releases",
    "load_release",
    "load_spec_from_dict",
    "load_spec_from_yaml",
    "release_exists",
    "reverse_patch",
    "spec_signature",
    "spec_to_json_data",
    "store_release",
    "validate_against_schema_file",
    "validate_patch_shape",
]
