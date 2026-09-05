## Architecture: Engine SDK Wrapper

This repo is an **Engine** in the studiolot ecosystem. It wraps the
**Replicate** API and exposes a uniform interface that Vehicles call.

### The layers

```
studiolot (TUI) → Vehicle (script) → **Replicate Engine** → Provider API
```

Vehicles like Frame Composer and Motion Conductor are SDK-agnostic. They
discover this Engine, load it via `engine_loader.py`, and call
`engine.run(inputs)`. This Engine handles all **Replicate**-specific logic.

### Contract

- **`Engine.__init__` is PURE.** No network I/O, no SDK imports in the
  constructor. It only stores: profile dict, output_dir, api_key,
  on_progress callback.
- **`Engine.run(inputs: list[InputFile]) -> list[OutputFile]`** is the ONLY
  entry point Vehicles call. Returns ALL results — success and failure —
  as OutputFile objects. Never raises for per-bullet failures.
- **Error results carry `expected_path`**: the destination filename is
  precomputed BEFORE the API call (profile `output_format`/media default
  extension) so Vehicles can write error placeholders at the exact name.
- **`EngineError`** is for unrecoverable pre-flight failures only: missing
  API key, invalid profile, provider auth rejection.
- **`datatypes.py`** defines InputFile, OutputFile, ProgressEvent,
  EngineError. Single source of truth per Engine.
- **`metadata.py`** is zero-dependency (stdlib only). studiolot imports this
  (NOT engine.py) to read PROVIDER_NAME for the TUI label. This avoids
  pulling in the **Replicate** SDK transitively.
- **`endpoints/`** TOMLs are the canonical model catalog. One file per model,
  defining valid parameter ranges.
- **`profiles/standby/`** contains publishable YAML profiles, organized by
  media category (`IMG/` and `VID/`) mirroring `endpoints/`. When installed,
  these are seeded into Vehicle repos' `USER-FILES/02.STANDBY/`, filtered by
  the loading Vehicle's declared media type (Frame Composer → IMG, Motion
  Conductor → VID).

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

Vehicles find this Engine via `engine_loader.py`:
- Repo directory: `engine-replicate` 
- Python package: `engine_replicate`
- `engine_loader.py` handles the hyphen→underscore mapping
- Discovery order: local clone in `00_APPLICATIONS/ENGINES/` first, pip-installed package as fallback

### Source File Map

| File | Purpose |
|------|---------|
| `engine_replicate/engine.py` | Engine implementation — all SDK calls live here |
| `engine_replicate/datatypes.py` | InputFile, OutputFile, ProgressEvent, EngineError |
| `engine_replicate/metadata.py` | Zero-dependency identity constants (studiolot reads this) |
| `engine_replicate/__init__.py` | Re-exports Engine + all datatypes + `list_standby_profiles(media_type)` shelf selector |
| `engine_replicate/endpoints/` | TOML model catalog (IMG/VID/TXT/Vision) |
| `engine_replicate/profiles/standby/` | Publishable YAML profiles by category (IMG/VID) |
| `scripts/check_schema_sync.py` | Live schema ↔ TOML drift checker (NON-NEGOTIABLE gate) |
| `tests/test_engine.py` | Unit tests |
| `tests/test_schema_sync.py` | Offline checker-logic tests |
| `pyproject.toml` | Package metadata, pip-installable |
| `requirements.txt` | Provider SDK + dependencies |

### Reference

Full contract: `~/Nextcloud/00-DEVELOPMENT/MISC_DEV_TOOLS/studiolot/docs/architecture/ENGINE_CONTRACT.md`

### Session History

- 2026-09-05 — TOML Catalog Swept to Mirror Live Schemas (21/21 clean)
  - Policy (see NON-NEGOTIABLE section above): endpoint TOMLs must declare
    every server-side input, declare nothing that does not exist server-side,
    and mirror server enums/ranges/defaults exactly. New gate:
    `scripts/check_schema_sync.py` fetches each endpoint's live openapi
    schema and diffs it against the catalog — exit 1 on drift. Offline
    checker logic unit-tested in `tests/test_schema_sync.py` (15 tests).
  - VID fixes: p-video gained `no_op`, `save_audio`, `disable_safety_filter`
    params + `last_frame_image` slot, duration now integer 1–20 (was "max
    10"); grok gained `video` slot + full aspect enum + integer duration
    1–15; kling-2.5-turbo gained `image` (deprecated alias) slot and lost
    invented guidance_scale range; kling-v2.6 lost the nonexistent
    `end_image` slot; kling-v3 gained `multi_prompt`, integer duration
    3–15, generate_audio default aligned to false; seedance-2.0/lite/pro
    gained `last_frame_image` slot, lite/pro lost nonexistent
    `reference_videos`/`reference_audios` slots, durations converted to
    integer ranges (2–12 / 4–12 / -1–15), resolution/aspect enums
    completed (4k, 21:9, 9:21, 4:3, 3:4, 1:1...); veo-3.1 gained
    `last_frame` + `reference_images` slots; veo-3 duration default
    aligned to 8s; wan-2.5-i2v gained `audio` slot, lost nonexistent
    `generate_audio` param.
  - IMG fixes: all 10 IMG TOMLs gained `[general] image_url_param`
    (`input_images` flux, `image_input` nano-banana/seedream, `images`
    wan-2.7-image) — previously undeclared, so primary-image routing fell
    back to the wrong key; flux dropped the invented `21:9` aspect option;
    nano-banana aspect/output_format defaults aligned to server
    (`match_input_image`, `jpg`); seedream-4 gained
    `sequential_image_generation`; seedream-5-lite lost nonexistent
    `return_byteplus_urls`; wan-2.7-image num_outputs corrected 12→4.
  - The 11 VID standby YAMLs regenerated to mirror the fixed TOML defaults.
    Final gate run: 21/21 endpoints OK, 0 drifted. 78 engine tests green.
- 2026-09-05 — Scalar param coercion against live schema (fps/duration 422)
  - Live p-video run hit 422: `input.duration`/`input.fps` expected integer,
    given string — the VID YAMLs carry TOML select defaults as quoted
    strings (`duration: "5"`, `fps: "24"`).
  - `_build_replicate_input()` now coerces every parameter against the
    model openapi schema via new `_coerce_param_value()`: integer-typed
    params get `int(...)` from numeric strings, number → float, boolean →
    bool; non-convertible values (token durations like `auto`) and
    unknown-schema runs pass through verbatim. Metadata duration override
    is coerced the same way.
  - `_input_props()` now resolves `$ref`/`allOf` fragments into concrete
    type dicts (new `_resolve_schema_refs()`) — `fps` is declared as
    `{"allOf": [{"$ref": "#/components/schemas/fps"}]}` on the live
    p-video schema and previously evaded type detection.
  - Verified against the live prunaai/p-video schema: duration/fps coerce
    to int 5 / 24 (fps within enum [24, 48]); 63 green.
- 2026-09-05 — Vehicle-aware standby shelves (MC/FC seeding split)
  - `profiles/standby/` reorganized into `IMG/` (30 existing image YAMLs,
    moved) and `VID/` (11 new video YAMLs authored from `endpoints/VID-Models/`
    defaults: kling-2.5-turbo-pro, kling-v2.6, kling-v3-video, seedance-2.0,
    seedance-lite, seedance-pro, wan-2.5-i2v, p-video, veo-3, veo-3.1,
    grok-imagine-video).
  - `list_standby_profiles(media_type=None)` selects a shelf (`IMG`/`VID`,
    case-insensitive; `None` → every shelf, legacy behavior). Vehicles declare
    their media type (MC → VID, FC → IMG) through the vendored
    `copy_standby_profiles(media_type=...)`; engines without the parameter
    fall back to the no-arg call.
  - Video YAML shape: `platform`, `endpoint`, `media_type: video`, top-level
    `image_url_param`/`duration_type`/`duration_param_name`/`slots`,
    `parameters` carrying TOML defaults, `pricing.cost_per_second`.
  - 4 new shelf-selector tests; 53 green.
- 2026-09-05 — Live-run 422 fix: `_build_replicate_input` now shapes media
  values against the model's openapi schema — string params (start_image,
  end_image, image, last_frame) get a single URL, array params
  (reference_*) keep the list. `_input_props()` fetches the schema once per
  run via an authenticated `replicate.Client` (unauthenticated `models.get`
  401s); unknown schemas degrade by URL count. Tests updated to live-schema
  shapes; 52 green, black `--line-length 100` clean.
- 2026-08-05 — Created AGENTS.md, .env.example, .gitignore
