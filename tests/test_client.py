"""Tests for the CoAP client helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.philips_airpurifier.client import (
    async_create_client,
    async_fetch_device_info,
    async_fetch_status,
    async_fetch_status_with_nudge,
)
from custom_components.philips_airpurifier.const import Protocol

_CLIENT = "custom_components.philips_airpurifier.client"


async def _aiter(items: list[Any]) -> AsyncIterator[Any]:
    """Yield the given items as an async iterator."""
    for item in items:
        yield item


async def _aiter_raises(exc: Exception) -> AsyncIterator[Any]:
    """Raise the given exception on first iteration (after yielding nothing)."""
    if False:  # pragma: no cover - makes this a generator without yielding
        yield None
    raise exc


async def test_async_fetch_device_info_returns_library_info() -> None:
    """Device info comes from CoAPClient.get_device_info with sync disabled."""
    info = {"modelid": "CX7550/01", "name": "Büro", "device_id": "abc"}
    client = MagicMock()
    client.get_device_info = AsyncMock(return_value=info)
    client.shutdown = AsyncMock()
    create = AsyncMock(return_value=client)

    result = await async_fetch_device_info("1.2.3.4", create_client=create)

    assert result == info
    create.assert_awaited_once_with("1.2.3.4", sync=False)
    client.shutdown.assert_awaited()


async def test_async_fetch_device_info_shuts_down_on_read_error() -> None:
    """A failing get_device_info still shuts the client down and propagates."""
    client = MagicMock()
    client.get_device_info = AsyncMock(side_effect=RuntimeError("read rejected"))
    client.shutdown = AsyncMock()
    create = AsyncMock(return_value=client)

    with pytest.raises(RuntimeError, match="read rejected"):
        await async_fetch_device_info("1.2.3.4", create_client=create)

    client.shutdown.assert_awaited()


async def test_async_fetch_device_info_propagates_create_failure() -> None:
    """A failing client creation propagates and no client is created to shut down."""
    create = AsyncMock(side_effect=TimeoutError("connect timed out"))

    with pytest.raises(TimeoutError, match="connect timed out"):
        await async_fetch_device_info("1.2.3.4", create_client=create)

    create.assert_awaited_once_with("1.2.3.4", sync=False)


async def test_async_fetch_status_with_nudge_success() -> None:
    """Test the observe-plus-nudge fetch returns the first pushed status."""
    status = {"D01S05": "CX7550/01", "D03102": 1}
    client = MagicMock()
    client.observe_status = MagicMock(return_value=_aiter([status]))
    client.set_control_value = AsyncMock()
    client.shutdown = AsyncMock()

    with (
        patch(f"{_CLIENT}.async_create_client", AsyncMock(return_value=client)),
        patch(f"{_CLIENT}._NUDGE_REGISTER_DELAY", 0),
    ):
        result = await async_fetch_status_with_nudge("1.2.3.4", [("D03105", 0), ("D03105", 115)])

    assert result == status
    client.set_control_value.assert_awaited()
    client.shutdown.assert_awaited()


async def test_async_fetch_status_with_nudge_timeout() -> None:
    """Test the nudge fetch raises a descriptive TimeoutError when no push arrives."""
    client = MagicMock()
    client.observe_status = MagicMock(return_value=_aiter([]))
    client.set_control_value = AsyncMock()
    client.shutdown = AsyncMock()

    with (
        patch(f"{_CLIENT}.async_create_client", AsyncMock(return_value=client)),
        patch(f"{_CLIENT}._NUDGE_REGISTER_DELAY", 0),
        patch(f"{_CLIENT}._NUDGE_WAIT_TIMEOUT", 0.01),
        pytest.raises(TimeoutError, match="no status push from 1.2.3.4"),
    ):
        await async_fetch_status_with_nudge("1.2.3.4", [("D03105", 0)])

    client.shutdown.assert_awaited()


async def test_async_fetch_status_with_nudge_write_failure_is_logged() -> None:
    """A failing control write is swallowed; a later push still succeeds."""
    status = {"D01S05": "CX7550/01"}
    client = MagicMock()
    client.observe_status = MagicMock(return_value=_aiter([status]))
    client.set_control_value = AsyncMock(side_effect=RuntimeError("write rejected"))
    client.shutdown = AsyncMock()

    with (
        patch(f"{_CLIENT}.async_create_client", AsyncMock(return_value=client)),
        patch(f"{_CLIENT}._NUDGE_REGISTER_DELAY", 0),
    ):
        result = await async_fetch_status_with_nudge("1.2.3.4", [("D03105", 0)])

    assert result == status
    client.set_control_value.assert_awaited()
    client.shutdown.assert_awaited()


async def test_async_fetch_status_with_nudge_observe_error_is_logged() -> None:
    """An observe-stream error is swallowed and surfaces as a nudge timeout."""
    client = MagicMock()
    client.observe_status = MagicMock(return_value=_aiter_raises(RuntimeError("stream died")))
    client.set_control_value = AsyncMock()
    client.shutdown = AsyncMock()

    with (
        patch(f"{_CLIENT}.async_create_client", AsyncMock(return_value=client)),
        patch(f"{_CLIENT}._NUDGE_REGISTER_DELAY", 0),
        patch(f"{_CLIENT}._NUDGE_WAIT_TIMEOUT", 0.01),
        pytest.raises(TimeoutError, match="no status push from 1.2.3.4"),
    ):
        await async_fetch_status_with_nudge("1.2.3.4", [("D03105", 0)])

    client.shutdown.assert_awaited()


# ---------------------------------------------------------------------------
# Transport dispatch
# ---------------------------------------------------------------------------


async def test_create_client_defaults_to_coap() -> None:
    """Without a protocol the CoAP client is used, as it always was."""
    coap_client = MagicMock()
    create = AsyncMock(return_value=coap_client)

    result = await async_create_client("1.2.3.4", create_client=create)

    assert result is coap_client
    create.assert_awaited_once_with("1.2.3.4")


async def test_create_client_builds_an_http_client() -> None:
    """The HTTP protocol creates a client bound to the given session."""
    http_client = MagicMock()
    create = AsyncMock(return_value=http_client)
    session = MagicMock()

    result = await async_create_client(
        "1.2.3.4",
        create_client=create,
        protocol=Protocol.HTTP,
        session=session,
    )

    assert result is http_client
    create.assert_awaited_once_with("1.2.3.4", session)


async def test_create_client_requires_a_session_for_http() -> None:
    """HTTP without a session is a programming error, not a device failure."""
    with pytest.raises(ValueError, match="aiohttp session is required"):
        await async_create_client("1.2.3.4", protocol=Protocol.HTTP)


async def test_fetch_status_over_http_shuts_the_client_down() -> None:
    """A one-shot HTTP read closes its temporary client."""
    client = MagicMock()
    client.get_status = AsyncMock(return_value=({"pwr": "1"}, 30))
    client.shutdown = AsyncMock()

    status = await async_fetch_status(
        "1.2.3.4",
        create_client=AsyncMock(return_value=client),
        protocol=Protocol.HTTP,
        session=MagicMock(),
    )

    assert status == {"pwr": "1"}
    client.get_status.assert_awaited_once_with(observe=False)
    client.shutdown.assert_awaited_once()


async def test_async_fetch_device_info_bounds_the_read() -> None:
    """A host with no CoAP stack must not hang the caller forever.

    Creating the client with ``sync=False`` performs no network I/O and returns
    immediately, so bounding only the creation guarded nothing: the read goes
    out with aiocoap's ``Unreliable`` transport tuning, which neither
    retransmits nor gives up. Against the AC2889 this hung indefinitely and the
    config flow never reached its HTTP fallback.
    """
    client = MagicMock()
    never_answers = asyncio.Event()
    client.get_device_info = AsyncMock(side_effect=never_answers.wait)
    client.shutdown = AsyncMock()

    with pytest.raises(TimeoutError):
        await async_fetch_device_info(
            "1.2.3.4",
            timeout=0.01,
            create_client=AsyncMock(return_value=client),
        )

    client.shutdown.assert_awaited_once()
