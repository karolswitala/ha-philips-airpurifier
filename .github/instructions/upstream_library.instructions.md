---
applyTo: "custom_components/philips_airpurifier/client.py, custom_components/philips_airpurifier/coordinator.py, custom_components/philips_airpurifier/http_client.py"
description: Enforce philips-airctrl usage for CoAP device communication
---

# Upstream Library Instructions

**Applies to:** `client.py`, `coordinator.py` and `http_client.py` in this integration

## Core Rule

All **CoAP** communication must go through the `philips-airctrl` client abstraction used by this repository.

- Allowed: `get_status()`, `observe_status()`, `set_control_values()`, and related upstream client methods.
- Not allowed: direct sockets, direct CoAP packets, manual CoAP encryption/decryption, or alternate CoAP clients.

## The HTTP Exception

Older firmware — for example the AC2889/10 on firmware 14 — implements **no CoAP stack at all** and is
reachable only over the legacy `/di/v1` HTTP API. `philips-airctrl` provides no HTTP transport, so there is
no upstream method to call and the rule above cannot apply.

- `http_client.py` is the **sanctioned and only** home for that protocol. It mirrors the slice of the
  `CoAPClient` API this integration uses, so the two are interchangeable behind `client.py`.
- Do not add HTTP protocol code anywhere else, and do not route CoAP through it.
- If `philips-airctrl` ever gains an HTTP client, replacing `http_client.py` with it is preferred.

`client.py` dispatches on `const.Protocol`; the config flow detects the transport once and stores it on the
config entry.

## Change Guidelines

- Treat `philips-airctrl` as the integration boundary for CoAP transport/protocol logic.
- Keep integration code focused on Home Assistant orchestration, mapping, and error handling.
- If a required CoAP capability is missing in the upstream client, do not work around it with local protocol code.

## Missing Capability Process

When an upstream CoAP method is missing:

1. Implement graceful handling in integration code if possible (feature unavailable, fallback, or clear error).
2. Open or reference an upstream issue in `ruaan-deysel/philips-airctrl` for the missing API.
3. Avoid introducing custom CoAP transport logic in this repository.

## Known API (v1.2.0+)

The following capabilities were added in **`philips-airctrl==1.2.0`** and are now the required way to handle push-only firmware:

| Method                                | Notes                                                                                                                                                                                                      |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CoAPClient.get_device_info()`        | Reads the plaintext `/sys/dev/info` resource without the encrypted sync handshake. Use for device identification before committing to a full encrypted session.                                            |
| `CoAPClient.create(host, sync=False)` | Creates the aiocoap transport context without running `_sync()`. Required before calling `get_device_info()` on devices that never answer the encrypted status path (e.g. CX7550 `AWS_Philips_AIR_Combo`). |

Do **not** import or use raw `aiocoap` primitives (`Context`, `Message`, `Unreliable`) in `client.py` or `coordinator.py` — use the methods above instead.
