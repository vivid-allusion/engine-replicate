"""N-in-flight execution for the gold engine.

`run_batch` caps in-flight jobs structurally: ThreadPoolExecutor's
max_workers equals the profile's `concurrency`, so more than N jobs can
never run at once. Per-item behaviour reuses the engine's existing
machinery verbatim (`_build_replicate_input`, `_request_with_retry`,
`_save_results`, error classification) — the same retry, backoff and
circuit-breaker semantics as the sequential loop. Results come back in
input order; a failing item never disturbs jobs still in flight; once a
fatal error or the breaker ends the batch, no new submissions start and
in-flight jobs run to completion.
"""

import concurrent.futures
import threading
import time
from datetime import datetime

from .datatypes import OutputFile


class BatchState:
    def __init__(self, circuit_max: int):
        self.circuit_max = circuit_max
        self.stop = False
        self.consecutive_conflicts = 0
        self._lock = threading.Lock()

    def note_success(self):
        with self._lock:
            if not self.stop:
                self.consecutive_conflicts = 0

    def note_rate_limited(self) -> bool:
        with self._lock:
            self.consecutive_conflicts += 1
            return self.consecutive_conflicts >= self.circuit_max


def run_item(engine, client, item, idx, total, state) -> list[OutputFile]:
    stem = item.path.stem
    current = idx + 1
    prefix = f"[{current}/{total}]"
    media_type = engine._profile.get("media_type", "")
    rel_dir = str(item.metadata.get("relative_dir", "") or "")
    dest_dir = engine._output_dir / rel_dir if rel_dir else engine._output_dir
    ts = datetime.now().strftime("%y%m%d_%H%M%S")
    ext = engine._default_extension(media_type)
    expected = dest_dir / f"{ts}-{stem}-{idx}{ext}"

    engine._emit(f"{prefix} 📡 Calling {engine.PROVIDER_NAME} API...")
    prompt = f"{engine._prefix}{item.prompt}{engine._suffix}".strip()
    if not prompt:
        return [
            OutputFile(
                source_path=item.path,
                status="error",
                error_msg="Empty prompt after applying prefix/suffix",
                media_type=media_type,
                expected_path=expected,
            )
        ]

    engine._emit(f"{prefix} 📝 Markdown: {item.path.name}")

    provider_input: dict | None = None
    try:
        provider_input = engine._build_replicate_input(
            dict(engine._profile.get("parameters", {})), prompt, item, media_type
        )
        raw_output = engine._request_with_retry(
            client, engine._profile["endpoint"], provider_input, prefix
        )
        engine._emit(f"{prefix} ✅ Response received")
        engine._emit(f"{prefix} 📦 Payload: {provider_input}")

        saved = engine._save_results(
            raw_output,
            media_type,
            stem,
            idx,
            prefix,
            current,
            total,
            rel_dir,
            provider_input,
        )
        if not saved:
            return [
                OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg="No output returned from replicate",
                    media_type=media_type,
                    expected_path=expected,
                )
            ]
        state.note_success()
        return [
            OutputFile(
                source_path=item.path,
                path=saved_path,
                status="ok",
                media_type=media_type,
                metadata={"api_payload": provider_input},
            )
            for saved_path in saved
        ]
    except Exception as exc:
        engine._emit(
            f"{prefix} Error: {exc}",
            level="error",
            current=current,
            total=total,
            api_payload=provider_input,
        )
        output = OutputFile(
            source_path=item.path,
            status="error",
            error_msg=str(exc),
            media_type=media_type,
            expected_path=expected,
        )
        if engine._is_fatal(exc):
            engine._emit(
                f"{prefix} 🛑 Fatal provider error — aborting batch. "
                f"Check payment, quota, or API key. ({exc})",
                level="error",
            )
            state.stop = True
            return [output]
        if engine._is_rate_limited(exc):
            tripped = state.note_rate_limited()
            if tripped:
                engine._emit(
                    f"{prefix} 🛑 Circuit breaker tripped after "
                    f"{state.consecutive_conflicts} consecutive rate-limited "
                    f"failures — aborting batch.",
                    level="error",
                )
                state.stop = True
                return [output]
        else:
            state.note_success()
        return [output]


def run_batch(engine, inputs) -> list[OutputFile]:
    import replicate

    client = replicate.Client(api_token=engine._resolve_api_key(), timeout=600.0)
    engine._output_dir.mkdir(parents=True, exist_ok=True)

    total = len(inputs)
    state = BatchState(int(engine._profile.get("circuit_breaker_max", 3)))
    results: list[list[OutputFile] | None] = [None] * total

    delay = float(engine._profile.get("delay_between_requests", 0.0))

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, engine._concurrency)
    ) as pool:
        futures = []
        for idx, item in enumerate(inputs):
            if state.stop:
                break
            if idx > 0 and delay > 0:
                time.sleep(delay)
            futures.append((idx, pool.submit(run_item, engine, client, item, idx, total, state)))
        for idx, future in futures:
            results[idx] = future.result()

    return [out for item in results if item is not None for out in item]
