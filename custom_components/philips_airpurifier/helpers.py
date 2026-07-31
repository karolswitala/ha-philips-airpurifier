"""Helper functions for Philips air purifier status."""

from typing import Any

from .const import (
    FILTER_CAPACITY_BY_KEY,
    FILTER_CAPACITY_BY_TYPE,
    FILTER_TYPES,
    FanAttributes,
    PhilipsApi,
)


def extract_name(status: dict[str, Any]) -> str:
    """Extract the name from the status."""
    for name_key in [PhilipsApi.NAME, PhilipsApi.NEW_NAME, PhilipsApi.NEW2_NAME]:
        name = status.get(name_key)
        if name:
            return name
    return ""


def resolve_filter_capacity(status: dict[str, Any], status_key: str) -> int | None:
    """Return the capacity in hours of the filter behind ``status_key``.

    A capacity reported by the device always wins. Devices on the legacy HTTP
    API report none, so fall back to the nominal lifetime for the filter type
    they do report, and finally to a per-field default. Returns ``None`` when
    the capacity is genuinely unknown, which callers must not treat as zero.
    """
    description = FILTER_TYPES.get(status_key)
    if description is None:
        return None

    total_key = description[FanAttributes.TOTAL]
    if total_key in status:
        # The device tracks a capacity itself, so trust it and do not fall back
        # to a nominal value. A zero means it reports none for this filter.
        total = status[total_key]
        return total if isinstance(total, int) and total > 0 else None

    type_key = description[FanAttributes.TYPE]
    if type_key:
        filter_type = status.get(type_key)
        if isinstance(filter_type, str):
            capacity = FILTER_CAPACITY_BY_TYPE.get(filter_type)
            if capacity is not None:
                return capacity

    return FILTER_CAPACITY_BY_KEY.get(status_key)


def extract_model(status: dict[str, Any]) -> str:
    """Extract the model from the status."""
    for model_key in [
        PhilipsApi.MODEL_ID,
        PhilipsApi.NEW_MODEL_ID,
        PhilipsApi.NEW2_MODEL_ID,
    ]:
        model = status.get(model_key)
        if model:
            return model[:9]
    return ""
