# Feature: Granular Progress Emits

## Summary

The engine currently emits only one progress message per file (`"Sending Markdown files to AI model (2/5)..."`), then blocks silently through `client.run()` and `urllib.request.urlretrieve()`. Add `_emit()` calls around each step so the Vehicle can display real-time per-image progress.

## Current State

`engine.py:44-45` — the only emit:
```python
self._emit(f"Sending Markdown files to AI model ({idx + 1}/{total})...")
```

After that, `client.run()` blocks (no progress), then `_save_results` downloads via `urlretrieve` (no progress).

## Tasks

### T1. Add progress emits in `run()`

File: `engine_replicate/engine.py:30-93`

Add `_emit()` calls around key steps:

```python
def run(self, inputs: list[InputFile]) -> list[OutputFile]:
    ...
    for idx, item in enumerate(inputs):
        stem = item.path.stem
        prefix = f"[{idx + 1}/{total}]"

        self._emit(f"{prefix} Calling Replicate API...")
        prompt = ...
        
        try:
            replicate_input = self._build_replicate_input(...)
            raw_output = client.run(endpoint, input=replicate_input)
            self._emit(f"{prefix} Response received")

            saved = self._save_results(raw_output, media_type, stem, idx, prefix)
            ...
        except Exception as exc:
            self._emit(f"{prefix} Error: {exc}", level="error")
            ...
```

### T2. Add progress emits in `_save_results()`

File: `engine_replicate/engine.py:129-151`

Add `_emit()` calls for download and save steps:

```python
def _save_results(self, raw_output, media_type, stem, idx, prefix=""):
    ...
    for i, item in enumerate(raw_output):
        if isinstance(item, str):
            self._emit(f"{prefix} Downloading...")
            ext = self._infer_extension(item, media_type)
            suffix = "" if len(raw_output) == 1 else f"_{i}"
            dest = self._output_dir / f"{stem}-{idx}-{ts}{suffix}{ext}"
            urllib.request.urlretrieve(item, dest)
            self._emit(f"{prefix} Saved: {dest.name}")
            saved.append(dest)
        elif hasattr(item, "read"):
            self._emit(f"{prefix} Saving file stream...")
            ...
            self._emit(f"{prefix} Saved: {dest.name}")
            saved.append(dest)
    return saved
```

### T3. Use structured `ProgressEvent` (optional polish)

File: `engine_replicate/datatypes.py:24-28`

The `ProgressEvent` dataclass already exists with `message` and `level` fields. If the Vehicle contract switches from `Callable[[str], None]` to `Callable[[ProgressEvent], None]`, update `_emit()` to pass the object:

```python
def _emit(self, message: str, level: str = "info"):
    if self._on_progress:
        self._on_progress(ProgressEvent(message=message, level=level))
```

## Expected Output (per file)

```
[1/3] Calling Replicate API...
[1/3] Response received
[1/3] Downloading...
[1/3] Saved: scene-01-250806_120001.png
```

On error:
```
[2/3] Calling Replicate API...
[2/3] Error: NSFW content detected
```

## Notes

- The `prefix` variable carries `[i/N]` through all emits for a given file — the Vehicle no longer adds framing
- The engine owns all emoji/icons and formatting — the Vehicle just prints the message string
- Keep emits async-safe: `_emit()` must not be called from threads
