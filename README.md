# engine-replicate

Engine wrapper for the [Replicate](https://replicate.com) API.

Part of the [studiolot](https://github.com/vivid-allusion/studiolot) Vehicle /
Engine / SDK architecture. This repo wraps the `replicate` Python SDK behind a
uniform interface that Vehicles (Frame Composer, Motion Conductor, etc.) call.
The Vehicle never imports `replicate` directly — it calls this Engine.

See `docs/architecture/ENGINE_CONTRACT.md` in the studiolot repo for the full
interface contract.

## Quick start

```bash
git clone https://github.com/vivid-allusion/engine-replicate.git
cd engine-replicate
pip install -r requirements.txt
```

Or install via pip:

```bash
pip install engine-replicate
```

## Usage

```python
from engine_replicate import Engine, InputFile
from pathlib import Path

profile = {
    "platform": "replicate",
    "media_type": "image",
    "endpoint": "google/nano-banana-pro",
    "parameters": {
        "aspect_ratio": "16:9",
        "resolution": "2K",
        "output_format": "png",
        "num_images": 1,
    },
    "prompt_prefix": "",
    "prompt_suffix": "",
}

engine = Engine(profile=profile, output_dir="/tmp/out")

inputs = [
    InputFile(
        path=Path("bullet-001.md"),
        prompt="a cinematic shot of a city at night",
        reference_urls=["https://example.com/ref.jpg"],
    ),
]

results = engine.run(inputs)
for r in results:
    print(r.status, r.path)
```

## API key

Set `REPLICATE_API_TOKEN` in your environment or a `.env` file:

```bash
export REPLICATE_API_TOKEN=r8_...
```

Get a token at: https://replicate.com/account/api-tokens

## Endpoint models

Endpoint TOML definitions live in `endpoints/`. Each file defines a model's
valid parameter ranges (aspect_ratio, resolution, etc.) — the "bounds" that
the ActionWizard reads to build select menus. When a new Replicate-hosted
model drops, add a TOML file here and send a PR. `git pull` the Engine repo
→ new model appears as an option in studiolot.

## Repo structure

```
engine-replicate/
├── __init__.py          ← re-exports Engine, InputFile, OutputFile, etc.
├── engine.py            ← Engine class — all Replicate SDK calls live here
├── datatypes.py         ← InputFile, OutputFile, ProgressEvent, EngineError
├── metadata.py          ← zero-dependency constants (studiolot imports this)
├── endpoints/
│   ├── IMG-Models/      ← image model TOML definitions (8 models)
│   ├── VID-Models/      ← video model TOML definitions (10 models)
│   ├── TXT-Models/      ← future: text models (engine-openrouter)
│   └── Vision-Models/   ← future: vision models (engine-openrouter)
├── requirements.txt     ← replicate>=1.0
├── .env.example         ← REPLICATE_API_TOKEN template
├── pyproject.toml       ← pip install engine-replicate
└── README.md
```

See `docs/architecture/ENGINE_CONTRACT.md` §6a in studiolot.
