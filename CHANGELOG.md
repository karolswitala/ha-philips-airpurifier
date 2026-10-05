# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Calendar Versioning](https://calver.org/) (`YYYY.MM.PATCH`).

## [2026.10.0] - 2026-10-05

### Changed

- Merged upstream releases `2026.8.0` through `2026.9.2` (below), including the
  per-device update-watchdog and missed-package options, AC2220 support and the
  HU1509/HU1510/HU4209 status-nudge fixes. These options apply to CoAP devices
  only; HTTP devices poll and have no watchdog.

### Fixed

- Connecting to a device over the legacy HTTP API now retries the Diffie-Hellman
  handshake once before reporting failure. The device ignores the first
  handshake offered to it after an idle spell, so adding an entry logged a
  connection warning and relied on Home Assistant's retry five seconds later.

## [2026.9.2] - 2026-09-30

### Fixed

- Reduced Home Assistant event-loop blocking warnings during Philips CoAP
  client creation by preparing aiocoap transport defaults in a worker thread
  before opening the CoAP client.
- Replaced deprecated `CONCENTRATION_MICROGRAMS_PER_CUBIC_METER` usage with
  `UnitOfDensity.MICROGRAMS_PER_CUBIC_METER` to stay compatible with the
  Home Assistant 2027.8 deprecation timeline.

## [2026.9.1] - 2026-09-28

### Fixed

- The **HU1509/HU1510** and **HU4209/00** now use a status nudge (toggling the
  display backlight) to fetch status, like the CX7550. Newer firmware on some
  of these humidifiers never answers a plain status read and only pushes
  updates on a real state change, which previously caused
  detection to time out, setup to fail with `ConfigEntryNotReady`, or the
  device to go permanently unavailable after the CoAP observe stream dropped
  (reconnect kept retrying a read the firmware would never answer). The
  nudge path also strips the `#N` suffix from the backlight key so it matches
  the observed status payload and restores the user-selected backlight state
  instead of forcing a stale value.
- Nudge-based devices (CX7550, HU1509/HU1510, HU4209/00) no longer go
  permanently silent when the CoAP observe stream hangs without erroring. The
  update watchdog was unconditionally disabled for these models on the
  assumption that a real disconnect always raises on the stream; in practice
  the stream can go quiet forever without raising (socket alive, no data, no
  exception), which nothing then detects. The watchdog now runs for these
  devices too, with a much longer timeout (30 minutes) so a device that is
  legitimately idle is not needlessly reconnected. The per-device "update
  watchdog" option can still disable it entirely for a device known to sit
  idle for very long stretches.
- The watchdog missed-package threshold is now configurable per device with a
  clear precedence order: per-device override, per-model default, then the
  global fallback. This lets models like the **AC3039** stay online longer in
  standby without forcing a broader change for every device, while still
  keeping the default watchdog tolerance at 3 missed packages globally
  ([#92](https://github.com/ruaan-deysel/ha-philips-airpurifier/issues/92)).

## [2026.9.0] - 2026-09-04

### Fixed

- Fix JSON Syntax on icons

## [2026.8.0] - 2026-08-30

### Fixed

- Reconnect recovery can no longer wedge indefinitely when a CoAP status read
  stalls during reconnect. Coordinator CoAP calls are now time-bounded and
  stale reconnect tasks are treated as wedged, so retries and availability
  recovery continue as expected
  ([#101](https://github.com/ruaan-deysel/ha-philips-airpurifier/pull/101)).
- Rotation (oscillation) control is available again on the **AMF870**
  (Series 8000i 2-in-1). The model configuration listed only the target
  temperature under its numbers, which replaced rather than extended the AMF
  family defaults and silently dropped the rotation angle entity. The
  **Oscillation** number (0° = off, 30°–350° in 5° steps) is restored next to
  the target temperature, and the fan entity now supports the standard
  `fan.oscillate` service and the oscillation toggle in the UI. The AMF765
  gains the same oscillation toggle — it kept the angle entity but never
  exposed the on/off control.
- On the **AMF765** and **AMF870**, where the angle and the on/off state share
  one device key, switching oscillation back on now restores the rotation angle
  the device last reported instead of overwriting it with a fixed value. When no
  angle has been reported yet — for example right after a Home Assistant restart
  — it starts at the configured default of 90° instead. Models whose oscillation
  key is a fixed on/off code are unaffected and keep writing their documented
  on-value.
- The `oscillation` and `target_temperature` number entities are translated
  again in German, Dutch and Bulgarian. Their translation keys were misspelled
  (`oscillaton`, `target_temp`) and never matched `strings.json`, so those
  entities fell back to their English names.

### Added

- Added a per-device option to enable or disable the update watchdog. This is
  useful for models that rely on status nudges and can remain idle for long
  periods without emitting push updates
  ([#100](https://github.com/ruaan-deysel/ha-philips-airpurifier/pull/100)).
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

[Unreleased]: https://github.com/karolswitala/ha-philips-airpurifier/compare/v2026.7.0...HEAD
[2026.7.0]: https://github.com/karolswitala/ha-philips-airpurifier/releases/tag/v2026.7.0
[2026.6.3]: https://github.com/ruaan-deysel/ha-philips-airpurifier/releases/tag/v2026.6.3
[2026.6.2]: https://github.com/ruaan-deysel/ha-philips-airpurifier/compare/v2026.6.1...v2026.6.2
[2026.6.1]: https://github.com/ruaan-deysel/ha-philips-airpurifier/compare/v2026.6.0...v2026.6.1
[2026.6.0]: https://github.com/ruaan-deysel/ha-philips-airpurifier/releases/tag/v2026.6.0
