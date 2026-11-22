## Architecture: Engine SDK Wrapper

This repo is an **Engine** in the Helmer ecosystem. It wraps the
**Replicate** API and exposes a uniform interface that Generators call.

### The layers

```
Helmer → Transceiver (helmer-transceiver) → Generator (Image or Video Generator) → **Replicate Engine** → Provider API
```

Generators like Image Generator and Video Generator are SDK-agnostic. The
Transceiver discovers this Engine and loads it for them; they call
`engine.run(inputs)`. This Engine handles all **Replicate**-specific logic.

### Contract

- **`Engine.__init__` is PURE.** No network I/O, no SDK imports in the
  constructor. It only stores: profile dict, output_dir, api_key,
  on_progress callback.
- **`Engine.run(inputs: list[InputFile]) -> list[OutputFile]`** is the ONLY
  entry point Generators call. Returns ALL results — success and failure —
  as OutputFile objects. Never raises for per-Markdown-file failures.
- **Error results carry `expected_path`**: the destination filename is
  precomputed BEFORE the API call (profile `output_format`/media default
  extension) so Generators can write error placeholders at the exact name.
- **Every per-item failure writes a verbose `<base>-<idx>_ERROR.log`** beside
  the would-be output (provider exception, empty-prompt skip, "no output
  returned", save/download failure — independent of any logging flag); the
  error result names the written file as `metadata["error_log"]`.
- **`EngineError`** is for unrecoverable pre-flight failures only: missing
  API key, invalid profile, provider auth rejection.
- **`datatypes.py`** defines InputFile, OutputFile, ProgressEvent,
  EngineError. Single source of truth per Engine.
- **`metadata.py`** is zero-dependency (stdlib only). Helmer imports this
  (NOT engine.py) to read PROVIDER_NAME for the provider name. This avoids
  pulling in the **Replicate** SDK transitively.
- **`endpoints/`** TOMLs are the canonical model catalog. One file per model,
  defining valid parameter ranges.
- **`profiles/standby/`** contains publishable YAML profiles, organized by
  media category (`IMG/` and `VID/`) mirroring `endpoints/`. When installed,
  these are seeded into Generator repos' `USER-FILES/02.STANDBY/`, filtered by
  the loading Generator's declared media type (Image Generator → IMG,
  Video Generator → VID).

## NON-NEGOTIABLE: Endpoint TOMLs Mirror the Live Schema

**The endpoint TOML catalog is NOT a "curated subset".** Every input the
model accepts server-side MUST be declared in the TOML — no exceptions, no
omissions. A missing param is a **bug**, not an editorial choice. This rule
exists because a missing param silently produces wrong payloads: users pick
a profile that cannot express the model's actual inputs (e.g. the
2026-09-05 p-video incident: `no_op`, `save_audio`, `disable_safety_filter`,
`last_frame_image` were absent while the live schema accepted all 14
inputs).

The rules, hard:

1. **Every server-side Input property** (except `prompt`) is declared in
   the TOML — as the `[general] image_url_param` (primary media input), a
   `[general] slots` entry (every other uri/array-of-uri input), or a
   `[params.*]` entry (every scalar).
2. **Nothing undeclared is invented.** No `[params.*]` key may exist that
   is not in the live schema. No slot may point at a key the server does
   not accept. (Two were removed in the 2026-09-05 sweep: seedance-lite/pro
   declared `reference_videos`/`reference_audios` which do not exist on
   those models.)
3. **Options and ranges mirror the server exactly.** `select` options must
   equal the server enum (no dropped values, no invented values — e.g. flux
   declared `21:9` which the schema rejects). `integer`/`number` min/max
   must equal the server's minimum/maximum (e.g. p-video duration max was
   "10s · Max" while the server accepts 1–20). Defaults equal the server
   default wherever the server declares one.
4. **Verified by `scripts/check_schema_sync.py`.** It fetches the live
   openapi schema for every endpoint and diffs it against the TOML catalog.
   Run it before committing any TOML change and after any model version
   bump:

   ```
   REPLICATE_API_TOKEN=r8_... python scripts/check_schema_sync.py
   ```

   Exit code 1 = drift = **do not merge**. A clean run is a precondition
   for shipping endpoint TOMLs. The offline logic is unit-tested
   (`tests/test_schema_sync.py`) so regressions in the checker itself are
   caught without network access.

### Provider details

| Field | Value |
|-------|-------|
| **Platform** | `replicate` |
| **API key env var** | `REPLICATE_API_TOKEN` |
| **Key pattern** | `^r8_` |
| **Homepage** | https://replicate.com |

### Engine discovery

The Transceiver (`hhc/engines.py`, `hhc/engine_registry.py` in helmer-transceiver) finds this Engine:
- Repo directory: `engine-replicate` 
- Python package: `engine_replicate`
- The loader handles the hyphen→underscore mapping
- Discovery order: the project's `.00_TOOLS/GENERATORS/ENGINES/` first (also `~/.config/helmer-transceiver/engines`), pip-installed package as fallback

### Source File Map

| File | Purpose |
|------|---------|
| `engine_replicate/engine.py` | Engine implementation — all SDK calls live here |
| `engine_replicate/datatypes.py` | InputFile, OutputFile, ProgressEvent, EngineError |
| `engine_replicate/metadata.py` | Zero-dependency identity constants (Helmer reads this) |
| `engine_replicate/__init__.py` | Re-exports Engine + all datatypes + `list_standby_profiles(media_type)` shelf selector |
| `engine_replicate/endpoints/` | TOML model catalog (IMG/VID/TXT/Vision) |
| `engine_replicate/profiles/standby/` | Publishable YAML profiles by category (IMG/VID) |
| `scripts/check_schema_sync.py` | Live schema ↔ TOML drift checker (NON-NEGOTIABLE gate) |
| `tests/test_engine.py` | Unit tests |
| `tests/test_schema_sync.py` | Offline checker-logic tests |
| `pyproject.toml` | Package metadata, pip-installable |
| `requirements.txt` | Provider SDK + dependencies |

### Reference

Loader-side contract: `docs/architecture/ENGINE_CONTRACT.md` in helmer-transceiver (this repo's own engine rules above are gold).
Session history: see [docs/HISTORY.md](docs/HISTORY.md).
