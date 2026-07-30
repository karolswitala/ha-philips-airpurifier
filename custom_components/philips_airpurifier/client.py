"""Client helpers for Philips Air Purifier device communication.

Devices speak one of two transports: encrypted CoAP (via the upstream
``philips-airctrl`` library) or the legacy HTTP API (:mod:`.http_client`). The
helpers here dispatch on :class:`~.const.Protocol` so callers only deal with one
API; the nudge and device-info helpers are CoAP-only, as those quirks have no
HTTP equivalent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, cast

from philips_airctrl import CoAPClient

from .const import Protocol
from .http_client import HTTPClient

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

type DeviceClient = CoAPClient | HTTPClient

# Delay before nudging, to let the observation register on the device.
_NUDGE_REGISTER_DELAY = 2.0
# How long to wait for a push after each nudge, and how many times to nudge.
_NUDGE_WAIT_TIMEOUT = 12.0
_NUDGE_ATTEMPTS = 2


async def async_create_client(
    host: str,
    timeout: float = 25,
    create_client: Any | None = None,
    protocol: Protocol = Protocol.COAP,
    session: ClientSession | None = None,
) -> DeviceClient:
    """Create a client for a host with timeout protection.

    ``protocol`` selects the transport; HTTP additionally requires ``session``,
    which must be Home Assistant's shared aiohttp session.
    """
    if protocol is Protocol.HTTP:
        if session is None:
            msg = "An aiohttp session is required for the HTTP protocol"
            raise ValueError(msg)
        http_creator = create_client or HTTPClient.create
        return await asyncio.wait_for(http_creator(host, session), timeout=timeout)
    creator = create_client or CoAPClient.create
    return await asyncio.wait_for(creator(host), timeout=timeout)


async def async_fetch_status(
    host: str,
    connect_timeout: float = 30,
    status_timeout: float = 30,
    create_client: Any | None = None,
    protocol: Protocol = Protocol.COAP,
    session: ClientSession | None = None,
) -> dict[str, Any]:
    """Fetch current status using a temporary client and shut it down.

    Over CoAP this uses ``observe=False`` (philips-airctrl >= 1.1.0) so the
    one-shot read does not leave an observation registered on the device.
    """
    client = await async_create_client(
        host,
        timeout=connect_timeout,
        create_client=create_client,
        protocol=protocol,
        session=session,
    )
    try:
        status, _ = await asyncio.wait_for(client.get_status(observe=False), timeout=status_timeout)
        return status
    finally:
        with contextlib.suppress(Exception):
            await client.shutdown()


async def async_fetch_device_info(
    host: str,
    timeout: float = 15,
    create_client: Any | None = None,
) -> dict[str, Any]:
    """Fetch the plaintext ``sys/dev/info`` resource (model id, name, device id).

    ``CoAPClient.create(host, sync=False)`` skips the encrypted sync handshake,
    which is required for push-only firmware that never answers the
    ``sys/dev/status`` read, so this identifies a device whose status cannot be
    read directly.
    """
    creator = create_client or CoAPClient.create
    client = await asyncio.wait_for(creator(host, sync=False), timeout=timeout)
    try:
        return await client.get_device_info()
    finally:
        with contextlib.suppress(Exception):
            await client.shutdown()


async def async_fetch_status_with_nudge(
    host: str,
    nudge: list[tuple[str, Any]],
    connect_timeout: float = 30,
    status_timeout: float = 30,
    create_client: Any | None = None,
) -> dict[str, Any]:
    """Fetch status from a device that only pushes on a real state change.

    Some firmwares never answer a status read; they only push the status
    resource to observers when the device state changes. This opens an
    observation and sends ``nudge`` control writes on the *same* CoAP client to
    force the first push, then returns that status.

    A single client is used for both the observation and the nudge writes:
    these firmwares appear to serve only one CoAP client, so a second
    connection would evict the observer and the push would never arrive. The
    observe GET and the encrypted control POST are independent requests on the
    one shared context, so they coexist safely.
    """
    _ = status_timeout  # bounded per-attempt by _NUDGE_WAIT_TIMEOUT below
    # Nudging is a CoAP-only quirk, so the client is always a CoAPClient here.
    client = cast(
        CoAPClient,
        await async_create_client(host, timeout=connect_timeout, create_client=create_client),
    )
    _LOGGER.debug("Nudge: client connected to %s, opening observation", host)
    result: dict[str, Any] = {}
    received = asyncio.Event()

    async def _watch() -> None:
        try:
            async for status in client.observe_status():
                result["status"] = status
                received.set()
                return
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("Nudge: observe stream for %s ended before a push: %s", host, ex)

    watch_task = asyncio.create_task(_watch())
    try:
        # Let the observation register on the device before changing state.
        await asyncio.sleep(_NUDGE_REGISTER_DELAY)
        for attempt in range(1, _NUDGE_ATTEMPTS + 1):
            for key, value in nudge:
                try:
                    await client.set_control_value(key, value)
                except Exception as ex:  # noqa: BLE001
                    _LOGGER.debug(
                        "Nudge: write %s=%s failed on attempt %d for %s: %s",
                        key,
                        value,
                        attempt,
                        host,
                        ex,
                    )
            _LOGGER.debug("Nudge: attempt %d/%d sent to %s, awaiting push", attempt, _NUDGE_ATTEMPTS, host)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(received.wait(), timeout=_NUDGE_WAIT_TIMEOUT)
            if received.is_set():
                _LOGGER.debug("Nudge: push received from %s on attempt %d", host, attempt)
                return result["status"]
            _LOGGER.debug("Nudge: no push from %s after attempt %d/%d", host, attempt, _NUDGE_ATTEMPTS)
        msg = f"no status push from {host} after {_NUDGE_ATTEMPTS} nudge attempts"
        raise TimeoutError(msg)
    finally:
        watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch_task
        with contextlib.suppress(Exception):
            await client.shutdown()
