# History

### Session History

- 2026-10-15 — Schema-less reference slots stay arrays; client timeouts retryable
  - `_coerce_media_value()` keeps a single URL as a list for profile-declared
    reference-list slots (`reference_image_urls`, `reference_images`, …) even
    when the endpoint exposes no openapi schema (`minimax/h3` 422: expected
    array, given string); single-image slots (`first_frame_image`,
    `last_frame_image`) keep the string fallback.
  - `_classify_error()` treats client timeouts (httpx `TimeoutException`,
    builtin `TimeoutError`) as `retryable` — cold-start read timeouts retry
    instead of failing the item; the retry line says "request timeout".
  - Tests: schema-less coercion + timeout classification cases; 110 green.

- 2026-10-15 — Per-item error logs on every failure (`<base>-<idx>_ERROR.log`)
  - Every failure branch of `Engine.run()` — provider exception, empty-prompt
    skip, "no output returned", save/download failure — now writes a verbose
    log to the item's destination dir (created if missing), named with the
    same `_output_stem` base as the would-be output and the 0-based idx. The
    error result carries `metadata["error_log"]`.
  - Content: title / `when:` ISO local / `item:` i of n / source / dest dir /
    expected / platform / endpoint / profile JSON / duration raw+effective+fps
    / media (primary + every slot) / exact final prompt / `error stage:`
    (`build_input` | `provider_call` | `save` | `no_output` | `empty_prompt`) /
    `error:` type+message / full traceback / request payload JSON / note.
    Key-like values (`r8_…`, `api_key`/`token`/`secret` fields) are masked;
    no environment dumps. Writing is best-effort — it never masks the real
    failure.
  - 7 new failure-path tests (`TestErrorLog`); 104 green.
- 2026-09-05 — Error events carry the API payload
  - `Engine.run()` declares `replicate_input` before the try block and
    attaches it to error progress events (`api_payload=`), so a failed API
    call reports the exact payload that was rejected — generators log it into
    their per-file run logs. 79 green.
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
- 2026-09-05 — Generator-aware standby shelves (MC/FC seeding split)
  - `profiles/standby/` reorganized into `IMG/` (30 existing image YAMLs,
    moved) and `VID/` (11 new video YAMLs authored from `endpoints/VID-Models/`
    defaults: kling-2.5-turbo-pro, kling-v2.6, kling-v3-video, seedance-2.0,
    seedance-lite, seedance-pro, wan-2.5-i2v, p-video, veo-3, veo-3.1,
    grok-imagine-video).
  - `list_standby_profiles(media_type=None)` selects a shelf (`IMG`/`VID`,
    case-insensitive; `None` → every shelf, legacy behavior). Generators declare
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
