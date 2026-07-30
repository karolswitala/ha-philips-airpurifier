"""Client for the legacy Philips HTTP (``/di/v1``) air purifier API.

Older Philips firmware -- for example the AC2889/10 on firmware 14 -- does not
implement CoAP at all and is reachable only over this HTTP API. The upstream
``philips-airctrl`` library ships a CoAP transport only, so the HTTP protocol is
implemented here; see ``AGENTS.md`` for the scope of the upstream-library rule.

The wire format is ported from ``py-air-control``'s ``pyairctrl/http_client.py``:
a Diffie-Hellman exchange against ``/di/v1/products/0/security`` yields an
AES-128-CBC session key (all-zero IV) that encrypts every subsequent request and
response body.

Unlike CoAP, this API has no push mechanism and spreads the device state across
several resources, so :meth:`HTTPClient.get_status` merges them and overlays the
identity fields (``DeviceId``, ``modelid``, ``name``, ``swversion``) that the
CoAP status carries inline but the HTTP status does not.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any
import xml.etree.ElementTree as ET

from aiohttp import ClientTimeout
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad, unpad

from .const import HTTP_POLL_INTERVAL, PhilipsApi

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

# Diffie-Hellman parameters used by the Philips firmware. These are fixed by the
# device, not a choice we get to make.
_DH_GENERATOR = int(
    "A4D1CBD5C3FD34126765A442EFB99905F8104DD258AC507FD6406CFF14266D31"
    "266FEA1E5C41564B777E690F5504F213160217B4B01B886A5E91547F9E2749F4"
    "D7FBD7D3B9A92EE1909D0D2263F80A76A6A24C087A091F531DBF0A0169B6A28A"
    "D662A4D18E73AFA32D779D5918D08BC8858F4DCEF97C2A24855E6EEB22B3B2E5",
    16,
)
_DH_MODULUS = int(
    "B10B8F96A080E01DDE92DE5EAE5D54EC52C99FBCFB06A3C69A6A9DCA52D23B61"
    "6073E28675A23D189838EF1E2EE652C013ECB4AEA906112324975C3CD49B83BF"
    "ACCBDD7D90C4BD7098488E9C219A73724EFFD6FAE5644738FAA31A4FF55BCCC0"
    "A151AF5F0DC8B4BD45BF37DF365C1A65E68CFDA76D4DA708DF1FB2BC2E4A4371",
    16,
)

SECURITY_PATH = "/di/v1/products/0/security"
STATUS_PATH = "/di/v1/products/1/air"
FILTERS_PATH = "/di/v1/products/1/fltsts"
FIRMWARE_PATH = "/di/v1/products/0/firmware"
WIFI_PATH = "/di/v1/products/0/wifi"
UPNP_PATH = "/upnp/description.xml"

UPNP_NAMESPACE = {"upnp": "urn:schemas-upnp-org:device-1-0"}

# Key returned by get_device_info() carrying the device's MAC address. Not a
# PhilipsApi status field -- the CoAP status has no equivalent -- so it is kept
# out of the merged status dict and read only by the config flow.
ATTR_MAC = "macaddress"

_REQUEST_TIMEOUT = ClientTimeout(total=10)


def _aes_decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt AES-128-CBC data with the all-zero IV the firmware uses."""
    return AES.new(key, AES.MODE_CBC, bytes(16)).decrypt(data)


def encrypt(values: dict[str, Any], key: bytes) -> bytes:
    """Encrypt a request body, returning base64 ciphertext.

    The firmware expects two filler bytes in front of the JSON payload; they are
    discarded on the device side and their value is irrelevant.
    """
    data = pad(("AA" + json.dumps(values)).encode("ascii"), 16, style="pkcs7")
    return base64.b64encode(AES.new(key, AES.MODE_CBC, bytes(16)).encrypt(data))


def decrypt(data: str, key: bytes) -> str:
    """Decrypt a base64 response body, stripping the two leading filler bytes."""
    plain = _aes_decrypt(base64.b64decode(data), key)
    return unpad(plain, 16, style="pkcs7")[2:].decode("ascii")


def model_from_firmware_name(name: str) -> str:
    """Convert a firmware product name to the CoAP-style model id.

    The firmware resource reports ``AC2889_10`` where the CoAP status reports
    ``AC2889/10``; the rest of the integration matches on the latter.
    """
    if "_" not in name:
        return name
    family, _, variant = name.rpartition("_")
    return f"{family}/{variant}"


class HTTPClient:
    """Talk to a Philips purifier over the legacy encrypted HTTP API.

    Mirrors the slice of ``philips_airctrl.CoAPClient`` that this integration
    uses, so the two are interchangeable behind ``client.py``. There is no
    ``observe_status()``: the HTTP API cannot push, and the coordinator polls
    instead.
    """

    def __init__(self, host: str, session: ClientSession) -> None:
        """Initialize the client. Use :meth:`create` rather than calling this."""
        self.host = host
        self._session = session
        self._session_key: bytes | None = None
        self._identity: dict[str, Any] = {}

    @classmethod
    async def create(cls, host: str, session: ClientSession) -> HTTPClient:
        """Connect to a host: exchange a session key and read device identity."""
        client = cls(host, session)
        await client._async_exchange_key()
        client._identity = await client._async_fetch_identity()
        return client

    async def shutdown(self) -> None:
        """Release resources; a no-op for this transport.

        There is no persistent connection to tear down, and the aiohttp session
        belongs to Home Assistant. The method exists so this client matches the
        CoAP client's lifecycle and can be closed the same way.
        """

    def _url(self, path: str) -> str:
        return f"http://{self.host}{path}"

    async def _async_exchange_key(self) -> bytes:
        """Run the Diffie-Hellman handshake, storing and returning the session key."""
        _LOGGER.debug("Exchanging session key with %s", self.host)
        private = secrets.randbits(256)
        public = pow(_DH_GENERATOR, private, _DH_MODULUS)
        async with self._session.put(
            self._url(SECURITY_PATH),
            data=json.dumps({"diffie": format(public, "x")}).encode("ascii"),
            timeout=_REQUEST_TIMEOUT,
        ) as response:
            response.raise_for_status()
            exchange = json.loads(await response.text())

        shared = pow(int(exchange["hellman"], 16), private, _DH_MODULUS)
        shared_bytes = shared.to_bytes(128, byteorder="big")[:16]
        self._session_key = _aes_decrypt(bytes.fromhex(exchange["key"]), shared_bytes)[:16]
        return self._session_key

    def _key(self) -> bytes:
        """Return the current session key."""
        if self._session_key is None:  # pragma: no cover - create() always sets it
            msg = "No session key; call create() first"
            raise RuntimeError(msg)
        return self._session_key

    async def _async_request_once(
        self, method: str, path: str, key: bytes, body: bytes | None = None
    ) -> dict[str, Any]:
        """Perform one encrypted request with the given session key."""
        async with self._session.request(
            method,
            self._url(path),
            data=body,
            timeout=_REQUEST_TIMEOUT,
        ) as response:
            response.raise_for_status()
            payload = await response.text()
        return dict(json.loads(decrypt(payload, key)))

    async def _async_get(self, path: str) -> dict[str, Any]:
        """GET and decrypt a resource, renewing the session key once on failure.

        The device silently invalidates the session key -- on reboot, or when
        another client pairs with it -- and the only symptom is a failed request
        or an undecryptable response. Re-running the handshake is the sole
        recovery path, so it is retried once before giving up.
        """
        try:
            return await self._async_request_once("GET", path, self._key())
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("GET %s failed (%s); renewing session key", path, ex)
        return await self._async_request_once("GET", path, await self._async_exchange_key())

    async def _async_write(self, path: str, values: dict[str, Any]) -> dict[str, Any]:
        """PUT an encrypted body, renewing the session key once on failure.

        Unlike a read, the retry must re-encrypt the body with the new key.
        """
        try:
            return await self._async_request_once("PUT", path, self._key(), encrypt(values, self._key()))
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("PUT %s failed (%s); renewing session key", path, ex)
        key = await self._async_exchange_key()
        return await self._async_request_once("PUT", path, key, encrypt(values, key))

    async def _async_fetch_upnp(self) -> dict[str, str]:
        """Read the unencrypted UPnP description for the model and friendly name."""
        async with self._session.get(self._url(UPNP_PATH), timeout=_REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            body = await response.text()

        # The document is a fixed, tiny descriptor served by a device on the
        # local network, and only two text fields are read from it.
        root = ET.fromstring(body)  # noqa: S314
        found: dict[str, str] = {}
        for device in root.findall("upnp:device", UPNP_NAMESPACE):
            for field in ("modelNumber", "friendlyName"):
                element = device.find(f"upnp:{field}", UPNP_NAMESPACE)
                if element is not None and element.text:
                    found[field] = element.text
        return found

    async def _async_fetch_identity(self) -> dict[str, Any]:
        """Build the identity fields the HTTP status does not carry.

        The CoAP status includes ``DeviceId``, ``modelid``, ``name`` and
        ``swversion`` inline. Over HTTP they live on three other resources, so
        they are read once at connect time and overlaid onto every status.

        ``WifiVersion`` is deliberately absent: this firmware has no equivalent,
        and the config flow tolerates it being missing rather than being fed a
        made-up value.
        """
        firmware = await self._async_get(FIRMWARE_PATH)
        wifi = await self._async_get(WIFI_PATH)
        upnp = await self._async_fetch_upnp()

        model = model_from_firmware_name(str(firmware.get("name", ""))) or upnp.get("modelNumber", "")
        identity: dict[str, Any] = {
            PhilipsApi.DEVICE_ID: wifi.get("cppid", ""),
            PhilipsApi.MODEL_ID: model,
            PhilipsApi.NAME: upnp.get("friendlyName", ""),
            PhilipsApi.SOFTWARE_VERSION: firmware.get("version", ""),
            PhilipsApi.TYPE: upnp.get("modelNumber", ""),
            ATTR_MAC: wifi.get("macaddress"),
        }
        _LOGGER.debug("Identified %s as %s (%s)", self.host, model, identity[PhilipsApi.NAME])
        return identity

    async def get_device_info(self) -> dict[str, Any]:
        """Return the cached identity fields, including the device MAC address."""
        return dict(self._identity)

    async def get_status(self, observe: bool = False) -> tuple[dict[str, Any], int]:
        """Read the current device status.

        Merges the control/sensor resource with the filter resource and overlays
        the cached identity fields, producing a dict shaped like the one the CoAP
        client returns so the entity platforms need no HTTP-specific handling.

        Args:
            observe: Accepted for signature compatibility with ``CoAPClient``
                and ignored -- this API cannot push.

        Returns:
            A tuple of the merged status and the suggested poll interval in
            seconds, mirroring the CoAP client's ``(status, max_age)`` shape.
        """
        _ = observe
        status = await self._async_get(STATUS_PATH)
        status.update(await self._async_get(FILTERS_PATH))
        status.update({key: value for key, value in self._identity.items() if key != ATTR_MAC})
        return status, int(HTTP_POLL_INTERVAL.total_seconds())

    async def set_control_value(self, key: str, value: Any) -> bool:
        """Set a single control value on the device."""
        return await self.set_control_values(data={key: value})

    async def set_control_values(self, data: dict[str, Any]) -> bool:
        """Set multiple control values on the device.

        The device answers a write with its resulting state, so a decodable
        response is the acknowledgement.
        """
        _LOGGER.debug("Writing %s to %s", data, self.host)
        await self._async_write(STATUS_PATH, data)
        return True
