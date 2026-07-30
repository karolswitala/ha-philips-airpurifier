"""Constants for Philips AirPurifier tests."""

DOMAIN = "philips_airpurifier"

TEST_HOST = "192.168.1.100"
TEST_MODEL = "AC3858/51"
TEST_NAME = "Living Room"
TEST_DEVICE_ID = "aabbccddeeff"
TEST_MAC = "b0f893123456"  # MAC address must be lowercase without colons for DHCP
TEST_MAC_FORMATTED = "b0:f8:93:12:34:56"  # TEST_MAC as stored by format_mac()

# Gen1 device status (AC3858/51 pattern)
MOCK_STATUS_GEN1: dict = {
    "pwr": "1",
    "mode": "AG",
    "om": "a",
    "aqil": 100,
    "uil": "1",
    "ddp": "1",
    "rddp": "1",
    "cl": False,
    "dt": 0,
    "err": 0,
    "DeviceId": TEST_DEVICE_ID,
    "name": TEST_NAME,
    "modelid": TEST_MODEL,
    "WifiVersion": "AWS_Philips_AIR@1.0.0",
    "Runtime": 7200000,
    # Sensors
    "pm25": 12,
    "iaql": 3,
    "rh": 50,
    "temp": 22,
    # Filters
    "fltsts0": 200,
    "flttotal0": 2400,
    "fltsts1": 1000,
    "flttotal1": 4800,
    "fltsts2": 500,
    "flttotal2": 2400,
    "fltt1": "A3",
    "fltt2": "C7",
}

# ---------------------------------------------------------------------------
# Legacy HTTP API (AC2889/10 on firmware 14)
#
# Key names and value shapes are exactly what a real device returns; the
# identifiers are synthetic so no real MAC, device id or SSID is committed.
# ---------------------------------------------------------------------------

TEST_HTTP_HOST = "192.168.1.91"
TEST_HTTP_MODEL = "AC2889/10"
TEST_HTTP_NAME = "Bedroom"
TEST_HTTP_DEVICE_ID = "1234567890abcdef"
TEST_HTTP_MAC = "aa:bb:cc:dd:ee:ff"
TEST_HTTP_MAC_FORMATTED = "aa:bb:cc:dd:ee:ff"

# GET /di/v1/products/1/air
MOCK_HTTP_AIR: dict = {
    "om": "0",
    "pwr": "0",
    "cl": False,
    "aqil": 100,
    "uil": "1",
    "dt": 0,
    "dtrs": 0,
    "mode": "M",
    "pm25": 1,
    "iaql": 1,
    "aqit": 0,
    "ddp": "1",
    "err": 193,
}

# GET /di/v1/products/1/fltsts
MOCK_HTTP_FILTERS: dict = {
    "fltt1": "A3",
    "fltt2": "C7",
    "fltsts0": 0,
    "fltsts1": 1620,
    "fltsts2": 1620,
}

# GET /di/v1/products/0/firmware
MOCK_HTTP_FIRMWARE: dict = {
    "name": "AC2889_10",
    "version": "14",
    "upgrade": "",
    "state": "idle",
    "progress": 0,
    "statusmsg": "",
    "mandatory": False,
}

# GET /di/v1/products/0/wifi
MOCK_HTTP_WIFI: dict = {
    "ssid": "TestNetwork",
    "password": "",
    "protection": "wpa-2",
    "ipaddress": TEST_HTTP_HOST,
    "netmask": "255.255.255.0",
    "gateway": "192.168.1.1",
    "dhcp": True,
    "macaddress": TEST_HTTP_MAC,
    "cppid": TEST_HTTP_DEVICE_ID,
}

# GET /upnp/description.xml (unencrypted)
MOCK_HTTP_UPNP = (
    '<?xml version="1.0"?>'
    '<root xmlns="urn:schemas-upnp-org:device-1-0">'
    "<specVersion><major>1</major><minor>1</minor></specVersion>"
    "<device>"
    "<deviceType>urn:philips-com:device:DiProduct:1</deviceType>"
    f"<friendlyName>{TEST_HTTP_NAME}</friendlyName>"
    "<manufacturer>Royal Philips Electronics</manufacturer>"
    "<modelName>AirPurifier</modelName>"
    "<modelNumber>AC2889</modelNumber>"
    "<UDN>uuid:12345678-1234-1234-1234-aabbccddeeff</UDN>"
    f"<cppId>{TEST_HTTP_DEVICE_ID}</cppId>"
    "</device>"
    "</root>"
)

# What HTTPClient.get_status() produces: /air merged with /fltsts, plus the
# identity fields the HTTP status does not carry inline.
MOCK_STATUS_HTTP: dict = {
    **MOCK_HTTP_AIR,
    **MOCK_HTTP_FILTERS,
    "DeviceId": TEST_HTTP_DEVICE_ID,
    "modelid": TEST_HTTP_MODEL,
    "name": TEST_HTTP_NAME,
    "swversion": "14",
    "type": "AC2889",
}
