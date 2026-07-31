# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Calendar Versioning](https://calver.org/) (`YYYY.MM.PATCH`).

## [2026.7.0] - 2026-07-31

First release of the fork at
[karolswitala/ha-philips-airpurifier](https://github.com/karolswitala/ha-philips-airpurifier),
which adds the legacy HTTP transport. Everything below is relative to upstream `2026.6.3`.

### Added

- Support for devices that speak only the **legacy HTTP (`/di/v1`) API** and have
  no CoAP stack at all — for example the **AC2889/10 on firmware 14**. The config
  flow now probes CoAP first and falls back to HTTP, storing the detected
  transport on the config entry. HTTP devices poll every 30 seconds, since that
  API cannot push; CoAP devices are unaffected and keep their push behaviour.
  Because the HTTP status resource carries no identity fields, the model, name,
  device id, software version and MAC address are read from the `/firmware`,
  `/wifi` and `/upnp/description.xml` resources instead. `philips-airctrl` ships
  no HTTP transport, so this lives in the new `http_client.py`.
- DHCP auto-discovery for the `E8C1D7*` MAC prefix, used by AC2889 units.
- Support for the **CX7550/01** (Philips oscillating tower fan). It uses Gen3
  CoAP and is fan-only (no heater). Exposes all 12 manual fan speeds, the Auto,
  Sleep and Natural preset modes, on/off oscillation, the display backlight
  light, the beep switch, a standby temperature-display switch, a timer, and
  the temperature sensor. Initial Wi-Fi setup requires the Philips Air app;
  control is fully local thereafter. The `AWS_Philips_AIR_Combo` firmware is
  push-only (never answers a status read), so the integration nudges the
  display backlight to obtain status. While the fan is off, the firmware forces
  a dim standby display that cannot be turned off from Home Assistant.

### Fixed

- Devices reporting no `WifiVersion` (all legacy HTTP firmware) no longer raise
  `AttributeError` during the config flow before model matching runs.
- Filter life is reported for devices on the legacy HTTP API. That API carries
  no `flttotal*` capacity field, so the filter replacement warning could never
  fire and `filter_reset` silently did nothing — an AC2889/10 reporting an
  exhausted pre-filter (`fltsts0: 0`) surfaced no warning at all. Capacity now
  falls back to the filter type's nominal lifetime when the device reports
  none, and a filter that reads zero warns even when no capacity is known.
  A capacity reported by the device is still always preferred, so CoAP
  behaviour is unchanged.
- The CoAP device-info read during setup is now bounded by a timeout, so
  probing a host that has no CoAP stack can no longer hang indefinitely.

### Changed

- The integration is now named **"Philips AirPurifier (with CoAP and HTTP)"**
  to distinguish it from upstream. The domain is unchanged
  (`philips_airpurifier`), so it installs over an upstream installation —
  remove the upstream integration from HACS first, or HACS will revert it.
- Filter sensors on devices using the legacy HTTP API report **percent**
  rather than hours, since capacity now resolves to a nominal lifetime. The
  nominal values are Philips' published replacement intervals and are not
  verified against hardware.
- README documents the fork, its installation path, and the HTTP behaviour a
  user can observe: the percentage-based filter sensors, the single
  `Failed to connect to host` warning absorbed by retry during setup, and the
  fact that the `coap` and `philips_airctrl` loggers stay silent over HTTP.

## [2026.6.3] - 2026-06-27

### Added

- Support for the **HU4209/00** (Philips Evaporative Humidifier Series 4000).
  It uses Gen3 CoAP and reuses the HU1509/HU1510 preset and speed mappings,
  differing only by the absence of ambient light mode
  ([#63](https://github.com/ruaan-deysel/ha-philips-airpurifier/pull/63)).
- Support for the **AC2210** family (PureProtect Quiet 2200 series, e.g.
  `AC2210/10`) by reusing the AC2221 device configuration; previously these
  devices were detected but rejected with `model_unsupported`
  ([#59](https://github.com/ruaan-deysel/ha-philips-airpurifier/pull/59)).

## [2026.6.2] - 2026-06-14

### Improvements

- Coordinator reconnect handling now uses exponential backoff retries
  (5 seconds up to a 60-second cap) after reconnect failures, instead of
  waiting for the watchdog interval to recover. ([#51](https://github.com/ruaan-deysel/ha-philips-airpurifier/issues/51))
- Devices are no longer marked unavailable immediately when the CoAP
  observation stream ends; unavailable is now set only when reconnect attempts
  actually fail, reducing transient warning noise. ([#51](https://github.com/ruaan-deysel/ha-philips-airpurifier/issues/51))

## [2026.6.1] - 2026-06-12

### Fixed

- DHCP discovery now matches already configured devices by MAC address (or
  host) **before** opening a CoAP connection
  ([#8](https://github.com/ruaan-deysel/ha-philips-airpurifier/issues/8)).
  This fixes two long-standing problems:
  - A purifier that received a new IP address from the router stayed
    unavailable forever, because the discovery flow had to connect to the
    device to identify it — which fails while the device is mid-transition or
    while the integration holds the device's single CoAP connection. The
    stored host is now updated from the DHCP packet alone and the entry is
    reloaded automatically.
  - An already configured purifier kept reappearing as a newly discovered
    device, repeatedly probing (and potentially disrupting) the active
    connection.
- Entries created via manual setup (which have no MAC stored) are matched by
  host on the first DHCP discovery and the MAC is backfilled, so subsequent
  IP changes are handled automatically.
- Config flow no longer raises `ConfigEntryNotReady` from flow steps (an
  invalid pattern that produced "unknown error" in the UI); connection
  failures now abort discovery flows with `cannot_connect` and re-show the
  form with an error in user-initiated flows.
- A connection timeout during manual setup no longer dead-ends the flow with
  an untranslated `timeout` abort; the form is shown again with a
  "cannot connect" error.
- Form error keys (`invalid_host`, `cannot_connect`) now match the defined
  translation strings; previously the UI displayed raw identifiers like
  `connect`.
- `select` entities now report `None` (unknown) instead of the raw device
  value when the device sends an option value the integration does not know,
  matching the Home Assistant `SelectEntity` contract.
- The fan mode select is no longer a configuration entity, so it appears in
  device automation pickers again
  ([#2](https://github.com/ruaan-deysel/ha-philips-airpurifier/issues/2)).
- Declared the correct minimum Home Assistant version (2026.4.0, matching the
  documented requirement) in `hacs.json` and the README badge. Home Assistant
  releases before 2026.3 run Python 3.13, where the integration fails to load
  with a syntax error
  ([#45](https://github.com/ruaan-deysel/ha-philips-airpurifier/issues/45));
  the previous HACS minimum of 2025.1.0 allowed broken installs.

### Changed

- All translation files (`bg`, `de`, `en`, `nl`, `ro`, `sk`) now have an
  identical key structure; missing abort/error strings
  (`cannot_connect`, `different_device`, `reconfigure_successful`) were added
  with proper translations.
- Removed descriptions for services that do not exist
  (`calibrate_sensors`, `set_display_brightness`, `schedule_maintenance`,
  `set_timer`, `reset_device`) and an unused repair issue string from
  `strings.json`.

### Quality

- Test suite extended to restore 100% coverage (config flow discovery
  matching, repairs acknowledge persistence, event code parsing, `const.py`
  value converters).

## [2026.6.0] - 2026-06-08

Latest release prior to this changelog being introduced. See the
[GitHub releases](https://github.com/ruaan-deysel/ha-philips-airpurifier/releases)
for the history of earlier versions.

[2026.7.0]: https://github.com/karolswitala/ha-philips-airpurifier/releases/tag/v2026.7.0
[2026.6.3]: https://github.com/ruaan-deysel/ha-philips-airpurifier/releases/tag/v2026.6.3
[2026.6.2]: https://github.com/ruaan-deysel/ha-philips-airpurifier/compare/v2026.6.1...v2026.6.2
[2026.6.1]: https://github.com/ruaan-deysel/ha-philips-airpurifier/compare/v2026.6.0...v2026.6.1
[2026.6.0]: https://github.com/ruaan-deysel/ha-philips-airpurifier/releases/tag/v2026.6.0
