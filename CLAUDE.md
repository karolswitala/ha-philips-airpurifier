# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Home Assistant custom integration for Philips air purifiers, humidifiers and fans.
62 model entries across 3 API generations, over **two transports**:

- **CoAP** — via the `philips-airctrl` library. Local push, no update interval.
- **Legacy HTTP** (`/di/v1`) — older firmware with no CoAP stack at all (e.g. AC2889/10 on
  firmware 14). Implemented locally in `http_client.py` because `philips-airctrl` ships no HTTP
  transport. Local polling every 30s.

The config flow probes CoAP first, falls back to HTTP, and stores the result as `CONF_PROTOCOL`
on the config entry. **Entries without that key are CoAP** — that is how pre-HTTP entries keep
working, so no migration exists or is needed.

`DOMAIN = "philips_airpurifier"` (in `const.py`). Note that `AGENTS.md` and some docs still say
`philips_airpurifier_coap`; they are wrong — trust `const.py`.

## Commands

```bash
./script/check       # type + lint + spell — run before committing
./script/test        # pytest; --cov for coverage (must stay at 100%)
./script/lint        # format and auto-fix all file types
./script/develop     # run Home Assistant with the integration loaded, debug logging on
./script/markdown    # format + lint Markdown (prettier + markdownlint)
./script/hassfest    # Home Assistant manifest validation

./script/setup/setup # DevContainer bootstrap — note: script/setup is a DIRECTORY, not a script
```

Underlying tools, if you need them directly:

```bash
uv sync --extra dev                           # create/refresh the venv
uv run pytest tests/ -k "test_name"           # single test
uv run pytest tests/ --cov --cov-report=term-missing
uv run ruff format . && uv run ruff check . --fix
uv run pyright custom_components/             # type checking — pyright, NOT mypy
```

`pyproject.toml` contains a `[tool.mypy]` block, but nothing runs it; `script/type-check` uses
pyright and `pyrightconfig.json` is the live config.

## Architecture

### Communication

**CoAP** (`philips-airctrl`, no cloud, no polling):

- `CoAPClient` handles encryption, the sync handshake, and observe
- Coordinator consumes the `observe_status()` async iterator for real-time updates
- A watchdog detects missed pushes and drives an exponential-backoff reconnect
- Some firmware (CX7550, `AWS_Philips_AIR_Combo`) never answers a status read and only pushes on
  a real state change; those models declare a `status_nudge` and the coordinator writes a
  transient value to force the first push

**Legacy HTTP** (no cloud, polls on `HTTP_POLL_INTERVAL`):

- Diffie-Hellman exchange against `/di/v1/products/0/security` yields an AES-128-CBC session key
  (all-zero IV); every request and response body is encrypted
- Device state is spread across `/air`, `/fltsts`, `/firmware`, `/wifi` and the _unencrypted_
  `/upnp/description.xml`. `HTTPClient.get_status()` merges them and overlays the identity fields
  (`DeviceId`, `modelid`, `name`, `swversion`, `type`) that the CoAP status carries inline but
  the HTTP status does not — so the result has the same shape and the entity platforms need no
  HTTP-specific handling
- No observe, no watchdog, no nudge; `DataUpdateCoordinator` handles retries
- The device silently invalidates the session key; the only recovery is redoing the handshake, so
  reads and writes retry once with a fresh key (writes re-encrypt the body)

### Key files

| File               | Role                                                                                                      |
| ------------------ | --------------------------------------------------------------------------------------------------------- |
| `__init__.py`      | Setup, protocol selection, platform forwarding; `PhilipsAirPurifierConfigEntry` type                      |
| `coordinator.py`   | `PhilipsAirPurifierCoordinator` — CoAP observe or HTTP polling                                            |
| `client.py`        | Transport dispatch (`async_create_client`, `async_fetch_status`); CoAP-only nudge and device-info helpers |
| `http_client.py`   | Legacy `/di/v1` transport: crypto, resource merge, identity synthesis                                     |
| `config_flow.py`   | DHCP + manual setup, model matching, transport detection                                                  |
| `device_models.py` | 62 `DeviceModelConfig` entries                                                                            |
| `const.py`         | `PhilipsApi` field names for 3 generations, entity descriptions, `Protocol`, `CONF_*`                     |
| `model.py`         | `DeviceInformation`, `ApiGeneration`, `DeviceModelConfig`                                                 |
| `entity.py`        | `PhilipsAirPurifierEntity` base (CoordinatorEntity)                                                       |
| `repairs.py`       | Repair flows; `_transport_kwargs()` picks the right transport per entry                                   |

Platforms: binary_sensor, climate, event, fan, humidifier, light, number, select, sensor, switch.

### Device model system

`device_models.py` maps each model to a `DeviceModelConfig` dataclass — capabilities, presets,
speeds and available entities are **data, not a class hierarchy**. Lookup order: exact model id
→ firmware-qualified name (`AC0850/11 AWS_Philips_AIR`) → six-character family (`AC2889`).

Entities are created purely by **key presence** in `coordinator.data` (see `sensor.py`), which is
why merging the HTTP resources into a CoAP-shaped dict was sufficient to support HTTP with zero
platform changes.

Three generations, different key formats:

- Gen1: `pwr`, `mode`, `om`
- Gen2: `D01-XX`, `D03-XX`
- Gen3: `D01SXX`, `D03XXX`

### Config entry pattern

`entry.runtime_data` holds the coordinator, typed via `PhilipsAirPurifierConfigEntry`. Entity
platforms read the coordinator straight off the config entry.

## Upstream library rule

**All CoAP communication must go through `philips-airctrl`** — no raw sockets, no alternate CoAP
clients, no hand-rolled CoAP encryption. If a CoAP capability is missing, open an upstream issue
rather than working around it.

**The one exception is `http_client.py`.** `philips-airctrl` has no HTTP transport, so there is
nothing upstream to call. Do not put HTTP protocol code anywhere else, and do not route CoAP
through it. Full text: `.github/instructions/upstream_library.instructions.md`.

## Testing

- `pyproject.toml` sets `fail_under = 100`. **New code needs full coverage or CI fails.**
- `tests/conftest.py` — `mock_coap_client`, `mock_coap_client_config_flow`, `init_integration`
- `tests/const.py` — `MOCK_STATUS_GEN1` (CoAP) and the `MOCK_HTTP_*` / `MOCK_STATUS_HTTP`
  fixtures, whose shapes are taken from a real AC2889 but with **synthetic identifiers** (public
  repo — never commit a real MAC, device id or SSID)
- `tests/test_http_client.py` drives a `FakeDevice` performing the **real** DH exchange and AES,
  so the wire format is genuinely exercised. The `"AA"` two-byte body prefix is the easy thing to
  break.
- `tests/test_config_flow.py` has an autouse `_no_real_http_fallback` fixture. Every CoAP-failure
  test now reaches the HTTP probe; without it they open real sockets, surfacing as _teardown_
  errors rather than failures.

**Mocked-green does not mean working.** `async_fetch_device_info` bounded only client creation,
not the read, and hung forever against a real non-CoAP host while the suite stayed green, because
the suite mocks that helper. Verify transport changes against hardware.

## Quality scale target

Targeting **platinum** per Home Assistant integration standards: 100% coverage, strict pyright,
full quality-scale compliance. Progress is tracked in `quality_scale.yaml`.

## Conventions

- **Python 3.14+** (`requires-python = ">=3.14.2"`, ruff `target-version = "py314"`)
- **Line length 120** (ruff)
- `uv` for dependency management; pre-commit runs ruff, codespell, yamllint and prettier
- Async throughout — all device communication is async
- Platforms follow `async_setup_entry()` → build entities → `async_add_entities()`
- Translations in `translations/`: bg, de, en, nl, ro, sk
- User-visible strings live in `strings.json` and must be mirrored into the translations

## Known drift and rough edges

- `AGENTS.md` refers to a "CRITICAL: Upstream Library Rules" section it does not contain, and
  both it and older docs give the domain as `philips_airpurifier_coap`.
- `coordinator.py` guards `self.data is not None` twice with a pyright suppression: Home
  Assistant types `DataUpdateCoordinator.data` as non-Optional, but it genuinely is `None` before
  the first refresh, so those guards are load-bearing — do not "simplify" them away.
- `.ai-scratch/` holds working notes. Only `.gitkeep` is tracked and the directory is
  **not** gitignored, so anything left there ships if you `git add -A`.
- `script/check` stops at the shell step if `shellcheck` is not installed.
- **`./script/develop` can break `cffi` in the venv.** Home Assistant installs integration
  requirements into the same venv while booting, and that can leave the `cffi` package and the
  compiled `_cffi_backend` extension on different versions. `cryptography` then fails to import,
  which fails `config_flow.py`, and Home Assistant reports **`Invalid handler specified`** when
  you try to add the integration — a symptom that looks nothing like the cause. Confirm with
  `python -c "import cffi, _cffi_backend; print(cffi.__version__, _cffi_backend.__version__)"`
  and fix with `uv pip install --reinstall cffi`.
- **Filter capacity is not universal.** CoAP devices report `flttotal*`; the legacy HTTP API
  does not, and no endpoint on an AC2889/10 (firmware 14) exposes it. `resolve_filter_capacity()`
  in `helpers.py` is the single place that resolves this — device value first, then the nominal
  lifetime tables in `const.py`. Those nominal hours come from Philips' published replacement
  intervals and are **not** verified against hardware; correct them there if a device disagrees.
