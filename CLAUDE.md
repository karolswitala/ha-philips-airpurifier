# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Home Assistant custom integration for Philips Air Purifiers. Domain: `philips_airpurifier_coap`. Supports 62+ device models across 3 API generations (Gen1, Gen2, Gen3), over two transports:

- **CoAP** (default) — via the `philips-airctrl` library. Local push.
- **Legacy HTTP** (`/di/v1`) — for older firmware with no CoAP stack, e.g. AC2889/10 on firmware 14. Implemented in `http_client.py` because `philips-airctrl` has no HTTP transport. Local polling.

The config flow detects the transport and stores it on the config entry; entries without one are CoAP.

## Commands

```bash
script/setup           # Install deps with uv, set up pre-commit hooks
script/lint            # Format and lint with ruff (auto-fix)
script/test            # Run pytest test suite: uv run pytest tests/
script/develop         # Run Home Assistant with integration loaded (debug mode)

# Individual commands
uv run ruff format .                          # Format only
uv run ruff check . --fix                     # Lint only
uv run pytest tests/ -k "test_name"           # Single test
uv run pytest tests/ --cov --cov-report=term  # With coverage
uv run mypy custom_components/                # Type checking (strict mode)
```

## Architecture

### Communication Pattern

CoAP (no cloud, no polling):

- **Local push** — `CoAPClient` from `philips-airctrl` handles encryption, sync, and observe
- Coordinator uses `observe_status()` async iterator for real-time updates
- Watchdog monitors connection health with automatic reconnection

Legacy HTTP (no cloud, polls every `HTTP_POLL_INTERVAL`):

- Diffie-Hellman handshake yields an AES-128-CBC session key; every body is encrypted
- Device state is spread across `/air`, `/fltsts`, `/firmware`, `/wifi` and `/upnp/description.xml`;
  `HTTPClient.get_status()` merges them into the same shape the CoAP status has
- No observe, no watchdog — `DataUpdateCoordinator` handles retries

### Key Files

- `__init__.py` — Integration setup, icon system, platform forwarding
- `coordinator.py` — `PhilipsAirPurifierCoordinator` (DataUpdateCoordinator): CoAP push observation or HTTP polling
- `client.py` — Transport-dispatching helpers; CoAP-only nudge and device-info helpers
- `http_client.py` — Legacy `/di/v1` HTTP transport (encryption, identity synthesis)
- `config_flow.py` — DHCP auto-discovery + manual IP config, model and transport detection
- `device_models.py` — 62+ per-model `DeviceModelConfig` entries (presets, speeds, available entities)
- `const.py` — API field mappings for 3 generations, entity descriptions (sensor/switch/light/select/number types)
- `model.py` — Type definitions, `DeviceInformation`, `ApiGeneration` enum, `DeviceModelConfig`
- `entity.py` — Base entity class (WIP)

### Device Model System

`device_models.py` maps each model to a `DeviceModelConfig` dataclass — capabilities, preset modes, speeds and available entities are declared as data, not through a class hierarchy. Lookup falls back from the exact model id, to the firmware-qualified name, to the six-character family.

Three API generations with different key formats:

- Gen1: simple keys (`pwr`, `mode`, `om`)
- Gen2: `D01-XX`, `D03-XX` format
- Gen3: `D01SXX`, `D03XXX` format

### Config Entry Pattern

Uses `entry.runtime_data` typed as `PhilipsAirPurifierConfigEntry` to store the coordinator. Entity platforms access coordinator directly from the config entry.

## Quality Scale Target

Targeting **platinum** quality scale per Home Assistant integration standards. Key requirements:

- 100% test coverage (configured in pyproject.toml)
- Strict mypy typing
- Full compliance with HA integration quality scale rules

## Conventions

- Python 3.12+, line length 100, ruff for formatting/linting
- Uses `uv` for dependency management
- Async throughout — all device communication is async
- Entity platforms follow HA patterns: `async_setup_entry()` → create entities → `async_add_entities()`
- Translations in `translations/` directory (en, de, bg, etc.)
