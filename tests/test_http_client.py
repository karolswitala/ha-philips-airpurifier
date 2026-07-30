"""Tests for the legacy HTTP client.

These drive :class:`HTTPClient` against a simulated device that performs the
real Diffie-Hellman exchange and AES-CBC encryption, so the wire format is
exercised end to end rather than mocked away. The simulated device mirrors
``py-air-control``'s ``testing/http_test_controller.py``.
"""

from __future__ import annotations

import base64
import json
import secrets
from typing import TYPE_CHECKING, Any

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad, unpad
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.philips_airpurifier.const import PhilipsApi
from custom_components.philips_airpurifier.http_client import (
    ATTR_MAC,
    FILTERS_PATH,
    FIRMWARE_PATH,
    SECURITY_PATH,
    STATUS_PATH,
    UPNP_PATH,
    WIFI_PATH,
    HTTPClient,
    decrypt,
    encrypt,
    model_from_firmware_name,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    MOCK_HTTP_AIR,
    MOCK_HTTP_FILTERS,
    MOCK_HTTP_FIRMWARE,
    MOCK_HTTP_UPNP,
    MOCK_HTTP_WIFI,
    MOCK_STATUS_HTTP,
    TEST_HTTP_DEVICE_ID,
    TEST_HTTP_HOST,
    TEST_HTTP_MAC,
    TEST_HTTP_MODEL,
    TEST_HTTP_NAME,
)

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

    from homeassistant.core import HomeAssistant

# Same fixed parameters the firmware uses.
_G = int(
    "A4D1CBD5C3FD34126765A442EFB99905F8104DD258AC507FD6406CFF14266D31"
    "266FEA1E5C41564B777E690F5504F213160217B4B01B886A5E91547F9E2749F4"
    "D7FBD7D3B9A92EE1909D0D2263F80A76A6A24C087A091F531DBF0A0169B6A28A"
    "D662A4D18E73AFA32D779D5918D08BC8858F4DCEF97C2A24855E6EEB22B3B2E5",
    16,
)
_P = int(
    "B10B8F96A080E01DDE92DE5EAE5D54EC52C99FBCFB06A3C69A6A9DCA52D23B61"
    "6073E28675A23D189838EF1E2EE652C013ECB4AEA906112324975C3CD49B83BF"
    "ACCBDD7D90C4BD7098488E9C219A73724EFFD6FAE5644738FAA31A4FF55BCCC0"
    "A151AF5F0DC8B4BD45BF37DF365C1A65E68CFDA76D4DA708DF1FB2BC2E4A4371",
    16,
)


class FakeDevice:
    """A simulated purifier speaking the legacy HTTP protocol."""

    def __init__(self, session_key: bytes = b"0123456789abcdef") -> None:
        """Initialize with the session key the device will hand out."""
        self.session_key = session_key
        self.handshakes = 0
        self.writes: list[dict[str, Any]] = []
        # When set, the next N encrypted requests fail, simulating the device
        # having silently invalidated the session key.
        self.fail_next = 0
        self.upnp = MOCK_HTTP_UPNP
        self.resources: dict[str, dict[str, Any]] = {
            STATUS_PATH: dict(MOCK_HTTP_AIR),
            FILTERS_PATH: dict(MOCK_HTTP_FILTERS),
            FIRMWARE_PATH: dict(MOCK_HTTP_FIRMWARE),
            WIFI_PATH: dict(MOCK_HTTP_WIFI),
        }

    def _encrypt_body(self, values: dict[str, Any]) -> str:
        """Encrypt a response body the way the firmware does."""
        return encrypt(values, self.session_key).decode("ascii")

    def register(self, aioclient_mock: AiohttpClientMocker, host: str = TEST_HTTP_HOST) -> None:
        """Register every endpoint on the aiohttp mocker."""
        base = f"http://{host}"
        aioclient_mock.put(f"{base}{SECURITY_PATH}", side_effect=self._handle_security)
        aioclient_mock.get(f"{base}{UPNP_PATH}", text=self.upnp)
        for path in self.resources:
            aioclient_mock.get(f"{base}{path}", side_effect=self._handle_get)
        aioclient_mock.put(f"{base}{STATUS_PATH}", side_effect=self._handle_write)

    async def _handle_security(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Complete the Diffie-Hellman exchange and hand back the session key."""
        self.handshakes += 1
        client_public = int(json.loads(data)["diffie"], 16)
        private = secrets.randbits(256)
        public = pow(_G, private, _P)
        shared = pow(client_public, private, _P)
        shared_bytes = shared.to_bytes(128, byteorder="big")[:16]
        encrypted_key = AES.new(shared_bytes, AES.MODE_CBC, bytes(16)).encrypt(self.session_key)
        return AiohttpClientMockResponse(
            method,
            url,
            text=json.dumps({"key": encrypted_key.hex(), "hellman": format(public, "x")}),
        )

    def _maybe_fail(self) -> bool:
        """Consume one scheduled failure, if any are pending."""
        if self.fail_next > 0:
            self.fail_next -= 1
            return True
        return False

    async def _handle_get(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Serve an encrypted resource."""
        _ = data
        if self._maybe_fail():
            return AiohttpClientMockResponse(method, url, status=500, text="")
        return AiohttpClientMockResponse(method, url, text=self._encrypt_body(self.resources[url.path]))

    async def _handle_write(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Apply an encrypted write and answer with the resulting state."""
        if self._maybe_fail():
            return AiohttpClientMockResponse(method, url, status=500, text="")
        values = json.loads(decrypt(data.decode("ascii"), self.session_key))
        self.writes.append(values)
        self.resources[STATUS_PATH].update(values)
        return AiohttpClientMockResponse(method, url, text=self._encrypt_body(self.resources[STATUS_PATH]))


async def _connect(hass: HomeAssistant, device: FakeDevice, aioclient_mock: AiohttpClientMocker) -> HTTPClient:
    """Register the fake device and connect a client to it."""
    device.register(aioclient_mock)
    return await HTTPClient.create(TEST_HTTP_HOST, async_get_clientsession(hass))


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip() -> None:
    """A body survives an encrypt/decrypt round trip."""
    key = b"0123456789abcdef"
    payload = {"pwr": "1", "om": "2"}

    assert json.loads(decrypt(encrypt(payload, key).decode("ascii"), key)) == payload


def test_encrypt_prefixes_two_filler_bytes() -> None:
    """The firmware expects two filler bytes ahead of the JSON body."""
    key = b"0123456789abcdef"

    raw = AES.new(key, AES.MODE_CBC, bytes(16)).decrypt(base64.b64decode(encrypt({"pwr": "1"}, key)))

    assert unpad(raw, 16, style="pkcs7").decode("ascii") == 'AA{"pwr": "1"}'


def test_decrypt_matches_device_encryption() -> None:
    """decrypt() reads a body encrypted exactly as the device encrypts it."""
    key = b"0123456789abcdef"
    body = pad(b'XX{"pwr": "1"}', 16, style="pkcs7")
    ciphertext = base64.b64encode(AES.new(key, AES.MODE_CBC, bytes(16)).encrypt(body)).decode("ascii")

    assert decrypt(ciphertext, key) == '{"pwr": "1"}'


@pytest.mark.parametrize(
    ("firmware_name", "expected"),
    [
        ("AC2889_10", "AC2889/10"),
        ("AC2729_10", "AC2729/10"),
        # No underscore: nothing to rewrite.
        ("AC2889", "AC2889"),
        ("", ""),
    ],
)
def test_model_from_firmware_name(firmware_name: str, expected: str) -> None:
    """Firmware product names convert to the CoAP-style model id."""
    assert model_from_firmware_name(firmware_name) == expected


# ---------------------------------------------------------------------------
# Client behaviour
# ---------------------------------------------------------------------------


async def test_create_performs_handshake_and_reads_identity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Connecting exchanges a key once and identifies the device."""
    device = FakeDevice()

    client = await _connect(hass, device, aioclient_mock)

    assert device.handshakes == 1
    info = await client.get_device_info()
    assert info[PhilipsApi.DEVICE_ID] == TEST_HTTP_DEVICE_ID
    assert info[PhilipsApi.MODEL_ID] == TEST_HTTP_MODEL
    assert info[PhilipsApi.NAME] == TEST_HTTP_NAME
    assert info[PhilipsApi.SOFTWARE_VERSION] == "14"
    assert info[PhilipsApi.TYPE] == "AC2889"
    assert info[ATTR_MAC] == TEST_HTTP_MAC


async def test_get_status_merges_resources_and_identity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Status merges /air and /fltsts and overlays the identity fields."""
    client = await _connect(hass, FakeDevice(), aioclient_mock)

    status, interval = await client.get_status()

    assert status == MOCK_STATUS_HTTP
    assert interval == 30
    # The MAC is deliberately not part of the status dict.
    assert ATTR_MAC not in status


async def test_get_status_accepts_observe_flag(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The observe flag exists for CoAP compatibility and is ignored."""
    client = await _connect(hass, FakeDevice(), aioclient_mock)

    with_observe, _ = await client.get_status(observe=True)
    without_observe, _ = await client.get_status(observe=False)

    assert with_observe == without_observe


async def test_identity_falls_back_to_upnp_model(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A device with no firmware product name is identified from UPnP."""
    device = FakeDevice()
    device.resources[FIRMWARE_PATH] = {"version": "14"}

    client = await _connect(hass, device, aioclient_mock)

    assert (await client.get_device_info())[PhilipsApi.MODEL_ID] == "AC2889"


async def test_identity_survives_missing_upnp_fields(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A UPnP document without the expected fields yields empty values."""
    device = FakeDevice()
    device.upnp = '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device/></root>'

    client = await _connect(hass, device, aioclient_mock)

    info = await client.get_device_info()
    assert info[PhilipsApi.NAME] == ""
    # The firmware name still identifies the model.
    assert info[PhilipsApi.MODEL_ID] == TEST_HTTP_MODEL


async def test_set_control_values_writes_and_updates(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Writing control values reaches the device and is reflected in status."""
    device = FakeDevice()
    client = await _connect(hass, device, aioclient_mock)

    assert await client.set_control_values(data={"pwr": "1", "om": "2"}) is True

    assert device.writes == [{"pwr": "1", "om": "2"}]
    status, _ = await client.get_status()
    assert status["pwr"] == "1"
    assert status["om"] == "2"


async def test_set_control_value_writes_single_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The single-key helper delegates to the multi-key write."""
    device = FakeDevice()
    client = await _connect(hass, device, aioclient_mock)

    assert await client.set_control_value("pwr", "1") is True

    assert device.writes == [{"pwr": "1"}]


async def test_read_renews_session_key_after_failure(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A failed read triggers a fresh handshake and one retry."""
    device = FakeDevice()
    client = await _connect(hass, device, aioclient_mock)
    handshakes_before = device.handshakes

    device.fail_next = 1
    status, _ = await client.get_status()

    assert device.handshakes == handshakes_before + 1
    assert status == MOCK_STATUS_HTTP


async def test_write_renews_session_key_and_reencrypts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A failed write re-encrypts the body with the new key before retrying."""
    device = FakeDevice()
    client = await _connect(hass, device, aioclient_mock)
    handshakes_before = device.handshakes
    # The device also rotates its key, so a body encrypted with the old one
    # would no longer decrypt -- proving the retry re-encrypts.
    device.fail_next = 1
    device.session_key = b"fedcba9876543210"

    assert await client.set_control_values(data={"pwr": "1"}) is True

    assert device.handshakes == handshakes_before + 1
    assert device.writes == [{"pwr": "1"}]


async def test_read_propagates_error_when_retry_also_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Two consecutive failures surface to the caller."""
    device = FakeDevice()
    client = await _connect(hass, device, aioclient_mock)

    device.fail_next = 2
    with pytest.raises(Exception, match="500"):
        await client.get_status()


async def test_write_propagates_error_when_retry_also_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Two consecutive write failures surface to the caller."""
    device = FakeDevice()
    client = await _connect(hass, device, aioclient_mock)

    device.fail_next = 2
    with pytest.raises(Exception, match="500"):
        await client.set_control_values(data={"pwr": "1"})


async def test_shutdown_is_a_safe_no_op(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Shutdown holds no connection open and must not close HA's session."""
    device = FakeDevice()
    client = await _connect(hass, device, aioclient_mock)

    await client.shutdown()
    await client.shutdown()

    # HA's shared session is untouched, so the client still works.
    status, _ = await client.get_status()
    assert status == MOCK_STATUS_HTTP
    assert device.handshakes == 1
