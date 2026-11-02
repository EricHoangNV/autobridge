"""REQ-083 C13: date-only values are calendar dates; wall-time default.

Tests run with a non-UTC ``context_tz`` so a stray timezone conversion
fails loudly: with ``America/New_York`` in force, a midnight-UTC
assumption would shift every date-only value back a day.
"""

from __future__ import annotations

CTX = "America/New_York"


def test_date_only_input_format_returns_calendar_date() -> None:
    from autobridge.runtime.transforms.business_date import transform_business_date
    from autobridge.runtime.transforms.date_local import transform_date_local

    # "%m-%d-%Y" carries no time: unchanged, even though midnight UTC
    # would land the previous day in New York.
    assert (
        transform_date_local("10-15-2026", {"input_format": "%m-%d-%Y"}, context_tz=CTX)
        == "2026-10-15"
    )
    assert (
        transform_business_date("10-15-2026", {"input_format": "%m-%d-%Y"}, context_tz=CTX)
        == "2026-10-15"
    )
    assert (
        transform_date_local("15/10/2026", {"input_format": "%d/%m/%Y"}, context_tz=CTX)
        == "2026-10-15"
    )


def test_date_only_wire_value_returns_calendar_date() -> None:
    from autobridge.runtime.transforms.business_date import transform_business_date
    from autobridge.runtime.transforms.date_local import transform_date_local

    # YYMMDD date-only wire value: unchanged (no cutoff either).
    assert transform_date_local("261015", {}, context_tz=CTX) == "2026-10-15"
    assert (
        transform_business_date("261015", {"cutoff": 4}, context_tz=CTX) == "2026-10-15"
    )


def test_time_bearing_input_format_without_offset_is_vendor_wall_time() -> None:
    from autobridge.runtime.transforms.business_date import transform_business_date
    from autobridge.runtime.transforms.date_local import transform_date_local

    # "%Y-%m-%d %H:%M:%S" with no offset: vendor wall time in the source
    # zone (naive_as defaults to source on this path), then converted.
    out = transform_date_local(
        "2026-10-15 12:00:00", {"input_format": "%Y-%m-%d %H:%M:%S", "tz": "UTC"},
        context_tz=CTX,
    )
    assert out == "2026-10-15"
    # An explicit naive_as=utc keeps the legacy assumption.
    out_utc = transform_date_local(
        "2026-10-15 00:30:00",
        {"input_format": "%Y-%m-%d %H:%M:%S", "tz": "UTC", "naive_as": "utc"},
        context_tz=CTX,
    )
    assert out_utc == "2026-10-15"
    # business_date applies the cutoff to wall-time-derived local times.
    assert (
        transform_business_date(
            "2026-10-15 02:00:00",
            {"input_format": "%Y-%m-%d %H:%M:%S", "cutoff": 4},
            context_tz=CTX,
        )
        == "2026-10-14"
    )


def test_datetime_utc_keeps_midnight_in_source_zone() -> None:
    from autobridge.runtime.transforms.datetime_utc import transform_datetime_utc

    # Pure dates still mean midnight in the source zone (unchanged).
    assert transform_datetime_utc("2026-10-15", {"tz": "source"}, context_tz=CTX) == (
        "2026-10-15T04:00:00+00:00"
    )
