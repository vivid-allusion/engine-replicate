# engine-replicate

Engine wrapper for the [Replicate](https://replicate.com) API.

Part of the [studiolot](https://github.com/vivid-allusion/studiolot) Vehicle /
Engine / SDK architecture. This repo wraps the `replicate` Python SDK behind a
uniform interface that Vehicles (Frame Composer, Motion Composer, etc.) call.
The Vehicle never imports `replicate` directly — it calls this Engine.

See `docs/architecture/ENGINE_CONTRACT.md` in the studiolot repo for the full
interface contract.

## Status

**Pre-release — not yet functional.** This README is a placeholder for the
implementation target defined in the studiolot ENGINE_CONTRACT.

## When implemented

```python
from engine_replicate import Engine

engine = Engine(profile=profile_dict, output_dir="/tmp/out")
results = engine.run(input_files)
```

## API key

Set `REPLICATE_API_TOKEN` in your environment or a `.env` file.

Get a token at: https://replicate.com/account/api-tokens

## Endpoint models

Endpoint TOML definitions live in `endpoints/`. When a new Replicate-hosted
model drops (e.g., Nano Banana 3), add a TOML file here and send a PR.
`git pull` the Engine repo → new model appears as an option in studiolot's
ActionWizard.

## Repo structure

See `docs/architecture/ENGINE_CONTRACT.md` §6a in studiolot.
